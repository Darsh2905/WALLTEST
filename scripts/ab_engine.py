#!/usr/bin/env python3
"""Interleaved A/B of engine throughput: a baseline git ref (default HEAD) against the working tree.

Each run uses a FRESH database migrated and seeded by that version's own code, on the same PostgreSQL server; runs are
interleaved (A1 B1 A2 B2 ...) so drift in machine load hits both sides alike. Reports median / min / max per side.
A single best-of-2 number (scripts/bench.py) is too noisy to compare versions: run-to-run spread at concurrency 8 is ~15%.

Usage: python scripts/ab_engine.py [--ref HEAD] [--reps 5] [--label ab-engine-v1-v2]
"""
import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = r'''
import asyncio, sys, time, pathlib
sys.path.insert(0, "{src}/backend")
from walltest import config, migrate, seed
config.MIGRATIONS_DIR = pathlib.Path("{src}/db/migrations")
from walltest.db import Database
from walltest.engine import CampaignRunner, RunConfig
DB = "{db}"
migrate.drop_database(DB); migrate.create_database_if_missing(DB); migrate.migrate(DB, verbose=False); seed.seed(DB, verbose=False)
async def go(conc):
    db = Database(config.api_dsn(DB), min_size=2, max_size=40); await db.open()
    t = time.perf_counter()
    await CampaignRunner(db, RunConfig(alpha=0.05, planned_slots=152, design="FULL_FACTORIAL", clock_mode="SIMULATED", seed=1,
                                       trust={{"trader-leaky": 0.9, "trader-partial": 0.9}}, concurrency=conc), None).run()
    r = 152 / (time.perf_counter() - t); await db.close(); return r
print(round(asyncio.run(go({conc})), 1))
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--label", default="ab-engine")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "baseline"
        base.mkdir()
        archive = subprocess.run(["git", "archive", a.ref, "backend", "db", "data"], cwd=ROOT, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(base)], input=archive, check=True)
        sides = {"baseline": base, "working_tree": ROOT}
        res: dict = {}
        for rep in range(a.reps):
            for side, src in sides.items():
                for conc in (1, 8):
                    code = RUN.format(src=src, db=f"walltest_ab_{side}", conc=conc)
                    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=src / "backend")
                    if out.returncode:
                        sys.exit(out.stderr[-2000:])
                    res.setdefault(f"{side}_conc{conc}", []).append(float(out.stdout.strip().splitlines()[-1]))
            print(f"rep {rep + 1}/{a.reps}: " + ", ".join(f"{k}={v[-1]}" for k, v in res.items()), flush=True)
    summary = {k: {"median": statistics.median(v), "min": min(v), "max": max(v), "runs": v} for k, v in res.items()}
    out = {"label": a.label, "baseline_ref": a.ref, "metric": "simulated slots per second, 152-slot 2x2x2 factorial", "results": summary}
    (ROOT / "docs" / "benchmarks" / f"{a.label}.json").write_text(json.dumps(out, indent=1))
    for k, v in summary.items():
        print(f"{k:22s} median {v['median']:6.1f}   min {v['min']:6.1f}   max {v['max']:6.1f}")


if __name__ == "__main__":
    main()
