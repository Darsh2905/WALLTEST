#!/usr/bin/env python3
"""Derive the demo defaults from the exact power calculation (not by trial and error), and write docs/defaults_derivation.json.

Design: full 2x2x2 factorial (8 cells) x 3 LOW agents -> a family of K = 24 tests, Holm at family-wise alpha = 0.05.
Truth planted by the scripted agents (trust 0.9 -> accuracy 0.95):
   trader-leaky   leaks in 7 of 8 cells (every cell with at least one channel on)
   trader-partial leaks in 4 of 8 cells (those with vector memory on)
   trader-clean   never leaks
Criterion (fixed BEFORE looking at the outputs): the smallest slots-per-cell m such that P(every truly-leaky cell is flagged by Holm)
is >= 0.80 for m and for every larger m up to m+8 (exact power is a sawtooth in n, so a single crossing is not enough).
p-values come from the SQL function binom_upper_p; the draws and the Holm step-down are vectorised in numpy (the SQL Holm is tested
against statsmodels in backend/tests/test_stats.py). Run: python scripts/choose_defaults.py [--reps 20000]"""
import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from walltest import config  # noqa: E402


def holm_reject(p: np.ndarray, alpha: float) -> np.ndarray:
    K = p.shape[1]
    order = np.argsort(p, axis=1)
    ps = np.take_along_axis(p, order, 1)
    adj = np.minimum(1, np.maximum.accumulate(ps * (K - np.arange(K)), axis=1))
    rej = np.zeros(adj.shape, bool)
    np.put_along_axis(rej, order, adj <= alpha, 1)
    return rej


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=20000)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--trust", type=float, default=0.9)
    ap.add_argument("--target", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--mmin", type=int, default=8)
    ap.add_argument("--mmax", type=int, default=40)
    ap.add_argument("--out", default=str(ROOT / "docs" / "defaults_derivation.json"))
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    acc = 0.5 + a.trust / 2
    cells = list(itertools.product((1, 0), repeat=3))
    truth, probs = [], []
    for agent in ("leaky", "partial", "clean"):
        for v, n, c in cells:
            leak = (v or n or c) if agent == "leaky" else (v if agent == "partial" else 0)
            truth.append(bool(leak)); probs.append(acc if leak else 0.5)
    truth, probs = np.array(truth), np.array(probs)
    K = len(probs)
    rows = []
    with psycopg.connect(config.admin_dsn()) as c:
        for m in range(a.mmin, a.mmax + 1):
            ptab = np.array([c.execute("SELECT binom_upper_p(%s,%s)", (m, k)).fetchone()[0] for k in range(m + 1)])   # SQL p-values
            k = rng.binomial(m, probs, size=(a.reps, K))
            rej = holm_reject(ptab[k], a.alpha)
            cell_pw = c.execute("SELECT power_exact(%s,%s,%s)", (m, acc, a.alpha / K)).fetchone()[0]
            rows.append({"m": m, "n_total": 8 * m, "cell_power_bonferroni_exact": cell_pw,
                         "p_all_leaky_flagged": float(rej[:, truth].all(1).mean()),
                         "p_leaky_all_on_flagged": float(rej[:, 0].mean()),
                         "mean_leaky_cell_power": float(rej[:, truth].mean()),
                         "fwer_false_alarm": float(rej[:, ~truth].any(1).mean())})
    chosen = next(r for i, r in enumerate(rows) if all(x["p_all_leaky_flagged"] >= a.target for x in rows[i:i + 9]) and i + 8 < len(rows))
    out = {"alpha": a.alpha, "trust": a.trust, "planted_accuracy": acc, "K": K, "target_joint_power": a.target, "reps": a.reps, "seed": a.seed,
           "criterion": "smallest m with P(all truly-leaky cells flagged by Holm) >= target for m..m+8", "chosen_m": chosen["m"],
           "chosen_n_total": chosen["n_total"], "default_joint_power": chosen["p_all_leaky_flagged"], "default_fwer": chosen["fwer_false_alarm"],
           "default_cell_power_bonferroni_exact": chosen["cell_power_bonferroni_exact"], "table": rows}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"{'m':>3} {'n':>4} {'cell power (exact, Bonf.)':>26} {'P(all leaky flagged)':>21} {'P(leaky all-on)':>16} {'FWER':>7}")
    for r in rows:
        mark = "  <== default" if r["m"] == chosen["m"] else ""
        print(f"{r['m']:>3} {r['n_total']:>4} {r['cell_power_bonferroni_exact']:>26.3f} {r['p_all_leaky_flagged']:>21.3f} {r['p_leaky_all_on_flagged']:>16.3f} {r['fwer_false_alarm']:>7.4f}{mark}")
    print(f"\nchosen: {chosen['m']} slots per cell = {chosen['n_total']} slots; joint power {chosen['p_all_leaky_flagged']:.3f}; FWER {chosen['fwer_false_alarm']:.4f} (alpha {a.alpha})")


if __name__ == "__main__":
    main()
