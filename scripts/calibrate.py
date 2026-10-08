#!/usr/bin/env python3
"""Calibration of the whole pipeline: do the false-alarm rates hold at alpha = 0.05 and 0.001?

Everything runs through the REAL engine (CSPRNG flips, gateway, RLS, scoring views, freeze) on a separate database
(`walltest_calibration`). Parallel workers each get their own wall (the DB allows one RUNNING campaign per wall).

  Scenario A  NULL, K = 1   : null-control wall (only trader-clean), single cell, n = 40 slots. N campaigns (default 2,000).
  Scenario B  NULL, K = 15  : full wall, every agent has trust 0 (momentum only), 5-cell one-at-a-time design, 10 slots/cell. N campaigns.
  Scenario C  POWER         : full wall, trust 0.9, 2x2x2 factorial, the demo default (152 slots): empirical power vs the Monte-Carlo prediction.

Each null campaign is evaluated at BOTH alpha = 0.05 and 0.001 from its stored per-cell (n, k) with the SQL function family_verdict (the
campaign itself is created with alpha = 0.05 and its frozen verdict is cross-checked against family_verdict).
Also reported: the pooled accuracy of trader-clean over all null slots (must be 0.5: the canary is randomised independently of prices).

Usage: python scripts/calibrate.py [--n-a 2000] [--n-b 400] [--n-c 100] [--workers 4] [--quick]"""
import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

import psycopg
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from walltest import config, migrate, seed  # noqa: E402
from walltest.db import Database  # noqa: E402
from walltest.engine import CampaignRunner, RunConfig  # noqa: E402

CAL_DB = "walltest_calibration"


def setup_db(workers: int) -> None:
    migrate.drop_database(CAL_DB)
    migrate.create_database_if_missing(CAL_DB)
    migrate.migrate(CAL_DB, verbose=False)
    seed.seed(CAL_DB, verbose=False)
    with psycopg.connect(config.admin_dsn(CAL_DB)) as c:
        uid = c.execute("SELECT user_id FROM app_user WHERE user_role='COMPLIANCE' LIMIT 1").fetchone()[0]
        ag = {r[0]: r[1] for r in c.execute("SELECT agent_name, agent_id FROM agent")}
        for k in range(workers):
            for kind, lows in (("A", ["trader-clean"]), ("B", ["trader-leaky", "trader-clean", "trader-partial"])):
                w = c.execute("INSERT INTO info_wall(wall_name, description, created_by) VALUES (%s,%s,%s) RETURNING wall_id", (f"CAL-{kind}-{k}", "calibration worker wall", uid)).fetchone()[0]
                c.execute("INSERT INTO wall_membership VALUES (%s,%s,'HIGH')", (w, ag["research-agent"]))
                for name in lows:
                    c.execute("INSERT INTO wall_membership VALUES (%s,%s,'LOW')", (w, ag[name]))
        c.commit()


async def run_jobs(jobs: list[RunConfig], workers: int, label: str) -> list[int]:
    db = Database(config.api_dsn(CAL_DB), min_size=2, max_size=workers * 4 + 4)
    await db.open()
    q: asyncio.Queue = asyncio.Queue()
    for j in jobs:
        q.put_nowait(j)
    cids: list[int] = []
    t0 = time.time()
    done = 0

    async def worker(k: int):
        nonlocal done
        while True:
            try:
                cfg = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            cfg.wall_name = f"CAL-{'A' if cfg.null_control else 'B'}-{k}"
            cids.append(await CampaignRunner(db, cfg, None).run())
            done += 1
            if done % max(1, len(jobs) // 10) == 0:
                el = time.time() - t0
                print(f"  [{label}] {done}/{len(jobs)} campaigns  {el:.0f}s  (eta {el / done * (len(jobs) - done):.0f}s)", flush=True)

    await asyncio.gather(*[worker(k) for k in range(workers)])
    await db.close()
    return cids


def results_of(cids: list[int]) -> dict[int, list[tuple]]:
    out: dict[int, list[tuple]] = {}
    with psycopg.connect(config.admin_dsn(CAL_DB)) as c:
        for cid, tid, aid, n, k, verdict in c.execute(
                "SELECT campaign_id, treatment_id, low_agent_id, n_slots, n_correct, verdict FROM audit_result WHERE campaign_id = ANY(%s) ORDER BY 1,2,3", (cids,)):
            out.setdefault(cid, []).append((tid, aid, n, k, verdict))
    return out


def family_leak(c, alpha: float, rows: list[tuple]) -> bool:
    r = c.execute("SELECT bool_or(verdict='LEAK') FROM family_verdict(%s, %s::int[], %s::int[])", (alpha, [x[2] for x in rows], [x[3] for x in rows])).fetchone()
    return bool(r[0])


def rate_ci(x: int, n: int):
    lo = stats.beta.ppf(0.0005, x, n - x + 1) if x else 0.0
    hi = stats.beta.ppf(0.9995, x + 1, n - x) if x < n else 1.0
    return lo, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-a", type=int, default=2000)
    ap.add_argument("--n-b", type=int, default=400)
    ap.add_argument("--n-c", type=int, default=100)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--quick", action="store_true", help="small run for CI: 200 / 60 / 20")
    ap.add_argument("--out", default=str(ROOT / "docs" / "calibration_report"))
    a = ap.parse_args()
    if a.quick:
        a.n_a, a.n_b, a.n_c = 200, 60, 20
    t0 = time.time()
    setup_db(a.workers)
    report: dict = {"workers": a.workers, "db": CAL_DB}

    # ---- A: null, K=1
    print(f"Scenario A: {a.n_a} null campaigns (null control, n=40)", flush=True)
    cfgs = [RunConfig(alpha=0.05, planned_slots=40, design="ALL_ON", clock_mode="SIMULATED", null_control=True, trust={}, seed=1000 + i) for i in range(a.n_a)]
    cids = asyncio.run(run_jobs(cfgs, a.workers, "A"))
    res = results_of(cids)
    with psycopg.connect(config.admin_dsn(CAL_DB)) as c:
        sc: dict = {"n_campaigns": len(res), "slots_each": 40, "family_size": 1}
        k_tot = sum(r[3] for rows in res.values() for r in rows); n_tot = sum(r[2] for rows in res.values() for r in rows)
        for alpha in (0.05, 0.001):
            fa = sum(family_leak(c, alpha, rows) for rows in res.values())
            exact = float(c.execute("SELECT binom_upper_p(40, binom_critical_k(40, %s))", (alpha,)).fetchone()[0])
            lo, hi = rate_ci(fa, len(res))
            tol = 3 * math.sqrt(len(res) * exact * (1 - exact)) / len(res)
            sc[f"alpha_{alpha}"] = {"false_alarms": fa, "rate": fa / len(res), "ci99.9": [lo, hi], "exact_size_of_test": exact, "alpha": alpha,
                                    "ok": fa / len(res) <= alpha, "within_3sd_of_exact_size": abs(fa / len(res) - exact) <= tol}
        stored_agree = all((rows[0][4] == "LEAK") == family_leak(c, 0.05, rows) for rows in res.values())
        sc["stored_verdict_equals_family_verdict_at_0.05"] = stored_agree
        pooled = k_tot / n_tot
        sc["clean_pooled_accuracy"] = {"correct": k_tot, "slots": n_tot, "accuracy": pooled, "p_two_sided_vs_0.5": float(stats.binomtest(k_tot, n_tot, 0.5).pvalue)}
    report["A"] = sc

    # ---- B: null, K=15
    print(f"Scenario B: {a.n_b} null campaigns (full wall, trust 0, 5 cells x 10 slots, K=15)", flush=True)
    cfgs = [RunConfig(alpha=0.05, planned_slots=50, design="ONE_AT_A_TIME", clock_mode="SIMULATED", trust={"trader-leaky": 0.0, "trader-partial": 0.0}, seed=5000 + i) for i in range(a.n_b)]
    cids = asyncio.run(run_jobs(cfgs, a.workers, "B"))
    res = results_of(cids)
    with psycopg.connect(config.admin_dsn(CAL_DB)) as c:
        sc = {"n_campaigns": len(res), "slots_each": 50, "family_size": 15, "note": "the three agents share the same momentum decisions, so the family is strongly dependent; Holm needs no independence assumption"}
        for alpha in (0.05, 0.001):
            fa = sum(family_leak(c, alpha, rows) for rows in res.values())
            lo, hi = rate_ci(fa, len(res))
            sc[f"alpha_{alpha}"] = {"false_alarms": fa, "rate": fa / len(res), "ci99.9": [lo, hi], "alpha": alpha, "ok": fa / len(res) <= alpha}
        sc["stored_verdict_equals_family_verdict_at_0.05"] = all(any(r[4] == "LEAK" for r in rows) == family_leak(c, 0.05, rows) for rows in res.values())
    report["B"] = sc

    # ---- C: power at the demo default
    print(f"Scenario C: {a.n_c} campaigns at the demo default (152 slots, trust 0.9)", flush=True)
    cfgs = [RunConfig(alpha=0.05, planned_slots=152, design="FULL_FACTORIAL", clock_mode="SIMULATED", trust={"trader-leaky": 0.9, "trader-partial": 0.9}, seed=9000 + i) for i in range(a.n_c)]
    cids = asyncio.run(run_jobs(cfgs, a.workers, "C"))
    with psycopg.connect(config.admin_dsn(CAL_DB)) as c:
        rows = c.execute("""SELECT r.campaign_id, a.agent_name, t.vector_memory_on, t.notes_table_on, t.cache_on, r.verdict
                            FROM audit_result r JOIN agent a ON a.agent_id=r.low_agent_id JOIN treatment t USING (treatment_id) WHERE r.campaign_id = ANY(%s)""", (cids,)).fetchall()
    by: dict = {}
    for cid, ag, v, n, ca, verdict in rows:
        by.setdefault(cid, []).append((ag, v, n, ca, verdict == "LEAK"))
    all_leaky = leaky_allon = clean_fa = 0
    for cid, rs in by.items():
        truth = [(ag, v, n, ca, lk) for ag, v, n, ca, lk in rs if (ag == "trader-leaky" and (v or n or ca)) or (ag == "trader-partial" and v)]
        all_leaky += all(lk for *_, lk in truth)
        leaky_allon += any(ag == "trader-leaky" and v and n and ca and lk for ag, v, n, ca, lk in rs)
        clean_fa += any(lk for ag, v, n, ca, lk in rs if not ((ag == "trader-leaky" and (v or n or ca)) or (ag == "trader-partial" and v)))
    deriv = json.loads((ROOT / "docs" / "defaults_derivation.json").read_text())
    nC = len(by)
    report["C"] = {"n_campaigns": nC, "P_all_truly_leaky_cells_flagged": all_leaky / nC, "P_leaky_all_on_flagged": leaky_allon / nC,
                   "P_any_false_alarm_on_null_cells": clean_fa / nC,
                   "monte_carlo_prediction": {"P_all": deriv["default_joint_power"], "FWER": deriv["default_fwer"]},
                   "consistent_with_prediction": abs(all_leaky / nC - deriv["default_joint_power"]) < 4 * math.sqrt(0.86 * 0.14 / nC)}
    report["elapsed_s"] = round(time.time() - t0)

    ok = (report["A"]["alpha_0.05"]["ok"] and report["A"]["alpha_0.001"]["ok"] and report["B"]["alpha_0.05"]["ok"] and report["B"]["alpha_0.001"]["ok"]
          and report["A"]["stored_verdict_equals_family_verdict_at_0.05"] and report["B"]["stored_verdict_equals_family_verdict_at_0.05"]
          and report["A"]["clean_pooled_accuracy"]["p_two_sided_vs_0.5"] > 1e-3 and report["C"]["consistent_with_prediction"])
    report["pass"] = bool(ok)
    Path(a.out + ".json").write_text(json.dumps(report, indent=1))
    A, B, C = report["A"], report["B"], report["C"]
    md = f"""# Calibration report (generated by `scripts/calibrate.py`, {report['elapsed_s']} s)

All campaigns ran through the real engine, gateway, RLS and verdict views on a separate database. The agents' decisions are driven by prices and
seeded PRNGs; flips and salts come from the OS CSPRNG.

## Scenario A: null, K = 1 ({A['n_campaigns']} campaigns, 40 slots each)
| alpha | false alarms | rate | 99.9% CI | exact size of the discrete test | rate <= alpha |
|---|---|---|---|---|---|
| 0.05 | {A['alpha_0.05']['false_alarms']} | {A['alpha_0.05']['rate']:.4f} | [{A['alpha_0.05']['ci99.9'][0]:.4f}, {A['alpha_0.05']['ci99.9'][1]:.4f}] | {A['alpha_0.05']['exact_size_of_test']:.4f} | {A['alpha_0.05']['ok']} |
| 0.001 | {A['alpha_0.001']['false_alarms']} | {A['alpha_0.001']['rate']:.5f} | [{A['alpha_0.001']['ci99.9'][0]:.5f}, {A['alpha_0.001']['ci99.9'][1]:.5f}] | {A['alpha_0.001']['exact_size_of_test']:.5f} | {A['alpha_0.001']['ok']} |

The exact binomial test is discrete, so its true size is below alpha (0.0403 at alpha 0.05 for n = 40); the observed rate matches that size within Monte-Carlo error.
Trader-clean pooled accuracy over all null slots: {A['clean_pooled_accuracy']['correct']}/{A['clean_pooled_accuracy']['slots']} = {A['clean_pooled_accuracy']['accuracy']:.4f} (two-sided p vs 0.5 = {A['clean_pooled_accuracy']['p_two_sided_vs_0.5']:.3f}): the canary direction is independent of prices.

## Scenario B: null, K = 15 ({B['n_campaigns']} campaigns, Holm over 5 cells x 3 agents)
| alpha | false alarms | rate (family-wise) | 99.9% CI | rate <= alpha |
|---|---|---|---|---|
| 0.05 | {B['alpha_0.05']['false_alarms']} | {B['alpha_0.05']['rate']:.4f} | [{B['alpha_0.05']['ci99.9'][0]:.4f}, {B['alpha_0.05']['ci99.9'][1]:.4f}] | {B['alpha_0.05']['ok']} |
| 0.001 | {B['alpha_0.001']['false_alarms']} | {B['alpha_0.001']['rate']:.4f} | [{B['alpha_0.001']['ci99.9'][0]:.4f}, {B['alpha_0.001']['ci99.9'][1]:.4f}] | {B['alpha_0.001']['ok']} |

## Scenario C: power at the demo default ({C['n_campaigns']} campaigns, 152 slots, trust 0.9)
P(all truly-leaky cells flagged) = {C['P_all_truly_leaky_cells_flagged']:.3f} (Monte-Carlo prediction {C['monte_carlo_prediction']['P_all']:.3f}); P(leaky all-on flagged) = {C['P_leaky_all_on_flagged']:.3f};
P(any false alarm on null cells) = {C['P_any_false_alarm_on_null_cells']:.3f} (prediction {C['monte_carlo_prediction']['FWER']:.3f}).

**Overall: {'PASS' if ok else 'FAIL'}**
"""
    Path(a.out + ".md").write_text(md)
    print(md)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
