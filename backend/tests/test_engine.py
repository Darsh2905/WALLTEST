"""End-to-end through the real engine, gateway, RLS and verdict views (committed data in the test database)."""
import asyncio
import datetime as dt

import psycopg
import pytest
from scipy.stats import chi2_contingency, binomtest

from walltest import commitment as cm
from walltest import config
from walltest.db import Database
from walltest.engine import CampaignRunner, RunConfig

UTC = dt.timezone.utc


@pytest.fixture
async def db(enginedb):
    d = Database(config.api_dsn(enginedb), min_size=2, max_size=12)
    await d.open()
    yield d
    await d.close()


async def run(db, **kw):
    ev = []
    cfg = RunConfig(**{"clock_mode": "SIMULATED", "seed": 11, **kw})
    r = CampaignRunner(db, cfg, ev.append)
    cid = await r.run()
    return cid, ev


async def q(db, role, sql, params=None):
    return await db.fetch(role, sql, params)


async def test_trust_zero_leaky_is_identical_to_clean_slot_by_slot(db):
    """No signal followed => the leaky trader's decision is exactly the price-momentum rule, i.e. the clean trader's."""
    cid, _ = await run(db, planned_slots=24, design="ALL_ON", alpha=0.05, trust={"trader-leaky": 0.0, "trader-partial": 0.0})
    rows = await q(db, "audit_engine", "SELECT s.slot_id, a.agent_name, s.correct, s.net_position FROM v_slot_score s JOIN agent a ON a.agent_id=s.low_agent_id WHERE s.campaign_id=%s", (cid,))
    by = {}
    for r in rows:
        by.setdefault(r["slot_id"], {})[r["agent_name"]] = (r["correct"], r["net_position"])
    assert len(by) == 24
    assert all(v["trader-leaky"] == v["trader-clean"] == v["trader-partial"] for v in by.values())


async def test_trust_one_leaky_and_partial_are_always_right_through_real_channels(db):
    cid, ev = await run(db, planned_slots=40, design="ALL_ON", alpha=0.001, trust={"trader-leaky": 1.0, "trader-partial": 1.0})
    v = {r["agent_name"]: r for r in await q(db, "audit_engine", "SELECT a.agent_name, v.* FROM v_verdict v JOIN agent a ON a.agent_id=v.low_agent_id WHERE v.campaign_id=%s", (cid,))}
    assert v["trader-leaky"]["n_correct"] == 40 and v["trader-partial"]["n_correct"] == 40     # the planted accuracy 0.5 + 1/2 is realised
    assert v["trader-leaky"]["verdict"] == "LEAK" and v["trader-partial"]["verdict"] == "LEAK"
    assert v["trader-clean"]["verdict"] == "NO_EVIDENCE" and v["trader-clean"]["n_correct"] < 40
    frozen = await q(db, "compliance", "SELECT count(*) AS n FROM audit_result WHERE campaign_id=%s", (cid,))
    assert frozen[0]["n"] == 3


async def test_clean_trader_uses_no_shared_channel(db):
    cid, _ = await run(db, planned_slots=8, design="ALL_ON", trust={"trader-leaky": 0.9, "trader-partial": 0.9})
    rows = await q(db, "audit_engine", "SELECT d.asset_name, count(*) AS n FROM access_event e JOIN agent a USING (agent_id) JOIN data_asset d USING (asset_id) "
                                       "JOIN audit_campaign c ON c.campaign_id=%s WHERE a.agent_name='trader-clean' AND e.event_time <@ tstzrange(c.started_at, c.closed_at, '[]') GROUP BY 1", (cid,))
    assert {r["asset_name"] for r in rows} == {"daily_price"}


async def test_blocked_channels_carry_nothing(db):
    cid, ev = await run(db, planned_slots=40, design="ONE_AT_A_TIME", alpha=0.05, trust={"trader-leaky": 1.0, "trader-partial": 1.0}, partial_channel="vector_memory")
    eff = await q(db, "audit_engine", "SELECT a.agent_name, e.channel, e.acc_on, e.acc_off FROM v_channel_effect e JOIN agent a ON a.agent_id=e.low_agent_id WHERE e.campaign_id=%s", (cid,))
    pe = {r["channel"]: r for r in eff if r["agent_name"] == "trader-partial"}
    assert pe["vector_memory"]["acc_on"] == 1.0 and pe["vector_memory"]["acc_off"] < 0.9      # the partial agent's channel is attributed
    assert pe["notes_table"]["acc_on"] == pytest.approx(pe["notes_table"]["acc_off"], abs=0.5) # other channels: no systematic effect
    leaky_all_off = await q(db, "audit_engine", "SELECT v.n_correct, v.n_slots FROM v_verdict v JOIN agent a ON a.agent_id=v.low_agent_id JOIN treatment t USING (treatment_id) "
                                                "WHERE v.campaign_id=%s AND a.agent_name='trader-leaky' AND NOT t.vector_memory_on AND NOT t.notes_table_on AND NOT t.cache_on", (cid,))
    assert leaky_all_off[0]["n_slots"] == 8 and leaky_all_off[0]["n_correct"] < 8              # all channels off: no signal reaches it
    denied = await q(db, "audit_engine", "SELECT count(*) AS n FROM access_event e JOIN agent a USING (agent_id) WHERE e.outcome='DENIED' AND e.detail='CHANNEL_OFF' AND a.agent_name='trader-partial'")
    assert denied[0]["n"] > 0           # blocked reads are logged, not silently dropped


async def test_balanced_blocking_randomised_bits_and_verified_commitments(db):
    cid, ev = await run(db, planned_slots=128, design="FULL_FACTORIAL", alpha=0.001, trust={"trader-leaky": 0.9, "trader-partial": 0.9})
    slots = await q(db, "audit_engine", "SELECT s.slot_id, s.treatment_id, s.commitment, lower(s.slot_period) AS st FROM canary_slot s WHERE campaign_id=%s ORDER BY lower(s.slot_period)", (cid,))
    assert len(slots) == 128
    for b in range(16):                                                       # every block of 8 consecutive slots has each cell exactly once
        assert len({s["treatment_id"] for s in slots[8 * b: 8 * b + 8]}) == 8
    rev = await q(db, "audit_engine", "SELECT slot_id, flip_bit, salt_hex, commitment_ok FROM v_slot_reveal WHERE campaign_id=%s", (cid,))
    assert len(rev) == 128 and all(r["commitment_ok"] for r in rev)
    assert len({r["salt_hex"] for r in rev}) == 128 and all(len(r["salt_hex"]) >= 32 for r in rev)       # >= 16 bytes, all distinct
    com = {s["slot_id"]: (s["commitment"].strip(), s["st"]) for s in slots}
    for r in rev:                                                             # the engine's Python hash agrees with the stored commitment
        assert cm.commitment(cid, com[r["slot_id"]][1], r["flip_bit"], bytes.fromhex(r["salt_hex"])) == com[r["slot_id"]][0]
    var = await q(db, "audit_engine", "SELECT v.slot_id, v.variant_bit FROM canary_variant v WHERE v.direction='POSITIVE' AND v.slot_id = ANY(%s)", ([s["slot_id"] for s in slots],))
    pos_bit = {r["slot_id"]: r["variant_bit"] for r in var}
    flips = {r["slot_id"]: r["flip_bit"] for r in rev}
    table = [[0, 0], [0, 0]]
    for sid in flips:
        table[flips[sid]][pos_bit[sid]] += 1
    assert chi2_contingency(table)[1] > 1e-4                                  # POSITIVE's bit is independent of the flip
    assert binomtest(sum(pos_bit.values()), 128, 0.5).pvalue > 1e-4           # and unbiased
    assert binomtest(sum(flips.values()), 128, 0.5).pvalue > 1e-4


async def test_channel_attribution_factorial(db):
    cid, _ = await run(db, planned_slots=128, design="FULL_FACTORIAL", alpha=0.001, trust={"trader-leaky": 0.9, "trader-partial": 0.9})
    res = await q(db, "audit_engine", "SELECT a.agent_name, t.vector_memory_on AS v, t.notes_table_on AS n, t.cache_on AS c, r.verdict, r.n_correct, r.n_slots "
                                      "FROM audit_result r JOIN agent a ON a.agent_id=r.low_agent_id JOIN treatment t USING (treatment_id) WHERE r.campaign_id=%s", (cid,))
    assert len(res) == 24
    for r in res:   # a leaky cell with any channel on must score far above chance (accuracy ~0.95 planted)
        if r["agent_name"] == "trader-leaky" and (r["v"] or r["n"] or r["c"]):
            assert r["n_correct"] / r["n_slots"] >= 0.75, r
    eff = {(r["agent_name"], r["channel"]): r for r in await q(db, "audit_engine", "SELECT a.agent_name, e.* FROM v_channel_effect e JOIN agent a ON a.agent_id=e.low_agent_id WHERE campaign_id=%s", (cid,))}
    assert eff[("trader-partial", "vector_memory")]["acc_on"] - eff[("trader-partial", "vector_memory")]["acc_off"] > 0.3
    for ch in ("notes_table", "cache"):
        assert abs(eff[("trader-partial", ch)]["acc_on"] - eff[("trader-partial", ch)]["acc_off"]) < 0.3
    # redundancy: the leaky agent reads all three, so no single channel removes its leak; only all-off does
    all_off = [r for r in res if r["agent_name"] == "trader-leaky" and not (r["v"] or r["n"] or r["c"])][0]
    assert all_off["verdict"] == "NO_EVIDENCE"
    clean_flags = [r for r in res if r["agent_name"] == "trader-clean" and r["verdict"] == "LEAK"]
    assert clean_flags == []                                                  # family-wise false alarm at alpha=0.001: expected 0


async def test_exposure_trail_and_lag(db):
    cid, _ = await run(db, planned_slots=8, design="ALL_ON", trust={"trader-leaky": 1.0, "trader-partial": 1.0})
    sid = (await q(db, "compliance", "SELECT slot_id FROM canary_slot WHERE campaign_id=%s ORDER BY slot_id LIMIT 1", (cid,)))[0]["slot_id"]
    tr = await q(db, "compliance", "SELECT * FROM exposure_trail(%s)", (sid,))
    actions = [(t["agent_name"], t["action"]) for t in tr]
    assert actions[0] == ("research-agent", "READ canary variant")
    assert ("research-agent", "WRITE derived note") in actions and ("trader-leaky", "READ derived note") in actions
    assert any(a == "trader-leaky" and x.startswith("ORDER") for a, x in actions) and any(a == "trader-partial" and x == "READ derived note" for a, x in actions)
    assert not any(a == "trader-clean" and x == "READ derived note" for a, x in actions)
    times = [t["event_time"] for t in tr]
    assert times == sorted(times)
    lag = await q(db, "compliance", "SELECT low_agent, lag_ms FROM v_lag WHERE campaign_id=%s", (cid,))
    assert len(lag) == 8 * 3 and all(0 < r["lag_ms"] < 2000 for r in lag)


async def test_the_research_agent_wrote_real_embeddings_and_paraphrases(db):
    cid, _ = await run(db, planned_slots=4, design="ALL_ON", trust={"trader-leaky": 0.5, "trader-partial": 0.5})
    rows = await q(db, "compliance", "SELECT d.asset_name, count(*) AS n, count(n.embedding) AS emb FROM agent_note n JOIN data_asset d USING (asset_id) "
                                     "JOIN audit_campaign c ON c.campaign_id=%s WHERE n.created_at <@ tstzrange(c.started_at, c.closed_at, '[]') GROUP BY 1 ORDER BY 1", (cid,))
    got = {r["asset_name"]: (r["n"], r["emb"]) for r in rows}
    assert got == {"feature_cache": (4, 0), "notes_table": (4, 0), "vector_memory": (8, 8)}      # 4 slots: 1 note + 1 distractor in vector memory


async def test_live_campaign_commit_before_expose_on_the_wall_clock(db):
    seen = []
    cfg = RunConfig(clock_mode="LIVE", planned_slots=6, design="ALL_ON", slot_ms=700, alpha=0.05, seed=5, trust={"trader-leaky": 1.0, "trader-partial": 1.0})
    r = CampaignRunner(db, cfg, seen.append)
    poll_results = []

    async def poll():
        while True:
            await asyncio.sleep(0.25)
            if r.cid:
                rows = await q(db, "audit_engine", "SELECT decidable, verdict, p_raw FROM v_verdict WHERE campaign_id=%s", (r.cid,))
                st = (await q(db, "audit_engine", "SELECT status FROM audit_campaign WHERE campaign_id=%s", (r.cid,)))[0]["status"]
                poll_results.append((st, rows))
    t = asyncio.create_task(poll())
    cid = await r.run()
    t.cancel()
    slots = await q(db, "audit_engine", "SELECT slot_id, committed_at, lower(slot_period) AS st, upper(slot_period) AS en FROM canary_slot WHERE campaign_id=%s ORDER BY 3", (cid,))
    assert len(slots) == 6
    for s in slots:
        assert s["committed_at"] < s["st"] - dt.timedelta(milliseconds=300)             # published well before the slot opens (server clock)
        assert (s["en"] - s["st"]).total_seconds() == pytest.approx(0.7)
    for a, b in zip(slots, slots[1:]):
        assert a["en"] == b["st"]
    rev = [e for e in seen if e["type"] == "slot_revealed"]
    assert len(rev) == 6 and all(e["commitment_ok"] for e in rev)
    # the first order of every slot is placed inside its window and nothing is "no trade" because of latency
    nt = await q(db, "audit_engine", "SELECT sum(n_no_trade) AS n FROM v_progress WHERE campaign_id=%s", (cid,))
    assert nt[0]["n"] == 0
    # NO PEEKING: while the campaign was RUNNING, no inferential column was ever visible
    running = [rows for st, rows in poll_results if st == "RUNNING"]
    assert running
    for rows in running:
        for row in rows:
            if not row["decidable"]:
                assert row["verdict"] is None and row["p_raw"] is None
    # the sealed -> committed -> revealed order is visible in the event stream
    order = [e["type"] for e in seen if e["type"] in ("slot_committed", "slot_open", "slot_revealed")]
    assert order[0] == "slot_committed" and order.index("slot_open") < order.index("slot_revealed")


async def test_cancelled_run_is_aborted_not_left_running(db):
    cfg = RunConfig(clock_mode="LIVE", planned_slots=6, design="ALL_ON", slot_ms=700, seed=1)
    r = CampaignRunner(db, cfg, None)
    task = asyncio.create_task(r.run())
    await asyncio.sleep(3.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    st = (await q(db, "audit_engine", "SELECT status FROM audit_campaign WHERE campaign_id=%s", (r.cid,)))[0]["status"]
    assert st == "ABORTED"
    assert (await q(db, "audit_engine", "SELECT count(*) AS n FROM audit_result WHERE campaign_id=%s", (r.cid,)))[0]["n"] == 0


async def test_null_control_wall_audits_only_the_clean_trader(db):
    cid, _ = await run(db, planned_slots=16, design="ALL_ON", null_control=True)
    rows = await q(db, "audit_engine", "SELECT a.agent_name, v.family_size FROM v_verdict v JOIN agent a ON a.agent_id=v.low_agent_id WHERE v.campaign_id=%s", (cid,))
    assert [(r["agent_name"], r["family_size"]) for r in rows] == [("trader-clean", 1)]


async def test_live_run_survives_early_reveal_requests_and_commits_well_ahead(db):
    """Regression (found by the Playwright run): a LIVE campaign was ABORTED because the engine asked for a reveal a few ms before the DB considered the
    slot ended (clock-offset estimate error on a loaded machine). The DB is the authority: the engine must retry, not abort."""
    cfg = RunConfig(clock_mode="LIVE", planned_slots=6, design="ALL_ON", slot_ms=700, alpha=0.05, seed=9, trust={"trader-leaky": 1.0, "trader-partial": 1.0})
    r = CampaignRunner(db, cfg, None)
    r.finalize_margin = -0.3                      # ask for every reveal 300 ms BEFORE the slot has ended
    cid = await r.run()
    assert r.reveal_retries > 0                    # the database refused (WT006) and the engine retried
    st = (await q(db, "audit_engine", "SELECT status FROM audit_campaign WHERE campaign_id=%s", (cid,)))[0]["status"]
    assert st == "CLOSED"
    ok = await q(db, "audit_engine", "SELECT bool_and(commitment_ok) AS ok, count(*) AS n FROM v_slot_reveal WHERE campaign_id=%s", (cid,))
    assert ok[0]["ok"] and ok[0]["n"] == 6
    slots = await q(db, "audit_engine", "SELECT committed_at, lower(slot_period) AS st FROM canary_slot WHERE campaign_id=%s ORDER BY 2", (cid,))
    # commitments are published three slots ahead: from the 4th slot on, the margin is ~3 slot lengths (2.1 s)
    assert all((s["st"] - s["committed_at"]).total_seconds() > 1.4 for s in slots[3:]) and all((s["st"] - s["committed_at"]).total_seconds() > 0.3 for s in slots)
