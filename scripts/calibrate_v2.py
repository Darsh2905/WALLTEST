#!/usr/bin/env python3
"""Calibration of the v2 inference (docs/METHODS.md) through the REAL engine: gateway, per-agent logins, RLS, scoring views,
signed freeze. Separate database (`walltest_calibration_v2`); each parallel worker owns a wall (one RUNNING campaign per wall).

  N  NULL, every claim     full wall, all three LOW agents leak-free (trust 0), 2x2x2 factorial, 64 slots, FIXED inference.
                           Family-wise false-alarm rate over ALL hypotheses: 3 agent gates + 24 cells + 9 channel tests.
  S  NULL, sequential      full wall, trust 0, ONE cell (a stop check after EVERY slot), up to 320 slots, SEQUENTIAL FIRST_LEAK:
                           the engine really stops at the first flag. Compared with naive peeking on the SAME slot data: an exact
                           fixed-n test after every slot at alpha/n_agents, stopping at the first rejection.
  P  POWER, v1 vs v2       2x2x2 factorial, 152 slots, FIXED: strong leak (trust 0.9) and weak leak (trust 0.2). The SAME campaigns
                           scored by v2 (agent gate) and by the v1 procedure (flat Holm over all 24 cells; agent detected if any of
                           its cells is flagged). Channel attribution: the sign test should flag trader-partial's vector memory only.
  T  TIME TO DETECTION     2x2x2 factorial, up to 400 slots, trust 0.4, SEQUENTIAL FIRST_LEAK: slots used until the stop vs the
                           fixed n a fixed-n test needs for 80% power at the same level.

Writes docs/benchmarks/calibration-v2.json and docs/calibration_v2_report.md.
Usage: python scripts/calibrate_v2.py [--n-null 600] [--n-seq 300] [--n-power 200] [--n-time 150] [--workers 4] [--quick]
"""
import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from scipy import stats
from statsmodels.stats.multitest import multipletests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from walltest import config, migrate, seed  # noqa: E402
from walltest.db import Database  # noqa: E402
from walltest.engine import CampaignRunner, RunConfig  # noqa: E402

DB = "walltest_calibration_v2"
LOWS = ["trader-clean", "trader-leaky", "trader-partial"]


def setup_db(workers: int) -> None:
    migrate.drop_database(DB)
    migrate.create_database_if_missing(DB)
    migrate.migrate(DB, verbose=False)
    seed.seed(DB, verbose=False)
    with psycopg.connect(config.admin_dsn(DB)) as c:
        uid = c.execute("SELECT user_id FROM app_user WHERE user_role='COMPLIANCE' LIMIT 1").fetchone()[0]
        ag = {r[0]: r[1] for r in c.execute("SELECT agent_name, agent_id FROM agent")}
        for k in range(workers):
            w = c.execute("INSERT INTO info_wall(wall_name, description, created_by) VALUES (%s,'calibration worker wall',%s) RETURNING wall_id",
                          (f"CAL2-{k}", uid)).fetchone()[0]
            c.execute("INSERT INTO wall_membership VALUES (%s,%s,'HIGH')", (w, ag["research-agent"]))
            for name in LOWS:
                c.execute("INSERT INTO wall_membership VALUES (%s,%s,'LOW')", (w, ag[name]))
        c.commit()


async def run_jobs(jobs: list[RunConfig], workers: int, label: str) -> list[int]:
    db = Database(config.api_dsn(DB), min_size=2, max_size=workers * 4 + 4)
    await db.open()
    q: asyncio.Queue = asyncio.Queue()
    for j in jobs:
        q.put_nowait(j)
    cids: list[int] = []
    t0, done = time.time(), 0

    async def worker(k: int):
        nonlocal done
        while not q.empty():
            cfg = q.get_nowait()
            cfg.wall_name = f"CAL2-{k}"
            cids.append(await CampaignRunner(db, cfg, None).run())
            done += 1
            if done % max(1, len(jobs) // 10) == 0:
                el = time.time() - t0
                print(f"  [{label}] {done}/{len(jobs)}  {el:.0f}s  (eta {el / done * (len(jobs) - done):.0f}s)", flush=True)

    await asyncio.gather(*[worker(k) for k in range(workers)])
    await db.close()
    return cids


def rate(x: int, n: int, alpha: float | None = None) -> dict:
    lo = stats.beta.ppf(0.025, x, n - x + 1) if x else 0.0
    hi = stats.beta.ppf(0.975, x + 1, n - x) if x < n else 1.0
    d = {"x": x, "n": n, "rate": x / n, "ci95": [lo, hi]}
    if alpha is not None:
        d["alpha"] = alpha
        d["ok"] = lo <= alpha               # consistent with FWER <= alpha (the CI's lower end does not exceed alpha)
    return d


def frozen(c, cids):
    return c.execute("""SELECT r.campaign_id, r.scope, a.agent_name, r.treatment_id, r.channel, r.n_slots, r.n_correct, r.p_value,
                               r.p_adjusted, r.verdict, t.vector_memory_on, t.notes_table_on, t.cache_on
                        FROM audit_result r JOIN agent a ON a.agent_id = r.low_agent_id LEFT JOIN treatment t USING (treatment_id)
                        WHERE r.campaign_id = ANY(%s)""", (cids,)).fetchall()


def trajectories(c, cid) -> dict[str, list[int]]:
    """Per agent: correct (0/1) per scored slot in time order, inside the campaign's window."""
    rows = c.execute("""SELECT a.agent_name, s.correct FROM v_slot_score s JOIN canary_slot cs USING (slot_id) JOIN agent a ON a.agent_id = s.low_agent_id
                        WHERE s.campaign_id = %s ORDER BY lower(cs.slot_period)""", (cid,)).fetchall()
    out: dict[str, list[int]] = {}
    for name, ok in rows:
        out.setdefault(name, []).append(int(ok))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-null", type=int, default=600)
    ap.add_argument("--n-seq", type=int, default=300)
    ap.add_argument("--n-power", type=int, default=200)
    ap.add_argument("--n-time", type=int, default=150)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    if a.quick:
        a.n_null, a.n_seq, a.n_power, a.n_time = 60, 30, 20, 20
    t0 = time.time()
    setup_db(a.workers)
    rep: dict = {"label": "calibration-v2", "when": time.strftime("%Y-%m-%d %H:%M:%S"), "workers": a.workers, "db": DB}
    zero = {"trader-leaky": 0.0, "trader-partial": 0.0}

    # ---- N: null, every claim, FIXED -----------------------------------------------------------------------------------------
    print(f"N: {a.n_null} null campaigns, 64-slot factorial, FIXED (27 + 9 hypotheses each)", flush=True)
    cids = asyncio.run(run_jobs([RunConfig(alpha=0.05, planned_slots=64, design="FULL_FACTORIAL", clock_mode="SIMULATED", trust=zero,
                                           seed=10_000 + i) for i in range(a.n_null)], a.workers, "N"))
    with psycopg.connect(config.admin_dsn(DB)) as c:
        rows = frozen(c, cids)
    by: dict = {}
    for r in rows:
        by.setdefault(r[0], []).append(r)
    N = {"n_campaigns": len(by), "hypotheses_per_campaign": int(np.mean([len(v) for v in by.values()]))}
    for alpha in (0.05, 0.01, 0.001):
        N[f"fwer_all_claims_alpha_{alpha}"] = rate(sum(any(r[8] <= alpha for r in v) for v in by.values()), len(by), alpha)
        N[f"fwer_agent_gates_alpha_{alpha}"] = rate(sum(any(r[8] <= alpha for r in v if r[1] == "AGENT") for v in by.values()), len(by), alpha)
    N["stored_verdicts_match_p_adjusted_at_0.05"] = all((r[9] == "LEAK") == (r[8] <= 0.05) for r in rows)
    N["unadjusted_any_cell_p_below_0.05"] = rate(sum(any(r[7] <= 0.05 for r in v if r[1] == "CELL") for v in by.values()), len(by))
    rep["null_all_claims"] = N

    # ---- S: null, sequential, a stop check after every slot ------------------------------------------------------------------
    print(f"S: {a.n_seq} null campaigns, 1 cell, up to 320 slots, SEQUENTIAL FIRST_LEAK", flush=True)
    cids = asyncio.run(run_jobs([RunConfig(alpha=0.05, planned_slots=320, design="ALL_ON", clock_mode="SIMULATED", trust=zero,
                                           inference="SEQUENTIAL", stop_rule="FIRST_LEAK", min_blocks=2, seed=20_000 + i)
                                 for i in range(a.n_seq)], a.workers, "S"))
    alpha, n_agents = 0.05, 3
    with psycopg.connect(config.admin_dsn(DB)) as c:
        rows = frozen(c, cids)
        seq_flag = {cid: False for cid in cids}
        stop_n = {}
        for r in rows:
            if r[1] == "AGENT":
                seq_flag[r[0]] |= r[9] == "LEAK"
                stop_n[r[0]] = r[5]
        naive = 0
        naive_ok_superset = True
        for cid in cids:
            tr = trajectories(c, cid)
            hit = False
            for name, xs in tr.items():
                k = np.cumsum(xs)
                n = np.arange(1, len(xs) + 1)
                p = stats.binom.sf(k - 1, n, 0.5)
                hit |= bool((p[1:] <= alpha / n_agents).any())        # every look from the 2nd slot on, like the engine
            naive += hit
            naive_ok_superset &= hit or not seq_flag[cid]
    S = {"n_campaigns": len(cids), "max_slots": 320, "looks": "after every slot", "stopped_early": sum(1 for v in stop_n.values() if v < 320),
         "rows": [dict(procedure="anytime e-values, engine stops at first flag (v2)", **rate(sum(seq_flag.values()), len(cids), alpha)),
                  dict(procedure="naive: exact fixed-n test after every slot", **rate(naive, len(cids), alpha))],
         "naive_flags_every_campaign_the_e_values_flag": naive_ok_superset}
    S["rows"] = [{**r, "ci": r["ci95"]} for r in S["rows"]]
    rep["sequential"] = S

    # ---- P: power, v1 vs v2 procedures on the same campaigns ------------------------------------------------------------------
    P = {}
    for label, trust in (("strong (trust 0.9: accuracy 0.95 when exposed)", 0.9), ("weak (trust 0.2: accuracy 0.60 when exposed)", 0.2)):
        print(f"P: {a.n_power} campaigns, 152-slot factorial, {label}", flush=True)
        cids = asyncio.run(run_jobs([RunConfig(alpha=0.05, planned_slots=152, design="FULL_FACTORIAL", clock_mode="SIMULATED",
                                               trust={"trader-leaky": trust, "trader-partial": trust}, seed=30_000 + int(trust * 100) * 1000 + i)
                                     for i in range(a.n_power)], a.workers, "P"))
        with psycopg.connect(config.admin_dsn(DB)) as c:
            rows = frozen(c, cids)
        by = {}
        for r in rows:
            by.setdefault(r[0], []).append(r)
        res = {"n_campaigns": len(by)}
        for ag in LOWS:
            v2 = sum(any(r[1] == "AGENT" and r[2] == ag and r[9] == "LEAK" for r in v) for v in by.values())
            v1 = 0
            for v in by.values():                                    # v1: flat Holm over every (cell x agent), agent flagged if any cell is
                cells = [r for r in v if r[1] == "CELL"]
                adj = multipletests([r[7] for r in cells], method="holm")[1]
                v1 += any(x <= 0.05 and r[2] == ag for x, r in zip(adj, cells))
            res[ag] = {"v2_agent_gate": rate(v2, len(by)), "v1_any_cell_flat_holm": rate(v1, len(by))}
        attr = {}
        for ag, ch in (("trader-partial", "vector_memory"), ("trader-partial", "notes_table"), ("trader-partial", "cache"),
                       ("trader-leaky", "vector_memory"), ("trader-leaky", "notes_table"), ("trader-leaky", "cache")):
            attr[f"{ag}:{ch}"] = rate(sum(any(r[1] == "CHANNEL" and r[2] == ag and r[4] == ch and r[9] == "LEAK" for r in v) for v in by.values()), len(by))
        res["channel_flags"] = attr
        res["false_flags_on_trader_clean"] = rate(sum(any(r[2] == "trader-clean" and r[9] == "LEAK" for r in v) for v in by.values()), len(by))
        P[label] = res
    rep["power"] = P

    # ---- T: slots to detection under sequential stopping ----------------------------------------------------------------------
    print(f"T: {a.n_time} campaigns, factorial up to 400 slots, trust 0.4, SEQUENTIAL FIRST_LEAK", flush=True)
    cids = asyncio.run(run_jobs([RunConfig(alpha=0.05, planned_slots=400, design="FULL_FACTORIAL", clock_mode="SIMULATED",
                                           trust={"trader-leaky": 0.4, "trader-partial": 0.4}, inference="SEQUENTIAL", stop_rule="FIRST_LEAK",
                                           seed=40_000 + i) for i in range(a.n_time)], a.workers, "T"))
    with psycopg.connect(config.admin_dsn(DB)) as c:
        rows = frozen(c, cids)
        n_fixed = c.execute("SELECT n_required_exact(%s, %s, 0.8)", (0.5 + 0.4 / 2 * 7 / 8, 0.05 / 3)).fetchone()[0]
    stops = sorted({r[0]: r[5] for r in rows if r[1] == "AGENT"}.values())
    detected = sum(any(r[1] == "AGENT" and r[2] == "trader-leaky" and r[9] == "LEAK" for r in rows if r[0] == cid) for cid in cids)
    rep["time_to_detection"] = {"n_campaigns": len(cids), "leaky_accuracy_pooled": 0.5 + 0.4 / 2 * 7 / 8,
                                "slots_used": {"median": float(np.median(stops)), "p10": float(np.percentile(stops, 10)), "p90": float(np.percentile(stops, 90)),
                                               "max_planned": 400},
                                "detected_trader_leaky": rate(detected, len(cids)),
                                "fixed_n_for_80pct_power_same_level": n_fixed}
    rep["elapsed_s"] = round(time.time() - t0)
    ok = (all(rep["null_all_claims"][f"fwer_all_claims_alpha_{al}"]["ok"] for al in (0.05, 0.01, 0.001))
          and rep["null_all_claims"]["stored_verdicts_match_p_adjusted_at_0.05"] and rep["sequential"]["rows"][0]["ok"]
          and rep["sequential"]["naive_flags_every_campaign_the_e_values_flag"])
    rep["pass"] = bool(ok)
    (ROOT / "docs" / "benchmarks" / "calibration-v2.json").write_text(json.dumps(rep, indent=1, default=float))
    write_report(rep)
    print(json.dumps({k: rep[k] for k in ("pass", "elapsed_s")}))
    sys.exit(0 if ok else 1)


def pct(d):
    return f"{d['rate'] * 100:.1f}% [{d['ci95'][0] * 100:.1f}, {d['ci95'][1] * 100:.1f}]"


def write_report(r: dict) -> None:
    N, S, P, T = r["null_all_claims"], r["sequential"], r["power"], r["time_to_detection"]
    lines = [f"# Calibration of the v2 inference (generated by `scripts/calibrate_v2.py`, {r['elapsed_s']} s)", "",
             "Every campaign ran through the real engine (per-agent logins, gateway, RLS, scoring views, signed freeze) on a separate database.",
             "Rates are shown with exact 95% Clopper-Pearson intervals.", "",
             f"## N. No leak anywhere: family-wise false alarms over every claim ({N['n_campaigns']} campaigns, {N['hypotheses_per_campaign']} hypotheses each)", "",
             "| alpha | any false LEAK (all claims) | any false LEAK (agent gates only) | FWER <= alpha |", "|---|---|---|---|"]
    for al in (0.05, 0.01, 0.001):
        lines.append(f"| {al} | {pct(N[f'fwer_all_claims_alpha_{al}'])} | {pct(N[f'fwer_agent_gates_alpha_{al}'])} | {N[f'fwer_all_claims_alpha_{al}']['ok']} |")
    lines += ["", f"Without any multiplicity control, some cell would show p <= 0.05 in {pct(N['unadjusted_any_cell_p_below_0.05'])} of these leak-free campaigns.", "",
              f"## S. No leak, watched after every slot ({S['n_campaigns']} campaigns, up to {S['max_slots']} slots, {S['stopped_early']} stopped early)", "",
              "| procedure | false LEAK rate | nominal alpha |", "|---|---|---|"]
    for row in S["rows"]:
        lines.append(f"| {row['procedure']} | {pct(row)} | {row['alpha']} |")
    lines += ["", "Naive peeking flags every campaign the e-values flag, and many more: " + str(S["naive_flags_every_campaign_the_e_values_flag"]) + ".", "",
              "## P. Power: the same campaigns scored by the v2 and the v1 procedure (152 slots, 2x2x2)", ""]
    for label, res in P.items():
        lines += [f"### {label} ({res['n_campaigns']} campaigns)", "", "| agent | v2 agent gate | v1: any cell, flat Holm over 24 |", "|---|---|---|"]
        for ag in LOWS:
            lines.append(f"| {ag} | {pct(res[ag]['v2_agent_gate'])} | {pct(res[ag]['v1_any_cell_flat_holm'])} |")
        lines += ["", "| channel test (matched pairs) | flagged |", "|---|---|"] + [f"| {k} | {pct(v)} |" for k, v in res["channel_flags"].items()]
        lines += ["", f"Any false LEAK on trader-clean: {pct(res['false_flags_on_trader_clean'])}.", ""]
    lines += [f"## T. Slots to detection, sequential, trust 0.4 ({T['n_campaigns']} campaigns)", "",
              f"Slots used: median {T['slots_used']['median']:.0f} (10th-90th percentile {T['slots_used']['p10']:.0f}-{T['slots_used']['p90']:.0f}) of up to {T['slots_used']['max_planned']}; "
              f"trader-leaky detected in {pct(T['detected_trader_leaky'])}. A fixed-n exact test at the same level needs n = {T['fixed_n_for_80pct_power_same_level']} for 80% power.", "",
              f"**Overall: {'PASS' if r['pass'] else 'FAIL'}**", ""]
    (ROOT / "docs" / "calibration_v2_report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
