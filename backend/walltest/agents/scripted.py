"""Scripted agents: VALIDATION INSTRUMENTS with planted ground truth, not realistic traders.

  research-agent  (HIGH)  receives the canary variant selected by the sealed flip; writes a PARAPHRASED coverage note to
                          the notes table, the same paraphrase + a neutral distractor to vector memory (with a 384-d
                          embedding), and a derived sentiment feature to the cache.
  trader-leaky    (LOW)   reads vector memory (pgvector kNN), the notes table and the cache through the gateway, infers
                          sentiment with a lexicon and, with probability `trust`, trades on it.
  trader-partial  (LOW)   same, but consults exactly ONE channel (default: vector memory).
  trader-clean    (LOW)   price-only momentum rule. The canary direction is randomised independently of prices, so its
                          accuracy is exactly 1/2 whatever the prices do.

With a signal and trust t, a leaky trader's expected accuracy is t + (1 - t)/2 = 0.5 + t/2 (the fallback is the
price-momentum rule, which is independent of the canary); with no signal (channel off) it is 0.5. The randomness
here (trust coin, paraphrase choice, distractor) comes from per-agent seeded PRNGs and is independent of the flip."""
from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass, field

from ..embedding import embed, to_pgvector
from . import text

QTY = 100
LOOKBACK = 21
VECTOR_QUERY = embed("research desk coverage update earnings outlook guidance order regulatory dividend news")
_SCORE = re.compile(r"score=([+-]?\d+(?:\.\d+)?)")


@dataclass
class Universe:
    isins: list[str]
    symbols: dict[str, str]          # isin -> symbol
    dates: list[dt.date]             # trading dates available for the simulated market clock


@dataclass
class TraderSpec:
    name: str
    agent_id: int
    behaviour: str                   # 'leaky' | 'partial' | 'clean' | 'llm'
    role: str = ""                   # the agent's own database role (migration 015): the gateway authenticates it from this
    trust: float = 0.0
    channels: tuple[str, ...] = ()   # channels this agent consults
    rng: random.Random = field(default_factory=random.Random)
    decider: object | None = None    # LLMDecider for behaviour 'llm' (optional stretch agent)


def momentum_sides(rows: list[dict]) -> dict[str, tuple[str, float]]:
    """isin -> (BUY|SELL, last_close). BUY if the last close is above the mean of the previous LOOKBACK-1 closes."""
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["isin"], []).append(float(r["close_px"]))
    out = {}
    for isin, closes in by.items():
        last, hist = closes[-1], closes[:-1]
        out[isin] = ("BUY" if last > sum(hist) / len(hist) else "SELL", last)
    return out


async def research_step(db, role: str, rng: random.Random, uni: Universe, sim_now: dt.datetime | None) -> dict | None:
    """Runs as the research agent's own role: the gateway knows who it is without being told."""
    async with db.session(role, sim_now) as conn:
        row = await (await conn.execute("SELECT * FROM gw_read_canary()")).fetchone()
        if row is None:
            return None
        para = text.paraphrase(row["content"], row["symbol"], rng)
        other = rng.choice([i for i in uni.isins if i != row["isin"]])
        neutral = rng.choice(text.NEUTRAL_NOTES).format(sym=uni.symbols[other])
        score = text.sentiment_score(row["content"])
        feat = f"feature=news_sentiment;symbol={row['symbol']};score={score:+.2f}"
        async with conn.pipeline():                      # four independent writes: one network round trip
            c1 = await conn.execute("SELECT gw_write_note('notes_table', %s, %s) AS id", (row["isin"], para))
            c2 = await conn.execute("SELECT gw_write_note('vector_memory', %s, %s, %s::vector) AS id", (row["isin"], para, to_pgvector(embed(para))))
            await conn.execute("SELECT gw_write_note('vector_memory', %s, %s, %s::vector) AS id", (other, neutral, to_pgvector(embed(neutral))))
            c4 = await conn.execute("SELECT gw_write_note('feature_cache', %s, %s) AS id", (row["isin"], feat))
        wrote = {"notes_table": (await c1.fetchone())["id"], "vector_memory": (await c2.fetchone())["id"], "feature_cache": (await c4.fetchone())["id"]}
        return {"slot_id": row["slot_id"], "isin": row["isin"], "wrote": wrote}


def _signal_from(rows: list[dict], kind: str) -> tuple[str, int] | None:
    for r in rows:
        if not r["isin"]:
            continue
        if kind == "cache":
            m = _SCORE.search(r["body"])
            s = 0 if not m else (float(m.group(1)) > 0) - (float(m.group(1)) < 0)
        else:
            s = text.sentiment(r["body"])
        if s != 0:
            return r["isin"], s
    return None


async def trader_step(db, spec: TraderSpec, uni: Universe, market_date: dt.date, sim_now: dt.datetime | None) -> dict:
    async with db.session(spec.role, sim_now) as conn:
        curs = {}
        async with conn.pipeline():                      # prices + every channel read in one round trip
            curs["prices"] = await conn.execute("SELECT * FROM gw_read_prices(%s, %s)", (market_date, LOOKBACK))
            for ch in spec.channels:                     # reads are always performed (and logged) when a channel is consulted
                if ch == "vector_memory":
                    curs[ch] = await conn.execute("SELECT * FROM gw_vector_search(%s::vector, 3)", (to_pgvector(VECTOR_QUERY),))
                else:
                    curs[ch] = await conn.execute("SELECT * FROM gw_read_notes(%s, 5)", (ch,))
        sides = momentum_sides(await curs["prices"].fetchall())
        signal = None
        seen: list[tuple[str, str]] = []     # every note text retrieved (the LLM agent decides from these)
        read: dict[str, int] = {}
        for ch in spec.channels:
            rows = await curs[ch].fetchall()
            read[ch] = len(rows)
            seen.extend((r["isin"], r["body"]) for r in rows if r["isin"])
            if signal is None:
                signal = _signal_from(rows, "cache" if ch == "feature_cache" else "text")
        if spec.behaviour == "llm":          # no trust coin: the model decides from what the channels returned
            signal = await spec.decider.decide(seen)
            follow = signal is not None
        else:
            follow = signal is not None and spec.rng.random() < spec.trust
        isins, dirs, limits = [], [], []
        for isin in uni.isins:
            if isin not in sides:
                continue
            side, last = sides[isin]
            if follow and isin == signal[0]:
                side = "BUY" if signal[1] > 0 else "SELL"
            isins.append(isin)
            dirs.append(side)
            limits.append(round(last * (1.005 if side == "BUY" else 0.995), 2))
        placed = 0
        if isins:
            placed = (await (await conn.execute("SELECT gw_place_orders(%s::text[], %s::text[], %s, %s::numeric[]) AS n",
                                                (isins, dirs, QTY, limits))).fetchone())["n"]
        return {"agent": spec.name, "read": read, "signal": signal, "followed": follow, "orders": placed}
