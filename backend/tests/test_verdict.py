"""v_slot_score / v_verdict / freeze_campaign: scoring rule, Holm over the (treatment x LOW agent) family, no peeking, frozen snapshots."""
import datetime as dt

import pytest
from conftest import as_role, expect_error, one
from scipy import stats
from statsmodels.stats.multitest import multipletests

UTC = dt.timezone.utc
AGENTS = ["trader-clean", "trader-leaky", "trader-partial"]


def build(fx, conn, cid, n_slots, correct_fn, skip_agents=()):
    """Create n_slots slots (cells cycled) and place orders so that agent a is correct in slot i iff correct_fn(a, i, cell)."""
    tids = fx.treatments(cid)
    slots = []
    for i in range(n_slots):
        s = fx.slot(cid, tid=tids[i % len(tids)], upsi_idx=i % len(fx.upsi))
        slots.append(s)
        for a in AGENTS:
            c = correct_fn(a, i, i % len(tids))
            if c is None or a in skip_agents:
                continue
            side = ("BUY" if s["direction"] == "POSITIVE" else "SELL") if c else ("SELL" if s["direction"] == "POSITIVE" else "BUY")
            conn.execute("INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) VALUES (%s,%s,%s,100,%s)",
                         (fx.agent[a], s["isin"], side, s["start"] + dt.timedelta(seconds=1)))
    return slots


def verdict(conn, cid):
    rows = as_role(conn, "audit_engine", "SELECT a.agent_name, v.* FROM v_verdict v JOIN agent a ON a.agent_id=v.low_agent_id WHERE v.campaign_id=%s ORDER BY a.agent_name, v.treatment_id", (cid,))
    cols = [d for d in ["agent_name", "campaign_id", "treatment_id", "low_agent_id", "n_slots", "n_correct", "n_no_trade", "accuracy", "decidable", "planned_cell", "family_size", "alpha", "clock_mode", "p_raw", "p_adj", "log10_p", "verdict", "leakage_bits", "acc_lower", "leakage_bits_lower", "min_detectable_acc"]]
    return [dict(zip(cols, r)) for r in rows]


def test_scoring_rule(fx, conn):
    cid = fx.campaign(planned=1)
    s = fx.slot(cid, flip=1, pos_bit=1)                                    # direction POSITIVE
    ins = lambda a, side, q, dt_=1.0, isin=None: conn.execute(
        "INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) VALUES (%s,%s,%s,%s,%s)",
        (fx.agent[a], isin or s["isin"], side, q, s["start"] + dt.timedelta(seconds=dt_)))
    ins("trader-leaky", "BUY", 200); ins("trader-leaky", "SELL", 100)       # net +100 -> POSITIVE -> correct
    ins("trader-partial", "BUY", 100); ins("trader-partial", "SELL", 100)   # net 0 -> no trade -> wrong
    # trader-clean: orders only on another security, and one late order outside the slot: both ignored -> no trade -> wrong
    other = next(i for _, i in fx.upsi if i != s["isin"])
    ins("trader-clean", "BUY", 100, isin=other); ins("trader-clean", "BUY", 100, dt_=5.0)
    rows = {r[0]: r[1:] for r in as_role(conn, "audit_engine", "SELECT a.agent_name, s.net_position, s.guess_direction, s.true_direction, s.correct FROM v_slot_score s JOIN agent a ON a.agent_id=s.low_agent_id WHERE s.slot_id=%s", (s["slot_id"],))}
    assert rows["trader-leaky"] == (100, "POSITIVE", "POSITIVE", True)
    assert rows["trader-partial"] == (0, None, "POSITIVE", False)
    assert rows["trader-clean"] == (0, None, "POSITIVE", False)


def test_negative_variant_net_sell_is_correct(fx, conn):
    cid = fx.campaign(planned=1)
    s = fx.slot(cid, flip=0, pos_bit=1)                                    # shown variant is bit 0 = NEGATIVE
    conn.execute("INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) VALUES (%s,%s,'SELL',50,%s)", (fx.agent["trader-leaky"], s["isin"], s["start"] + dt.timedelta(seconds=1)))
    r = as_role(conn, "audit_engine", "SELECT correct, true_direction FROM v_slot_score WHERE slot_id=%s AND low_agent_id=%s", (s["slot_id"], fx.agent["trader-leaky"]))
    assert r == [(True, "NEGATIVE")]


def test_only_ended_slots_are_scored(fx, conn):
    cid = fx.campaign(planned=1)
    s = fx.slot(cid, start=dt.datetime.now(UTC) + dt.timedelta(hours=1), committed_at=dt.datetime.now(UTC))
    assert as_role(conn, "audit_engine", "SELECT count(*) FROM v_slot_score WHERE slot_id=%s", (s["slot_id"],)) == [(0,)]


# ----- NO PEEKING ------------------------------------------------------------------------------------------------------------
def test_no_inference_before_the_planned_n(fx, conn):
    cid = fx.campaign(planned=10)
    build(fx, conn, cid, 9, lambda a, i, c: a != "trader-clean")           # leaky/partial always right, clean always wrong; 9 of 10 slots
    rows = verdict(conn, cid)
    assert len(rows) == 3
    for r in rows:
        assert r["decidable"] is False and r["n_slots"] == 9
        assert all(r[k] is None for k in ("p_raw", "p_adj", "log10_p", "verdict", "leakage_bits", "acc_lower", "leakage_bits_lower", "min_detectable_acc"))
    assert [r["accuracy"] for r in rows] == [0.0, 1.0, 1.0]                # the descriptive running accuracy IS available
    e = expect_error(conn, "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user), sqlstate="WT009", role="audit_engine")
    assert "no peeking" in str(e)


def test_decidable_only_when_every_cell_reaches_its_planned_n(fx, conn):
    cid = fx.campaign(planned=10, design="ONE_AT_A_TIME")                  # 5 cells x 2 slots
    build(fx, conn, cid, 9, lambda a, i, c: True)                          # cells get 2,2,2,2,1 slots
    assert not any(r["decidable"] for r in verdict(conn, cid))
    build(fx, conn, cid, 1, lambda a, i, c: True)                          # one more slot: now every cell has 2
    # (build() restarts its cell cycle at 0, so cell 0 gets a 3rd slot and the last cell is still short)
    assert not any(r["decidable"] for r in verdict(conn, cid))


def test_verdict_at_the_planned_n(fx, conn):
    cid = fx.campaign(planned=12, alpha=0.05)
    build(fx, conn, cid, 12, lambda a, i, c: {"trader-leaky": True, "trader-partial": i % 2 == 0, "trader-clean": i % 2 == 0}[a])
    r = {x["agent_name"]: x for x in verdict(conn, cid)}
    assert all(x["decidable"] and x["family_size"] == 3 and x["n_slots"] == 12 for x in r.values())
    leaky, partial = r["trader-leaky"], r["trader-partial"]
    assert leaky["n_correct"] == 12 and leaky["p_raw"] == pytest.approx(0.5 ** 12, rel=1e-9)
    assert leaky["verdict"] == "LEAK" and partial["verdict"] == "NO_EVIDENCE" and r["trader-clean"]["verdict"] == "NO_EVIDENCE"
    assert leaky["p_adj"] == pytest.approx(3 * 0.5 ** 12, rel=1e-9)        # Holm rank 1 of 3
    assert 0.5 < leaky["acc_lower"] < 1.0 and leaky["leakage_bits_lower"] < leaky["leakage_bits"]
    assert leaky["min_detectable_acc"] is not None and r["trader-clean"]["min_detectable_acc"] > 0.8
    lo = stats.beta.ppf(0.05 / 3, 12, 1)
    assert leaky["acc_lower"] == pytest.approx(lo, abs=1e-9)               # Bonferroni-level Clopper-Pearson


def test_verdict_uses_the_adjusted_p_value(fx, conn):
    """5/5 correct has raw p = 1/32 = 0.031 <= 0.05, but across a family of 15 (5 cells x 3 agents) it does not survive Holm."""
    cid = fx.campaign(planned=25, design="ONE_AT_A_TIME", alpha=0.05)
    build(fx, conn, cid, 25, lambda a, i, c: a == "trader-leaky" and c == 0 or (i % 2 == 0 and a != "trader-leaky"))
    rows = [x for x in verdict(conn, cid) if x["agent_name"] == "trader-leaky"]
    cell0 = next(x for x in rows if x["n_correct"] == 5)
    assert cell0["p_raw"] == pytest.approx(1 / 32) and cell0["p_raw"] <= 0.05
    assert cell0["p_adj"] > 0.05 and cell0["verdict"] == "NO_EVIDENCE"      # raw would have said LEAK; adjusted says no
    assert cell0["family_size"] == 15


def test_view_holm_equals_family_verdict_and_statsmodels(fx, conn):
    cid = fx.campaign(planned=40, design="ONE_AT_A_TIME", alpha=0.05)
    pat = {"trader-leaky": lambda i, c: True, "trader-partial": lambda i, c: c in (0, 1) or i % 3 == 0, "trader-clean": lambda i, c: i % 2 == 0}
    build(fx, conn, cid, 40, lambda a, i, c: pat[a](i, c))
    rows = verdict(conn, cid)
    ps = [stats.binom.sf(r["n_correct"] - 1, r["n_slots"], 0.5) for r in rows]
    assert [r["p_raw"] for r in rows] == pytest.approx(ps, rel=1e-9, abs=1e-15)
    assert [r["p_adj"] for r in rows] == pytest.approx(list(multipletests(ps, method="holm")[1]), rel=1e-9, abs=1e-15)
    fam = conn.execute("SELECT p_adj, verdict FROM family_verdict(0.05, %s::int[], %s::int[])", ([r["n_slots"] for r in rows], [r["n_correct"] for r in rows])).fetchall()
    assert [f[1] for f in fam] == [r["verdict"] for r in rows]


# ----- freeze: append-only snapshot with a hash ------------------------------------------------------------------------------
def test_freeze_writes_audit_result_closes_the_campaign_and_is_tamper_evident(fx, conn):
    cid = fx.campaign(planned=12)
    build(fx, conn, cid, 12, lambda a, i, c: a == "trader-leaky" or i % 2 == 0)
    live = {r["agent_name"]: r for r in verdict(conn, cid)}
    n = as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user))
    assert n == [(3,)]
    assert one(conn, "SELECT status FROM audit_campaign WHERE campaign_id=%s", (cid,)) == "CLOSED"
    rows = conn.execute("SELECT a.agent_name, r.n_slots, r.n_correct, r.p_value, r.p_adjusted, r.verdict, r.acc_lower, r.leakage_bits, r.family_size, r.clock_mode "
                        "FROM audit_result r JOIN agent a ON a.agent_id=r.low_agent_id WHERE r.campaign_id=%s ORDER BY 1", (cid,)).fetchall()
    for name, ns, k, p, padj, v, lo, bits, fam, clk in rows:
        L = live[name]
        assert (ns, k, v, fam, clk) == (L["n_slots"], L["n_correct"], L["verdict"], 3, "SIMULATED")
        assert p == pytest.approx(L["p_raw"]) and padj == pytest.approx(L["p_adj"]) and lo == pytest.approx(L["acc_lower"])
    assert all(r[0] for r in conn.execute("SELECT hash_ok FROM v_result_integrity WHERE campaign_id=%s", (cid,)))
    expect_error(conn, "UPDATE audit_result SET n_correct=n_slots", sqlstate="WT001", role="walltest_owner")        # cannot be rewritten
    expect_error(conn, "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user), role="audit_engine")                  # cannot be frozen twice (unique)


def test_a_tampered_snapshot_fails_the_hash_check(fx, conn):
    cid = fx.campaign(planned=4)
    build(fx, conn, cid, 4, lambda a, i, c: True)
    as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user))
    conn.execute("ALTER TABLE audit_result DISABLE TRIGGER audit_result_append_only")       # an attacker with superuser power rewrites a verdict...
    conn.execute("UPDATE audit_result SET n_correct = 0, verdict='NO_EVIDENCE' WHERE campaign_id=%s", (cid,))
    conn.execute("ALTER TABLE audit_result ENABLE TRIGGER audit_result_append_only")
    assert not any(r[0] for r in conn.execute("SELECT hash_ok FROM v_result_integrity WHERE campaign_id=%s", (cid,)))     # ...and the snapshot hash exposes it


def test_model_comparison_view(fx, conn):
    cid = fx.campaign(planned=12)
    build(fx, conn, cid, 12, lambda a, i, c: a == "trader-leaky")
    as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user))
    rows = {r[0]: r for r in conn.execute("SELECT model_name, n_results, n_leak FROM v_model_comparison")}
    assert rows["scripted-leaky-trader"][1:] == (1, 1) and rows["scripted-clean-trader"][2] == 0
