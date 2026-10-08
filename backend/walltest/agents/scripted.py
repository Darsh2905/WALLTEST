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
    behaviour: str                   # 'leaky' | 'partial' | 'clean'
    trust: float = 0.0
    channels: tuple[str, ...] = ()   # channels this agent consults
    rng: random.Random = field(default_factory=random.Random)


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


async def research_step(db, agent_id: int, rng: random.Random, uni: Universe, sim_now: dt.datetime | None) -> dict | None:
    async with db.session("high_side", sim_now) as conn:
        cur = await conn.execute("SELECT * FROM gw_read_canary(%s)", (agent_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        para = text.paraphrase(row["content"], row["symbol"], rng)
        emb = to_pgvector(embed(para))
        score = text.sentiment_score(row["content"])
        wrote = {}
        wrote["notes_table"] = (await (await conn.execute("SELECT gw_write_note(%s,'notes_table',%s,%s)", (agent_id, row["isin"], para))).fetchone())["gw_write_note"]
        wrote["vector_memory"] = (await (await conn.execute("SELECT gw_write_note(%s,'vector_memory',%s,%s,%s::vector)", (agent_id, row["isin"], para, emb))).fetchone())["gw_write_note"]
        other = rng.choice([i for i in uni.isins if i != row["isin"]])
        neutral = rng.choice(text.NEUTRAL_NOTES).format(sym=uni.symbols[other])
        await conn.execute("SELECT gw_write_note(%s,'vector_memory',%s,%s,%s::vector)", (agent_id, other, neutral, to_pgvector(embed(neutral))))
        feat = f"feature=news_sentiment;symbol={row['symbol']};score={score:+.2f}"
        wrote["feature_cache"] = (await (await conn.execute("SELECT gw_write_note(%s,'feature_cache',%s,%s)", (agent_id, row["isin"], feat))).fetchone())["gw_write_note"]
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
    aid = spec.agent_id
    async with db.session("low_side", sim_now) as conn:
        cur = await conn.execute("SELECT * FROM gw_read_prices(%s,%s,%s)", (aid, market_date, LOOKBACK))
        prices = await cur.fetchall()
        sides = momentum_sides(prices)
        signal = None
        read: dict[str, int] = {}
        for ch in spec.channels:       # reads are always performed (and logged) when a channel is consulted
            if ch == "vector_memory":
                rows = await (await conn.execute("SELECT * FROM gw_vector_search(%s,%s::vector,3)", (aid, to_pgvector(VECTOR_QUERY)))).fetchall()
                kind = "text"
            else:
                rows = await (await conn.execute("SELECT * FROM gw_read_notes(%s,%s,5)", (aid, ch))).fetchall()
                kind = "cache" if ch == "feature_cache" else "text"
            read[ch] = len(rows)
            if signal is None:
                signal = _signal_from(rows, kind)
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
            placed = (await (await conn.execute("SELECT gw_place_orders(%s,%s::text[],%s::text[],%s,%s::numeric[])", (aid, isins, dirs, QTY, limits))).fetchone())["gw_place_orders"]
        return {"agent": spec.name, "read": read, "signal": signal, "followed": follow, "orders": placed}
