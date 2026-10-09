# WALLTEST v2: plan

v1 answered "is there a leak in this cell?" with a flat Holm correction over 24 cells, a fixed sample size, a lower bound only,
and descriptive channel attribution. v2 upgrades the **inference**, the **security architecture** and the **performance**, and
measures every improvement against a v1 baseline (`docs/benchmarks/`). Invariants kept: exactly 20 tables, all statistics in SQL,
no mock numbers, every claim reproducible by a script.

## 1. Inference v2 (all in SQL, validated against independent references)

| # | Method | Why it is better than v1 |
|---|---|---|
| I-1 | **Hierarchical gatekeeping.** Per LOW agent: pooled exact binomial test over all its slots at α/n_agents (Bonferroni over agents); only if rejected, Holm over that agent's cells and channels at α/n_agents. Wall verdict = any agent rejected. | The headline verdict uses all 152 slots instead of 19: minimum detectable accuracy drops from ~0.92 to ~0.62. FWER ≤ α over *every* claim (agents, cells, channels), under arbitrary dependence. |
| I-2 | **Exact matched-pair randomization test for channel attribution.** Within each block, the two cells that differ only in channel *c* form a pair; under H0 ("channel c has no effect") the label is a fair coin on discordant pairs → exact sign test. | v1 attribution was a descriptive bar. v2 gives an exact p-value whose validity follows from the randomisation itself (no model assumptions). |
| I-3 | **Two-sided bounds.** Clopper–Pearson upper bound → an **upper bound on leakage in bits**. | "NO EVIDENCE" now comes with "at most X bits leaked", not only "could not detect below MDA". |
| I-4 | **Anytime-valid inference (sequential mode).** Closed-form beta-binomial **mixture e-values** E(n,k) = 2ⁿ⁺¹·P(Bin(n+1,½) ≤ k)/((n+1)·C(n,k)); anytime-valid **confidence sequences**; Ville's inequality gives validity under continuous monitoring and optional stopping. | v1 forbade peeking. v2's sequential mode shows a live, valid statistic and can stop as soon as the evidence is decisive. |
| I-5 | Faster exact kernels: Lanczos log-gamma, binomial tails summed from k with early termination. | Same accuracy (re-validated vs scipy), fewer iterations. |

## 2. Security architecture v2

| # | Change | Closes |
|---|---|---|
| S-1 | **Per-agent database roles.** Each agent has its own role (member of its side role); the gateway authenticates the agent from the role (`current_setting('role')`), not from a parameter. | v1 limitation "roles are per side; a LOW agent could present another agent's id". |
| S-2 | **Merkle evidence root** over the campaign's log (commitments, flips, variants, notes, access events, orders), RFC-6962-style domain separation, inclusion proofs. | v1 limitation "a superuser can disable triggers": any later edit or deletion changes the recomputed root. |
| S-3 | **Ed25519-signed verdict snapshots** (key held by the engine, never in the DB), verified in the browser with Web Crypto. | v1 D-06 ("signed" was only a hash). |

## 3. Performance v2 (measured before/after)

| # | Change |
|---|---|
| P-1 | Engine: fewer round trips per slot (role + clock in one statement, pipelined independent statements), parallel simulated slots. |
| P-2 | Database at scale: BRIN indexes on the append-only time columns, covering indexes for scoring, verified with EXPLAIN on the 1M-row calibration database. |
| P-3 | pgvector 0.8 **HNSW with iterative scans** for filtered nearest-neighbour search, with a recall test against the exact scan. |
| P-4 | LIVE clock: NTP-style min-RTT offset estimation, periodic re-sync. |
| P-5 | Frontend: route-level code splitting, windowed slot list. |

## 4. Verification
Every previous test stays green (updated where semantics changed), plus: e-values vs numerical integration, CS coverage by simulation,
gatekeeping FWER under the full dependence structure, attribution test size, FWER of **naive peeking vs e-values** under continuous
monitoring, power v1 vs v2, benchmark report, e2e, fresh clone.

## 5. Outcome against this plan (what was built, and where measurement overruled the plan)

| Plan item | Outcome |
|---|---|
| I-1 … I-4 | built as planned (migrations 012, 014); validated in `docs/calibration_v2_report.md` |
| I-5 kernels | went further: incomplete beta + safeguarded Newton replaced the bisections (migration 016) |
| S-1 per-agent **roles** | became per-agent **logins**: the tests showed a shared login that `SET ROLE`s into agent roles authenticates nothing (D-26) |
| S-2, S-3 | built; the leaf format was then pinned to explicit column lists after noticing `to_jsonb(row)` would break every signed root at the next `ADD COLUMN` (D-27) |
| P-1 engine | built, plus what profiling found: top-N price reads (42% of DB time), asynchronous slot-phase commits, a sliding window, a single-pass freeze (D-31) |
| P-2 **BRIN** | **rejected on measurement**: 30× slower than a B-tree for per-slot windows (`scripts/index_studies.py`, D-30) |
| P-3 **HNSW** | **rejected on measurement**: recall@10 0.70–0.91; replaced by exact search over distinct vectors (D-29) |
| P-4, P-5 | built (clock sync; code-split routes) |
| §4 verification | all of it, plus an interleaved A/B (`scripts/ab_engine.py`), because single best-of-two engine numbers varied by ~15% run to run |
