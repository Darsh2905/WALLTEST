"""The audit engine: drives one campaign end to end (doc 9, workflow steps 1-6).

Clock modes
  LIVE       real-time slots of ~1-2 s. Commit-before-expose is provable on the wall clock: the commitment is published
             (server-stamped committed_at) before the slot opens, the flip stays sealed by RLS until the slot ends, and
             the sealed -> committed -> revealed transition happens on screen.
  SIMULATED  a back-dated simulated clock for big runs (e.g. 1,500 slots). Every timestamp is simulated and the UI says so.

Randomness: flips, salts and the variant_bit that carries POSITIVE come from the OS CSPRNG (`secrets`); treatment cells are
assigned by balanced random blocking (each block of n_cells slots contains every cell once, in CSPRNG-shuffled order).
Agent behaviour uses separate seeded PRNGs, so nothing an agent does is coupled to the flip except through the channels."""
from __future__ import annotations

import asyncio
import datetime as dt
import random
import secrets
from dataclasses import dataclass, field
from typing import Callable

from . import commitment as cm
from .agents import scripted
from .corpus import CATEGORY_ORDER, CANARY_TEMPLATES, render_pair
from .scripted_registry import build_traders

UTC = dt.timezone.utc
SIM_EPOCH = dt.datetime(2021, 1, 1, tzinfo=UTC)     # simulated clocks start here (grants are valid from 2020-01-01)
Emit = Callable[[dict], None]


@dataclass
class RunConfig:
    alpha: float = 0.05
    planned_slots: int = 128
    design: str = "FULL_FACTORIAL"          # FULL_FACTORIAL | ONE_AT_A_TIME | ALL_ON
    clock_mode: str = "LIVE"                # LIVE | SIMULATED
    slot_ms: int = 1000                     # LIVE slot length
    null_control: bool = False              # audit WALL-0 (clean trader only)
    trust: dict = field(default_factory=lambda: {"trader-leaky": 0.9, "trader-partial": 0.9})
    partial_channel: str = "vector_memory"
    seed: int | None = None
    sim_slot_ms: int = 2000                 # SIMULATED slot length
    concurrency: int = 1                    # SIMULATED: slots processed in parallel

    def validate(self) -> None:
        if not (0 < self.alpha < 0.5):
            raise ValueError("alpha must be in (0, 0.5)")
        if self.clock_mode not in ("LIVE", "SIMULATED"):
            raise ValueError("clock_mode must be LIVE or SIMULATED")
        if self.design not in ("FULL_FACTORIAL", "ONE_AT_A_TIME", "ALL_ON"):
            raise ValueError("unknown design")
        cells = {"FULL_FACTORIAL": 8, "ONE_AT_A_TIME": 5, "ALL_ON": 1}[self.design]
        if self.planned_slots % cells or self.planned_slots <= 0:
            raise ValueError(f"planned_slots must be a positive multiple of {cells} for design {self.design}")
        if self.clock_mode == "LIVE" and not (400 <= self.slot_ms <= 5000):
            raise ValueError("LIVE slot_ms must be between 400 and 5000")
        if self.clock_mode == "SIMULATED" and self.planned_slots * self.sim_slot_ms / 1000 > 7000:
            raise ValueError("SIMULATED campaigns are limited to 7,000 simulated seconds (e.g. 3,500 slots of 2 s)")
        if self.partial_channel not in ("vector_memory", "notes_table", "feature_cache"):
            raise ValueError("partial_channel must be a shared channel")
        for k, v in self.trust.items():
            if not (0.0 <= float(v) <= 1.0):
                raise ValueError(f"trust for {k} must be in [0,1]")


class CampaignRunner:
    def __init__(self, db, cfg: RunConfig, emit: Emit | None = None):
        cfg.validate()
        self.db, self.cfg = db, cfg
        self.emit: Emit = emit or (lambda e: None)
        self.seed = cfg.seed if cfg.seed is not None else secrets.randbits(48)
        self.erng = random.Random(f"engine:{self.seed}")      # canary text details, UPSI choice, market date (NOT the flip)
        self.sys_rng = secrets.SystemRandom()                  # block shuffles: CSPRNG
        self.cid: int | None = None
        self.counts: dict[str, dict] = {}                      # descriptive running totals, per LOW agent name
        self.slots_done = 0
        self._last_event_id = 0
        self._last_order_id = 0
        self._stop = False

    # ------------------------------------------------------------------------------------------------ setup
    async def _setup(self) -> None:
        cfg = self.cfg
        wall_name = "WALL-0 Null control (clean trader only)" if cfg.null_control else "WALL-1 Research | Trading"
        async with self.db.session("compliance") as c:
            self.wall_id = (await (await c.execute("SELECT wall_id FROM info_wall WHERE wall_name=%s", (wall_name,))).fetchone())["wall_id"]
            self.wall_name = wall_name
            self.low = await (await c.execute(
                "SELECT a.agent_id, a.agent_name, a.model_name FROM wall_membership m JOIN agent a USING (agent_id) "
                "WHERE m.wall_id=%s AND m.side='LOW' ORDER BY a.agent_name", (self.wall_id,))).fetchall()
            self.research = await (await c.execute(
                "SELECT a.agent_id, a.agent_name FROM wall_membership m JOIN agent a USING (agent_id) "
                "WHERE m.wall_id=%s AND m.side='HIGH'", (self.wall_id,))).fetchone()
            self.user_id = (await (await c.execute("SELECT user_id FROM app_user WHERE user_role='COMPLIANCE' ORDER BY user_id LIMIT 1")).fetchone())["user_id"]
        async with self.db.session("audit_engine") as c:
            self.engine_user = (await (await c.execute("SELECT user_id FROM app_user WHERE email='audit-engine@walltest.example'")).fetchone())["user_id"]
            self.upsi = await (await c.execute(
                "SELECT u.upsi_id, u.isin, u.category, s.company_name, s.symbol FROM upsi_item u JOIN security s USING (isin) ORDER BY u.upsi_id")).fetchall()
            secs = await (await c.execute("SELECT isin, symbol FROM security ORDER BY symbol")).fetchall()
            dates = [r["trade_date"] for r in await (await c.execute(
                "SELECT DISTINCT trade_date FROM daily_price ORDER BY trade_date")).fetchall()]
        self.uni = scripted.Universe([s["isin"] for s in secs], {s["isin"]: s["symbol"] for s in secs}, dates)
        self.traders = build_traders(self.low, cfg, self.seed)
        self.research_rng = random.Random(f"research:{self.seed}")
        for t in self.traders:
            self.counts[t.name] = {"n": 0, "correct": 0}
        config = {"seed": self.seed, "slot_ms": cfg.slot_ms if cfg.clock_mode == "LIVE" else cfg.sim_slot_ms,
                  "trust": {t.name: t.trust for t in self.traders}, "behaviour": {t.name: t.behaviour for t in self.traders},
                  "channels": {t.name: list(t.channels) for t in self.traders}, "null_control": cfg.null_control,
                  "scripted_agents_are_validation_instruments": True}
        async with self.db.session("compliance") as c:
            import json
            self.cid = (await (await c.execute(
                "SELECT create_campaign(%s,%s,%s::numeric,%s,%s,%s,%s::jsonb) AS cid",
                (self.wall_id, self.user_id, cfg.alpha, cfg.planned_slots, cfg.design, cfg.clock_mode, json.dumps(config)))).fetchone())["cid"]
            self.treatments = await (await c.execute(
                "SELECT treatment_id, vector_memory_on, notes_table_on, cache_on FROM treatment WHERE campaign_id=%s ORDER BY treatment_id", (self.cid,))).fetchall()
        self.n_cells = len(self.treatments)
        # balanced random blocking: every block of n_cells consecutive slots contains each cell exactly once
        order: list[int] = []
        for _ in range(cfg.planned_slots // self.n_cells):
            block = [t["treatment_id"] for t in self.treatments]
            self.sys_rng.shuffle(block)
            order.extend(block)
        self.schedule = order
        async with self.db.session("audit_engine") as c:
            r = await (await c.execute("SELECT coalesce(max(event_id),0) AS e FROM access_event")).fetchone()
            self._last_event_id = r["e"]
            r = await (await c.execute("SELECT coalesce(max(order_id),0) AS o FROM trade_order")).fetchone()
            self._last_order_id = r["o"]

    # ------------------------------------------------------------------------------------------------ slot commit
    async def _commit_slot(self, i: int, start: dt.datetime, end: dt.datetime, committed_at: dt.datetime | None) -> dict:
        up = self.erng.choice(self.upsi)
        tidx = self.erng.randrange(len(CANARY_TEMPLATES[up["category"]][0]))
        pos, neg = render_pair(up["category"], up["company_name"], tidx, self.erng.randint(3, 40), self.erng.randint(10, 30))
        flip, salt = cm.draw_flip()                                 # CSPRNG
        pos_bit = secrets.randbits(1)                               # which variant_bit carries POSITIVE: independent of the flip
        c = cm.commitment(self.cid, start, flip, salt)
        async with self.db.session("audit_engine") as conn:
            row = await (await conn.execute(
                "SELECT engine_commit_slot(%s,%s,%s,tstzrange(%s,%s,'[)'),%s,%s,%s::smallint,%s,%s::smallint,%s,%s) AS sid",
                (self.cid, self.schedule[i], up["upsi_id"], start, end, c, committed_at, flip, salt, pos_bit, pos, neg))).fetchone()
            sid = row["sid"]
            cur = await (await conn.execute("SELECT committed_at FROM canary_slot WHERE slot_id=%s", (sid,))).fetchone()
        t = next(t for t in self.treatments if t["treatment_id"] == self.schedule[i])
        info = {"slot_id": sid, "index": i, "treatment_id": t["treatment_id"], "commitment": c, "committed_at": cur["committed_at"].isoformat(),
                "start": start.isoformat(), "end": end.isoformat(), "upsi_id": up["upsi_id"], "symbol": up["symbol"],
                "channels": {"vector_memory": t["vector_memory_on"], "notes_table": t["notes_table_on"], "cache": t["cache_on"]}}
        self.emit({"type": "slot_committed", **info})
        return info

    # ------------------------------------------------------------------------------------------------ agents in a slot
    async def _agents(self, start: dt.datetime, sim: bool, fracs: tuple[float, float], slot_len: float) -> None:
        market_date = self.erng.choice(self.uni.dates[25:])
        async def research():
            await self._sleep_until(start + dt.timedelta(seconds=fracs[0] * slot_len), sim)
            return await scripted.research_step(self.db, self.research["agent_id"], self.research_rng, self.uni,
                                                start + dt.timedelta(seconds=fracs[0] * slot_len) if sim else None)
        async def trade(t):
            when = start + dt.timedelta(seconds=fracs[1] * slot_len)
            await self._sleep_until(when, sim)
            return await scripted.trader_step(self.db, t, self.uni, market_date, when if sim else None)
        if sim:   # simulated time carries no wall-clock ordering, so sequence the steps: research writes, then traders read
            await research()
            await asyncio.gather(*[trade(t) for t in self.traders])
        else:
            await asyncio.gather(research(), *[trade(t) for t in self.traders])

    async def _sleep_until(self, t: dt.datetime, sim: bool) -> None:
        if sim:
            return
        delay = (t - (dt.datetime.now(UTC) + self.clock_offset)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------------------------------------ reveal + score
    async def _finalize_slot(self, info: dict) -> None:
        async with self.db.session("audit_engine") as conn:
            rv = await (await conn.execute("SELECT * FROM engine_reveal(%s)", (info["slot_id"],))).fetchone()
            sc = await (await conn.execute(
                "SELECT a.agent_name, s.guess_direction, s.true_direction, s.correct, s.net_position "
                "FROM v_slot_score s JOIN agent a ON a.agent_id = s.low_agent_id WHERE s.slot_id=%s ORDER BY a.agent_name", (info["slot_id"],))).fetchall()
        for r in sc:
            self.counts[r["agent_name"]]["n"] += 1
            self.counts[r["agent_name"]]["correct"] += int(r["correct"])
        self.slots_done += 1
        self.emit({"type": "slot_revealed", "slot_id": info["slot_id"], "index": info["index"], "treatment_id": info["treatment_id"],
                   "flip_bit": rv["flip_bit"], "salt_hex": rv["salt_hex"], "commitment": rv["commitment"],
                   "commitment_ok": rv["commitment_ok"], "scores": [dict(r) for r in sc]})
        self.emit(self._progress())

    def _progress(self) -> dict:
        return {"type": "progress", "slots_done": self.slots_done, "planned": self.cfg.planned_slots, "descriptive_only": True,
                "agents": {k: dict(v) for k, v in self.counts.items()}}

    # ------------------------------------------------------------------------------------------------ event poller
    async def _poll_events(self, stop: asyncio.Event, every: float = 0.12) -> None:
        while True:
            await self._drain_events()
            if stop.is_set():
                return
            await asyncio.sleep(every)

    async def _drain_events(self, limit: int = 120) -> None:
        async with self.db.session("audit_engine") as conn:
            ev = await (await conn.execute(
                "SELECT e.event_id, a.agent_name, d.asset_name, e.op, e.outcome, e.detail, e.row_ref, e.event_time "
                "FROM access_event e JOIN agent a USING (agent_id) JOIN data_asset d USING (asset_id) "
                "WHERE e.event_id > %s ORDER BY e.event_id LIMIT %s", (self._last_event_id, limit))).fetchall()
            if ev:
                self._last_event_id = ev[-1]["event_id"]
            od = await (await conn.execute(
                "SELECT a.agent_name, count(*) AS n, sum(CASE o.side WHEN 'BUY' THEN 1 ELSE 0 END) AS buys "
                "FROM trade_order o JOIN agent a USING (agent_id) WHERE o.order_id > %s GROUP BY a.agent_name", (self._last_order_id,))).fetchall()
            if od:
                self._last_order_id = (await (await conn.execute("SELECT max(order_id) AS m FROM trade_order")).fetchone())["m"]
        if ev:
            self.emit({"type": "access_events", "events": [{**r, "event_time": r["event_time"].isoformat()} for r in ev]})
        if od:
            self.emit({"type": "orders", "orders": [dict(r) for r in od]})

    # ------------------------------------------------------------------------------------------------ main
    async def run(self) -> int:
        cfg = self.cfg
        await self._setup()
        sim = cfg.clock_mode == "SIMULATED"
        L = (cfg.sim_slot_ms if sim else cfg.slot_ms) / 1000.0
        self.emit({"type": "campaign_created", "campaign_id": self.cid, "wall": self.wall_name, "clock_mode": cfg.clock_mode,
                   "simulated_clock": sim, "alpha": cfg.alpha, "planned_slots": cfg.planned_slots, "design": cfg.design,
                   "n_cells": self.n_cells, "family_size": self.n_cells * len(self.traders),
                   "treatments": [dict(t) for t in self.treatments], "agents": [t.name for t in self.traders], "seed": self.seed,
                   "trust": {t.name: t.trust for t in self.traders}, "slot_s": L})
        async with self.db.session("audit_engine") as conn:
            db_now = (await (await conn.execute("SELECT clock_timestamp() AS t")).fetchone())["t"]
        self.clock_offset = db_now - dt.datetime.now(UTC)          # DB clock is the source of truth
        if sim:
            # Each SIMULATED campaign owns a disjoint 2-hour window of the simulated past, derived from its id, so slots of
            # different campaigns never overlap (DB constraint canary_slot_one_clock) and all lie before now (RLS reveal).
            t0 = SIM_EPOCH + dt.timedelta(hours=2 * self.cid)
        else:
            t0 = db_now + dt.timedelta(seconds=1.5)
        async with self.db.session("audit_engine") as conn:
            await conn.execute("SELECT start_campaign(%s,%s)", (self.cid, t0 if sim else db_now))
        self.emit({"type": "campaign_started", "campaign_id": self.cid, "t0": t0.isoformat()})
        stop_poll = asyncio.Event()
        poller = asyncio.create_task(self._poll_events(stop_poll)) if not sim else None
        try:
            if sim:
                await self._run_sim(t0, L)
            else:
                await self._run_live(t0, L)
            # all slots have ended: the verdict is computed once, at the planned n
            if not sim:
                await self._sleep_until(t0 + dt.timedelta(seconds=cfg.planned_slots * L + 0.2), False)
            await self._drain_events(limit=100000) if sim else None
            closed_at = (t0 + dt.timedelta(seconds=cfg.planned_slots * L + 1)) if sim else None
            async with self.db.session("audit_engine") as conn:
                nres = (await (await conn.execute("SELECT freeze_campaign(%s,%s,coalesce(%s::timestamptz, clock_timestamp())) AS n",
                                                  (self.cid, self.engine_user, closed_at))).fetchone())["n"]
            stop_poll.set()
            if poller:
                await poller
            self.emit({"type": "verdict_frozen", "campaign_id": self.cid, "n_results": nres})
            return self.cid
        except BaseException:
            stop_poll.set()
            if poller:
                poller.cancel()
            try:
                async with self.db.session("audit_engine") as conn:
                    await conn.execute("SELECT abort_campaign(%s)", (self.cid,))
            except Exception:
                pass
            raise

    async def _run_live(self, t0: dt.datetime, L: float) -> None:
        N = self.cfg.planned_slots
        infos: dict[int, dict] = {}
        infos[0] = await self._commit_slot(0, t0, t0 + dt.timedelta(seconds=L), None)
        fin: list[asyncio.Task] = []
        for i in range(N):
            start = t0 + dt.timedelta(seconds=i * L)
            await self._sleep_until(start - dt.timedelta(seconds=0.02), False)
            if i + 1 < N:                                          # publish the NEXT commitment during this slot
                nxt = start + dt.timedelta(seconds=L)
                infos[i + 1] = await self._commit_slot(i + 1, nxt, nxt + dt.timedelta(seconds=L), None)
            await self._sleep_until(start, False)
            self.emit({"type": "slot_open", "slot_id": infos[i]["slot_id"], "index": i})
            await self._agents(start, False, (0.12, 0.40), L)
            await self._sleep_until(start + dt.timedelta(seconds=L + 0.03), False)
            fin.append(asyncio.create_task(self._finalize_slot(infos[i])))
        await asyncio.gather(*fin)

    async def _run_sim(self, t0: dt.datetime, L: float) -> None:
        N = self.cfg.planned_slots

        async def one(i: int):
            start = t0 + dt.timedelta(seconds=i * L)
            info = await self._commit_slot(i, start, start + dt.timedelta(seconds=L), start - dt.timedelta(seconds=0.5))
            self.emit({"type": "slot_open", "slot_id": info["slot_id"], "index": i})
            await self._agents(start, True, (0.05, 0.20), L)
            await self._finalize_slot(info)

        c = max(1, self.cfg.concurrency)
        for lo in range(0, N, c):
            await asyncio.gather(*[one(i) for i in range(lo, min(N, lo + c))])
            if (lo // c) % 5 == 0:
                await self._drain_events()
