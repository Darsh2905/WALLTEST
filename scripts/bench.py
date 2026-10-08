#!/usr/bin/env python3
"""Reproducible performance benchmark. Writes docs/benchmarks/<label>.json and prints a table.

  A. Engine throughput: simulated-clock campaigns (2x2x2 factorial, 152 slots) on a fresh database, concurrency 1 and 8.
  B. Query latency at scale on the calibration database (~1M access events, ~1.5M orders, ~400k notes, 115k slots):
     server-side execution time from EXPLAIN (ANALYZE), median of 5, run as the audit_engine role like the API does.
  C. Statistical kernels (SQL functions) at the proposal's scale.

Usage: python scripts/bench.py --label v1-baseline      (needs `docker compose up -d db`; scenario B needs `make calibrate` once)
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from walltest import config, migrate, seed  # noqa: E402
from walltest.db import Database  # noqa: E402
from walltest.engine import CampaignRunner, RunConfig  # noqa: E402

BENCH_DB = "walltest_bench"
BIG_DB = "walltest_calibration"


def engine_throughput() -> dict:
    migrate.drop_database(BENCH_DB)
    migrate.create_database_if_missing(BENCH_DB)
    migrate.migrate(BENCH_DB, verbose=False)
    seed.seed(BENCH_DB, verbose=False)
    out = {}

    async def run(conc: int) -> float:
        db = Database(config.api_dsn(BENCH_DB), min_size=2, max_size=40)
        await db.open()
        t = time.perf_counter()
        await CampaignRunner(db, RunConfig(alpha=0.05, planned_slots=152, design="FULL_FACTORIAL", clock_mode="SIMULATED", seed=1,
                                           trust={"trader-leaky": 0.9, "trader-partial": 0.9}, concurrency=conc), None).run()
        dt = time.perf_counter() - t
        await db.close()
        return 152 / dt

    for conc in (1, 8):
        rates = [asyncio.run(run(conc)) for _ in range(2)]
        out[f"sim_slots_per_s_conc{conc}"] = round(max(rates), 1)
    return out


def explain_ms(conn, sql: str, params=None, reps: int = 5) -> float:
    times = []
    for _ in range(reps):
        with conn.transaction():
            conn.execute("SET LOCAL ROLE audit_engine")
            plan = conn.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params).fetchone()[0]
            conn.execute("RESET ROLE")
        times.append(plan[0]["Execution Time"] + plan[0].get("Planning Time", 0))
    return round(statistics.median(times), 2)


def query_latency() -> dict:
    out = {}
    try:
        conn = psycopg.connect(config.admin_dsn(BIG_DB), autocommit=False)
    except Exception as e:  # noqa: BLE001
        return {"skipped": f"no {BIG_DB}: {e}"}
    with conn:
        cid = conn.execute("SELECT max(campaign_id) FROM audit_campaign WHERE status='CLOSED'").fetchone()[0]
        sid = conn.execute("SELECT max(slot_id) FROM canary_slot WHERE campaign_id=%s", (cid,)).fetchone()[0]
        t0, t1 = conn.execute("SELECT min(lower(slot_period)), max(upper(slot_period)) FROM canary_slot WHERE campaign_id=%s", (cid,)).fetchone()
        vec = conn.execute("SELECT embedding::text FROM agent_note WHERE embedding IS NOT NULL ORDER BY note_id DESC LIMIT 1").fetchone()[0]
        out["rows"] = {r[0]: r[1] for r in conn.execute("SELECT table_name, row_count FROM schema_row_counts()") if r[1] > 10000}
        out["v_verdict_one_campaign_ms"] = explain_ms(conn, "SELECT * FROM v_verdict WHERE campaign_id = %s", (cid,))
        out["v_slot_score_one_slot_ms"] = explain_ms(conn, "SELECT * FROM v_slot_score WHERE slot_id = %s", (sid,))
        out["v_progress_one_campaign_ms"] = explain_ms(conn, "SELECT * FROM v_progress WHERE campaign_id = %s", (cid,))
        out["exposure_trail_ms"] = explain_ms(conn, "SELECT * FROM exposure_trail(%s)", (sid,))
        out["v_lag_one_campaign_ms"] = explain_ms(conn, "SELECT * FROM v_lag WHERE campaign_id = %s", (cid,))
        out["access_event_time_window_count_ms"] = explain_ms(conn, "SELECT count(*) FROM access_event WHERE event_time >= %s AND event_time < %s", (t0, t1))
        out["knn_all_vector_memory_ms"] = explain_ms(conn, "SELECT note_id FROM agent_note WHERE asset_id = (SELECT asset_id FROM data_asset WHERE asset_name='vector_memory') AND embedding IS NOT NULL ORDER BY embedding <=> %s::vector LIMIT 3", (vec,))
        v2 = conn.execute("SELECT to_regproc('evidence_root') IS NOT NULL, to_regproc('semantic_search') IS NOT NULL").fetchone()
        if v2[0]:
            out["v_wall_verdict_one_campaign_ms"] = explain_ms(conn, "SELECT * FROM v_wall_verdict WHERE campaign_id = %s", (cid,))
            out["evidence_root_one_campaign_ms"] = explain_ms(conn, "SELECT * FROM evidence_root(%s)", (cid,))
        if v2[1]:
            out["semantic_search_ms"] = explain_ms(conn, "SELECT * FROM semantic_search(%s::vector, 10)", (vec,))
            out["semantic_search_bruteforce_ms"] = explain_ms(conn, "SELECT * FROM semantic_search_exact(%s::vector, 10)", (vec,))
            # semantic_search is exact by design: check it equals brute force row for row on 25 real canary texts
            same = 0
            qs = [r[0] for r in conn.execute("SELECT content FROM canary_variant ORDER BY md5(variant_id::text) LIMIT 25")]
            from walltest.embedding import embed, to_pgvector
            for t in qs:
                q = to_pgvector(embed(t))
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE audit_engine")
                    a_ = [r[0] for r in conn.execute("SELECT note_id FROM semantic_search(%s::vector, 10)", (q,))]
                    e_ = [r[0] for r in conn.execute("SELECT note_id FROM semantic_search_exact(%s::vector, 10)", (q,))]
                same += a_ == e_
            out["semantic_search_equals_bruteforce"] = f"{same}/{len(qs)}"
    return out


def kernels() -> dict:
    out = {}
    with psycopg.connect(config.admin_dsn(BENCH_DB)) as c:
        for name, sql in {"binom_upper_p_1543": "SELECT binom_upper_p(1543, 830)",
                          "clopper_pearson_lower_1543": "SELECT clopper_pearson_lower(1543, 830, 0.001)",
                          "min_detectable_acc_1500": "SELECT min_detectable_acc(1500, 0.001)",
                          "n_required_exact_055": "SELECT n_required_exact(0.55, 0.001)",
                          **({"clopper_pearson_upper_1543": "SELECT clopper_pearson_upper(1543, 830, 0.001)",
                              "cs_lower_1543": "SELECT cs_lower(1543, 830, 0.001)",
                              "log_evalue_mix_1543": "SELECT log_evalue_mix(1543, 830)"}
                             if c.execute("SELECT to_regproc('cs_lower') IS NOT NULL").fetchone()[0] else {})}.items():
            ts = []
            for _ in range(5):
                t = time.perf_counter(); c.execute(sql).fetchone(); ts.append((time.perf_counter() - t) * 1000)
            out[name + "_ms"] = round(statistics.median(ts), 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--skip-engine", action="store_true")
    a = ap.parse_args()
    res = {"label": a.label, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not a.skip_engine:
        res["engine"] = engine_throughput()
    res["queries"] = query_latency()
    res["kernels"] = kernels()
    d = ROOT / "docs" / "benchmarks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{a.label}.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
