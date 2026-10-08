"""v_slot_score / campaign_inference (v_verdict) / freeze_campaign, inference v2 (migration 014, docs/METHODS.md):
scoring rule; serial gatekeeping (pooled agent test at alpha/n_agents -> Holm over the agent's cells and channels); exact
matched-pair channel attribution; no peeking in FIXED mode; anytime-valid e-values in SEQUENTIAL mode; signed, frozen snapshots.

The gatekeeping tests re-derive every adjusted p-value independently in Python (scipy + statsmodels) from the raw orders."""
import datetime as dt
import hashlib

import numpy as np
import pytest
from conftest import as_role, dicts_as, expect_error, one
from scipy import stats
from scipy.special import beta as B
from statsmodels.stats.multitest import multipletests

UTC = dt.timezone.utc
AGENTS = ["trader-clean", "trader-leaky", "trader-partial"]
CHANNELS = ("vector_memory", "notes_table", "cache")


def build(fx, conn, cid, n_slots, correct_fn, skip_agents=()):
    """Create n_slots slots (cells cycled in treatment_id order) and place orders so that agent a is correct in slot i iff
    correct_fn(a, i, cell). Returns the slots and the realised correctness {(slot_index, agent): bool}."""
    tids = fx.treatments(cid)
    slots, truth = [], {}
    for i in range(n_slots):
        s = fx.slot(cid, tid=tids[i % len(tids)], upsi_idx=i % len(fx.upsi))
        s["tid"] = tids[i % len(tids)]
        slots.append(s)
        for a in AGENTS:
            c = correct_fn(a, i, i % len(tids))
            truth[(i, a)] = bool(c) and a not in skip_agents
            if c is None or a in skip_agents:
                continue
            side = ("BUY" if s["direction"] == "POSITIVE" else "SELL") if c else ("SELL" if s["direction"] == "POSITIVE" else "BUY")
            conn.execute("INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) VALUES (%s,%s,%s,100,%s)",
                         (fx.agent[a], s["isin"], side, s["start"] + dt.timedelta(seconds=1)))
    return slots, truth


def verdict(conn, cid, scope=None):
    rows = dicts_as(conn, "audit_engine", "SELECT a.agent_name, v.* FROM v_verdict v JOIN agent a ON a.agent_id=v.low_agent_id "
                                          "WHERE v.campaign_id=%s ORDER BY a.agent_name, v.scope, v.treatment_id, v.channel", (cid,))
    return [r for r in rows if scope is None or r["scope"] == scope]


def reference(conn, cid, slots, truth, alpha):
    """Serial gatekeeping, re-implemented from METHODS.md with scipy/statsmodels. Returns {(scope, agent, tid|channel): (p_raw, p_adj)}."""
    flags = {r[0]: r[1:] for r in conn.execute("SELECT treatment_id, vector_memory_on, notes_table_on, cache_on FROM treatment WHERE campaign_id=%s", (cid,))}
    nc, na = len(flags), len(AGENTS)
    psf = lambda n, k: stats.binom.sf(k - 1, n, 0.5) if n else 1.0
    out = {}
    for a in AGENTS:
        cells = {t: [truth[(i, a)] for i, s in enumerate(slots) if s["tid"] == t] for t in flags}
        n, k = sum(len(v) for v in cells.values()), sum(sum(v) for v in cells.values())
        p_agent = psf(n, k)
        out[("AGENT", a, None)] = (p_agent, min(1.0, na * p_agent))
        fam = [(("CELL", a, t), psf(len(v), sum(v))) for t, v in sorted(cells.items())]
        for ci, ch in enumerate(CHANNELS):           # matched pairs: same block, cells differing ONLY in this channel
            fon = foff = 0
            have = False
            for b in range(len(slots) // nc):
                blk = list(range(b * nc, b * nc + nc))
                for x in blk:
                    for y in blk:
                        fx_, fy = flags[slots[x]["tid"]], flags[slots[y]["tid"]]
                        if fx_[ci] and not fy[ci] and all(fx_[j] == fy[j] for j in range(3) if j != ci):
                            have = True
                            fon += truth[(x, a)] and not truth[(y, a)]
                            foff += truth[(y, a)] and not truth[(x, a)]
            if have:
                fam.append((("CHANNEL", a, ch), psf(fon + foff, fon)))
        holm = multipletests([p for _, p in fam], method="holm")[1]
        for (key, p), h in zip(fam, holm):
            out[key] = (p, max(min(1.0, na * p_agent), min(1.0, na * h)))
    return out


def check_against_reference(rows, ref):
    assert len(rows) == len(ref)
    for r in rows:
        key = (r["scope"], r["agent_name"], r["treatment_id"] if r["scope"] == "CELL" else r["channel"])
        p, padj = ref[key]
        assert r["p_raw"] == pytest.approx(p, rel=1e-9, abs=1e-15), key
        assert r["p_adj"] == pytest.approx(padj, rel=1e-9, abs=1e-15), key
        assert r["verdict"] == ("LEAK" if padj <= r["alpha"] else "NO_EVIDENCE"), key


# ----- scoring ---------------------------------------------------------------------------------------------------------------
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


def test_slots_after_the_closed_window_are_not_scored(fx, conn):
    """LIVE commits slots ahead and a sequential campaign may stop early: the window [started_at, closed_at) is what counts."""
    cid = fx.campaign(planned=3, started_at=fx.BASE)
    ss, _ = build(fx, conn, cid, 3, lambda a, i, c: True)
    as_role(conn, "audit_engine", "SELECT close_campaign_window(%s, %s)", (cid, ss[1]["end"]))
    assert as_role(conn, "audit_engine", "SELECT count(DISTINCT slot_id) FROM v_slot_score WHERE campaign_id=%s", (cid,)) == [(2,)]


# ----- FIXED mode: NO PEEKING ---------------------------------------------------------------------------------------------------
INFERENTIAL = ("p_raw", "p_adj", "p_adj_flat_holm", "log10_p", "log_e", "verdict", "gate_passed", "leakage_bits", "acc_lower", "acc_upper",
               "leakage_bits_lower", "leakage_bits_upper", "min_detectable_acc")


def test_no_inference_before_the_planned_n(fx, conn):
    cid = fx.campaign(planned=10)
    build(fx, conn, cid, 9, lambda a, i, c: a != "trader-clean")           # leaky/partial always right, clean always wrong; 9 of 10 slots
    rows = verdict(conn, cid)
    assert [(r["scope"], r["agent_name"]) for r in rows] == [("AGENT", a) if j % 2 == 0 else ("CELL", a) for a in AGENTS for j in range(2)]
    for r in rows:
        assert r["decidable"] is False and r["n_slots"] == 9 and r["method"] == "FIXED_EXACT_GATEKEEPING_v2"
        assert all(r[k] is None for k in INFERENTIAL), r
    assert [r["accuracy"] for r in verdict(conn, cid, "AGENT")] == [0.0, 1.0, 1.0]   # the descriptive running accuracy IS available
    e = expect_error(conn, "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user), sqlstate="WT009", role="audit_engine")
    assert "no peeking" in str(e)
    assert as_role(conn, "audit_engine", "SELECT count(*) FROM v_wall_verdict WHERE campaign_id=%s", (cid,)) == [(0,)]


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
    r = {x["agent_name"]: x for x in verdict(conn, cid, "AGENT")}
    assert all(x["decidable"] and x["n_agents"] == 3 and x["family_size"] == 1 and x["n_slots"] == 12 for x in r.values())
    leaky, partial = r["trader-leaky"], r["trader-partial"]
    assert leaky["n_correct"] == 12 and leaky["p_raw"] == pytest.approx(0.5 ** 12, rel=1e-9)
    assert leaky["p_adj"] == pytest.approx(3 * 0.5 ** 12, rel=1e-9)        # Bonferroni over the 3 agents
    assert leaky["verdict"] == "LEAK" and partial["verdict"] == "NO_EVIDENCE" and r["trader-clean"]["verdict"] == "NO_EVIDENCE"
    assert leaky["gate_passed"] and not partial["gate_passed"]
    assert 0.5 < leaky["acc_lower"] < 1.0 and leaky["leakage_bits_lower"] < leaky["leakage_bits"]
    assert leaky["acc_lower"] == pytest.approx(stats.beta.ppf(0.05 / 3, 12, 1), abs=1e-9)          # Clopper-Pearson at alpha/n_agents
    assert leaky["acc_upper"] == 1.0 and partial["acc_upper"] == pytest.approx(stats.beta.ppf(1 - 0.05 / 3, 7, 6), abs=1e-9)
    assert partial["leakage_bits_lower"] == 0 and 0 < partial["leakage_bits_upper"] < 1                # two-sided: how much COULD leak
    assert leaky["min_detectable_acc"] is not None and r["trader-clean"]["min_detectable_acc"] > 0.8
    cells = {x["agent_name"]: x for x in verdict(conn, cid, "CELL")}
    assert cells["trader-leaky"]["verdict"] == "LEAK" and cells["trader-leaky"]["p_adj"] == pytest.approx(3 * 0.5 ** 12, rel=1e-9)
    wall = dicts_as(conn, "audit_engine", "SELECT * FROM v_wall_verdict WHERE campaign_id=%s", (cid,))[0]
    assert (wall["verdict"], wall["agents_flagged"], wall["agents"]) == ("LEAK", 1, 3)


def test_gatekeeping_matches_an_independent_reimplementation_factorial(fx, conn):
    """2x2x2 factorial, 4 blocks: partial leaks only through vector memory, leaky everywhere but all-off, clean never."""
    cid = fx.campaign(planned=32, design="FULL_FACTORIAL", alpha=0.05)
    fl = {r[0]: r[1:] for r in conn.execute("SELECT treatment_id, vector_memory_on, notes_table_on, cache_on FROM treatment WHERE campaign_id=%s", (cid,))}
    tids = fx.treatments(cid)
    pat = {"trader-leaky": lambda i, c: any(fl[tids[c]]) or i % 2 == 0, "trader-partial": lambda i, c: fl[tids[c]][0] or i % 3 == 0,
           "trader-clean": lambda i, c: i % 2 == 0}
    slots, truth = build(fx, conn, cid, 32, lambda a, i, c: pat[a](i, c))
    rows = verdict(conn, cid)
    assert {r["scope"] for r in rows} == {"AGENT", "CELL", "CHANNEL"} and len(rows) == 3 * (1 + 8 + 3)
    check_against_reference(rows, reference(conn, cid, slots, truth, 0.05))
    assert all(r["family_size"] == 11 for r in rows)                                   # 8 cells + 3 channels per agent
    ch = {(r["agent_name"], r["channel"]): r for r in rows if r["scope"] == "CHANNEL"}
    assert ch[("trader-partial", "vector_memory")]["n_correct"] > ch[("trader-partial", "notes_table")]["n_correct"]
    # a CELL / CHANNEL claim can never be LEAK unless its agent's gate passed
    gate = {r["agent_name"]: r["gate_passed"] for r in rows if r["scope"] == "AGENT"}
    assert all(gate[r["agent_name"]] for r in rows if r["scope"] != "AGENT" and r["verdict"] == "LEAK")


def test_gatekeeping_matches_an_independent_reimplementation_one_at_a_time(fx, conn):
    cid = fx.campaign(planned=40, design="ONE_AT_A_TIME", alpha=0.05)
    pat = {"trader-leaky": lambda i, c: True, "trader-partial": lambda i, c: c in (0, 1) or i % 3 == 0, "trader-clean": lambda i, c: i % 2 == 0}
    slots, truth = build(fx, conn, cid, 40, lambda a, i, c: pat[a](i, c))
    rows = verdict(conn, cid)
    check_against_reference(rows, reference(conn, cid, slots, truth, 0.05))
    # the v1 procedure (flat Holm over all treatment x agent cells) is still reported, for comparison
    cells = [r for r in rows if r["scope"] == "CELL"]
    ps = [stats.binom.sf(r["n_correct"] - 1, r["n_slots"], 0.5) for r in cells]
    assert [r["p_adj_flat_holm"] for r in cells] == pytest.approx(list(multipletests(ps, method="holm")[1]), rel=1e-9, abs=1e-15)
    fam = conn.execute("SELECT p_adj FROM family_verdict(0.05, %s::int[], %s::int[])", ([r["n_slots"] for r in cells], [r["n_correct"] for r in cells])).fetchall()
    assert [f[0] for f in fam] == pytest.approx([r["p_adj_flat_holm"] for r in cells], rel=1e-9, abs=1e-15)


def test_a_failed_gate_blocks_every_cell_claim(fx, conn):
    """5/5 correct in one cell has raw p = 1/32 <= 0.05, but the agent's pooled test (5/25) fails, so no cell of it is LEAK."""
    cid = fx.campaign(planned=25, design="ONE_AT_A_TIME", alpha=0.05)
    build(fx, conn, cid, 25, lambda a, i, c: a == "trader-leaky" and c == 0 or (i % 2 == 0 and a != "trader-leaky"))
    rows = [x for x in verdict(conn, cid) if x["agent_name"] == "trader-leaky"]
    agent = next(x for x in rows if x["scope"] == "AGENT")
    cell0 = next(x for x in rows if x["scope"] == "CELL" and x["n_correct"] == 5)
    assert not agent["gate_passed"] and agent["verdict"] == "NO_EVIDENCE"
    assert cell0["p_raw"] == pytest.approx(1 / 32) and cell0["p_raw"] <= 0.05
    assert cell0["p_adj"] >= agent["p_adj"] > 0.05 and cell0["verdict"] == "NO_EVIDENCE"   # raw would have said LEAK
    assert cell0["family_size"] == 8                                                       # 5 cells + 3 testable channels


def test_inference_can_be_replayed_at_an_earlier_slot_for_the_peeking_demonstration(fx, conn):
    cid = fx.campaign(planned=10)
    build(fx, conn, cid, 10, lambda a, i, c: a == "trader-leaky")
    early = dicts_as(conn, "audit_engine", "SELECT * FROM campaign_inference(%s, 6, true) WHERE scope='AGENT'", (cid,))
    assert all(r["n_slots"] == 6 and r["decidable"] for r in early)
    assert next(r for r in early if r["n_correct"] == 6)["p_raw"] == pytest.approx(0.5 ** 6)
    assert all(not r["decidable"] for r in dicts_as(conn, "audit_engine", "SELECT * FROM campaign_inference(%s, 6) WHERE scope='AGENT'", (cid,)))


# ----- SEQUENTIAL mode: anytime-valid --------------------------------------------------------------------------------------------
def e_value(n, k):
    return B(k + 1, n - k + 1) * stats.binom.cdf(k, n + 1, 0.5) / 0.5 ** (n + 1)


def test_sequential_inference_is_available_at_every_n_and_equals_the_mixture_e_value(fx, conn):
    cid = fx.campaign(planned=40, config={"inference": "SEQUENTIAL", "stop_rule": "FIRST_LEAK"})
    build(fx, conn, cid, 9, lambda a, i, c: a == "trader-leaky" or (a == "trader-partial" and i % 3 != 0))
    rows = {r["agent_name"]: r for r in verdict(conn, cid, "AGENT")}
    for name, r in rows.items():
        assert r["decidable"] and r["method"] == "ANYTIME_EVALUE_GATEKEEPING_v2" and r["n_slots"] == 9
        e = e_value(r["n_slots"], r["n_correct"])
        assert r["log_e"] == pytest.approx(float(np.log(e)), rel=1e-9, abs=1e-12)
        assert r["p_raw"] == pytest.approx(min(1.0, 1 / e), rel=1e-9)
        assert r["p_adj"] == pytest.approx(min(1.0, 3 / e), rel=1e-9)
        assert r["acc_lower"] <= r["accuracy"] <= r["acc_upper"]
        assert r["log10_p"] is None and r["min_detectable_acc"] is None              # fixed-n quantities do not apply
    assert rows["trader-leaky"]["verdict"] == "LEAK"                                    # 9/9: E = (1/10)(1 - 2^-10) 2^10 = 102.3 > 3/0.05
    assert rows["trader-leaky"]["p_raw"] == pytest.approx(10 / 1023, rel=1e-9)
    # an anytime-valid p is never smaller than the fixed-n exact p for the same data (the price of optional stopping)
    assert rows["trader-leaky"]["p_raw"] >= 0.5 ** 9


def test_a_sequential_campaign_can_be_frozen_before_its_planned_n(fx, conn):
    cid = fx.campaign(planned=40, config={"inference": "SEQUENTIAL", "stop_rule": "FIRST_LEAK"}, started_at=fx.BASE)
    ss, _ = build(fx, conn, cid, 10, lambda a, i, c: a == "trader-leaky")
    n = as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s,%s)", (cid, fx.engine_user, ss[-1]["end"]))
    assert n == [(6,)]
    assert conn.execute("SELECT DISTINCT method FROM audit_result WHERE campaign_id=%s", (cid,)).fetchall() == [("ANYTIME_EVALUE_GATEKEEPING_v2",)]


# ----- freeze: signed, append-only snapshot ----------------------------------------------------------------------------------------
def test_freeze_writes_audit_result_closes_the_campaign_and_is_tamper_evident(fx, conn):
    cid = fx.campaign(planned=12)
    build(fx, conn, cid, 12, lambda a, i, c: a == "trader-leaky" or i % 2 == 0)
    live = {(r["scope"], r["agent_name"]): r for r in verdict(conn, cid)}
    n = as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user))
    assert n == [(6,)]                                                       # 3 agents x (AGENT + 1 CELL)
    assert one(conn, "SELECT status FROM audit_campaign WHERE campaign_id=%s", (cid,)) == "CLOSED"
    rows = conn.execute("SELECT r.scope, a.agent_name, r.n_slots, r.n_correct, r.p_value, r.p_adjusted, r.verdict, r.acc_lower, r.acc_upper, r.family_size, "
                        "r.n_agents, r.gate_passed, r.clock_mode, r.method FROM audit_result r JOIN agent a ON a.agent_id=r.low_agent_id WHERE r.campaign_id=%s", (cid,)).fetchall()
    assert len(rows) == 6
    for sc, name, ns, k, p, padj, v, lo, hi, fam, na, gate, clk, meth in rows:
        L = live[(sc, name)]
        assert (ns, k, v, fam, na, gate, clk, meth) == (L["n_slots"], L["n_correct"], L["verdict"], 1, 3, L["gate_passed"], "SIMULATED", "FIXED_EXACT_GATEKEEPING_v2")
        assert p == pytest.approx(L["p_raw"]) and padj == pytest.approx(L["p_adj"]) and lo == pytest.approx(L["acc_lower"]) and hi == pytest.approx(L["acc_upper"])
    assert all(r[0] for r in conn.execute("SELECT hash_ok FROM v_result_integrity WHERE campaign_id=%s", (cid,)))
    snap, root = conn.execute("SELECT DISTINCT snapshot_sha256, evidence_root FROM audit_result WHERE campaign_id=%s", (cid,)).fetchall()[0]
    msg = one(conn, "SELECT snapshot_message_frozen(%s)", (cid,))
    assert hashlib.sha256(msg.encode()).hexdigest() == snap and msg.startswith(f"WALLTEST-SNAPSHOT-v2|{cid}|{root}|")
    assert msg == one(conn, "SELECT snapshot_message(%s)", (cid,))         # the frozen rows reproduce the live computation exactly
    expect_error(conn, "UPDATE audit_result SET n_correct=n_slots", sqlstate="WT001", role="walltest_owner")        # cannot be rewritten
    expect_error(conn, "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user), role="audit_engine")                  # cannot be frozen twice


def test_freeze_refuses_a_signature_over_a_different_snapshot(fx, conn):
    cid = fx.campaign(planned=4, started_at=fx.BASE)
    ss, _ = build(fx, conn, cid, 4, lambda a, i, c: True)
    as_role(conn, "audit_engine", "SELECT close_campaign_window(%s,%s)", (cid, ss[-1]["end"]))       # the engine's step 1
    e = expect_error(conn, "SELECT freeze_campaign(%s,%s,%s,%s,'sig','pk')", (cid, fx.engine_user, ss[-1]["end"], "0" * 64), sqlstate="WT013", role="audit_engine")
    assert "does not match" in str(e)
    assert one(conn, "SELECT count(*) FROM audit_result WHERE campaign_id=%s", (cid,)) == 0
    sha = hashlib.sha256(one(conn, "SELECT snapshot_message(%s)", (cid,)).encode()).hexdigest()
    assert as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s,%s,%s,'sig','pk')", (cid, fx.engine_user, ss[-1]["end"], sha)) == [(6,)]


def test_a_tampered_snapshot_fails_the_hash_check(fx, conn):
    cid = fx.campaign(planned=4)
    build(fx, conn, cid, 4, lambda a, i, c: True)
    as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user))
    conn.execute("ALTER TABLE audit_result DISABLE TRIGGER audit_result_append_only")       # an attacker with superuser power rewrites a verdict...
    conn.execute("UPDATE audit_result SET n_correct = 0, verdict='NO_EVIDENCE' WHERE campaign_id=%s", (cid,))
    conn.execute("ALTER TABLE audit_result ENABLE TRIGGER audit_result_append_only")
    assert not any(r[0] for r in conn.execute("SELECT hash_ok FROM v_result_integrity WHERE campaign_id=%s", (cid,)))     # ...and the snapshot hash exposes it


def test_model_comparison_view_counts_agent_level_verdicts(fx, conn):
    cid = fx.campaign(planned=12)
    build(fx, conn, cid, 12, lambda a, i, c: a == "trader-leaky")
    as_role(conn, "audit_engine", "SELECT freeze_campaign(%s,%s)", (cid, fx.engine_user))
    rows = {r[0]: r for r in conn.execute("SELECT model_name, n_results, n_leak FROM v_model_comparison")}
    assert rows["scripted-leaky-trader"][1:] == (1, 1) and rows["scripted-clean-trader"][2] == 0
