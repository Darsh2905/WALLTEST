#!/usr/bin/env python3
"""Reproducible studies behind two REJECTED index designs (migration 016, DEVIATIONS D-26/D-27). Every candidate index is
built inside a transaction that is rolled back, on the calibration database (~1M access events, ~200k embedded notes).

  1. Approximate vector search (HNSW). Distance-based recall@10 against brute force -- a result counts if it is at least as
     close as the true 10th neighbour, so ties (identical vectors are common) cannot distort recall -- for realistic queries
     (canary texts the research agent reads) and for stored vectors, over ALL rows and over DISTINCT vectors only.
  2. Time-window indexes on access_event: no index vs BRIN vs B-tree, on the three queries that use a time window.

Writes docs/benchmarks/index-studies.json.  Usage: python scripts/index_studies.py   (needs `make calibrate` once)
"""
import json
import statistics
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from walltest import config  # noqa: E402
from walltest.embedding import embed, to_pgvector  # noqa: E402

DB = "walltest_calibration"
EFS = (40, 100, 400, 1000)


def explain_ms(c, sql, params, reps=5):
    ts = []
    for _ in range(reps):
        plan = c.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params).fetchone()[0][0]
        ts.append(plan["Execution Time"] + plan["Planning Time"])
    return round(statistics.median(ts), 2)


def vector_study(c) -> dict:
    texts = [r[0] for r in c.execute("SELECT content FROM canary_variant ORDER BY md5(variant_id::text) LIMIT 40")]
    realistic = [to_pgvector(embed(t)) for t in texts]
    stored = [r[0] for r in c.execute("SELECT embedding::text FROM agent_note WHERE embedding IS NOT NULL ORDER BY md5(note_id::text) LIMIT 25")]
    n_rows, n_distinct = c.execute("SELECT count(*), count(*) FILTER (WHERE embedding_canonical) FROM agent_note WHERE embedding IS NOT NULL").fetchone()
    # the brute-force reference uses the SAME row set as the variant measured (the 10th distance over all rows is smaller than
    # over distinct vectors, because duplicates fill the top 10 at one distance)
    exact = {}
    for pred in ("embedding IS NOT NULL", "embedding_canonical"):
        for q in realistic + stored:
            exact[(pred, q)] = c.execute(f"SELECT max(d) FROM (SELECT d FROM (SELECT embedding <=> %s::vector AS d FROM agent_note "
                                         f"WHERE {pred} OFFSET 0) a ORDER BY d LIMIT 10) x", (q,)).fetchone()[0]
    plateau = c.execute("SELECT count(DISTINCT round((embedding <=> %s::vector)::numeric, 9)) FROM agent_note WHERE embedding_canonical", (realistic[0],)).fetchone()[0]
    out = {"embedded_notes": n_rows, "distinct_vectors": n_distinct, "distinct_distance_values_one_query": plateau, "variants": {}}
    for variant, pred in (("all_rows", "embedding IS NOT NULL"), ("distinct_vectors", "embedding_canonical")):
        with c.transaction():
            c.execute("SET LOCAL maintenance_work_mem = '256MB'")
            c.execute("SET LOCAL max_parallel_maintenance_workers = 0")        # /dev/shm is 64 MB in a default container
            t = time.perf_counter()
            c.execute(f"CREATE INDEX study_hnsw ON agent_note USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE {pred}")
            build_s = round(time.perf_counter() - t, 1)
            c.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
            res = {"build_s": build_s, "ef_search": {}}
            for ef in EFS:
                c.execute(f"SET LOCAL hnsw.ef_search = {ef}")
                row = {}
                for label, qs in (("realistic_queries", realistic), ("stored_vector_queries", stored)):
                    hit = 0; ms = []
                    for q in qs:
                        s = time.perf_counter()
                        ds = [d for (d,) in c.execute(f"SELECT d FROM (SELECT embedding <=> %s::vector AS d FROM agent_note WHERE {pred} "
                                                      f"ORDER BY embedding <=> %s::vector LIMIT 40) x ORDER BY d LIMIT 10", (q, q))]
                        ms.append((time.perf_counter() - s) * 1000)
                        hit += sum(1 for d in ds if d <= exact[(pred, q)] + 1e-9)
                    row[label] = {"distance_recall_at_10": round(hit / (10 * len(qs)), 3), "median_ms": round(statistics.median(ms), 2)}
                res["ef_search"][str(ef)] = row
            out["variants"][variant] = res
            raise psycopg.Rollback()
    return out


def time_index_study(c) -> dict:
    cid = c.execute("SELECT max(campaign_id) FROM audit_campaign WHERE status='CLOSED'").fetchone()[0]
    sid = c.execute("SELECT max(slot_id) FROM canary_slot WHERE campaign_id=%s", (cid,)).fetchone()[0]
    qs = {"v_lag (per-slot 2 s windows)": ("SELECT * FROM v_lag WHERE campaign_id = %s", (cid,)),
          "access_summary (campaign window)": ("SELECT d.asset_name, e.op, e.outcome, count(*) FROM access_event e JOIN data_asset d ON d.asset_id = e.asset_id "
                                               "WHERE e.event_time >= (SELECT started_at FROM audit_campaign WHERE campaign_id = %s) "
                                               "AND e.event_time <= coalesce((SELECT closed_at FROM audit_campaign WHERE campaign_id = %s), 'infinity') GROUP BY 1,2,3", (cid, cid)),
          "exposure_trail (one slot)": ("SELECT * FROM exposure_trail(%s)", (sid,))}
    out = {}
    with c.transaction():
        c.execute("DROP INDEX access_event_time_idx")
        c.execute("ANALYZE access_event")
        out["none"] = {k: explain_ms(c, *v) for k, v in qs.items()}
        c.execute("CREATE INDEX study_brin ON access_event USING brin (event_time) WITH (pages_per_range = 32)")
        c.execute("ANALYZE access_event")
        out["brin_32"] = {k: explain_ms(c, *v) for k, v in qs.items()}
        out["brin_size"] = c.execute("SELECT pg_size_pretty(pg_relation_size('study_brin'))").fetchone()[0]
        c.execute("DROP INDEX study_brin")
        c.execute("CREATE INDEX study_btree ON access_event (event_time)")
        c.execute("ANALYZE access_event")
        out["btree"] = {k: explain_ms(c, *v) for k, v in qs.items()}
        out["btree_size"] = c.execute("SELECT pg_size_pretty(pg_relation_size('study_btree'))").fetchone()[0]
        raise psycopg.Rollback()
    return out


def main():
    with psycopg.connect(config.admin_dsn(DB), autocommit=True) as c:
        res = {"label": "index-studies", "when": time.strftime("%Y-%m-%d %H:%M:%S"),
               "rows": {r[0]: r[1] for r in c.execute("SELECT table_name, row_count FROM schema_row_counts()") if r[1] > 10000},
               "vector_ann": vector_study(c), "time_index": time_index_study(c)}
    out = ROOT / "docs" / "benchmarks" / "index-studies.json"
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
