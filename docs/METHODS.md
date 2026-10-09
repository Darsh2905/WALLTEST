# WALLTEST v2: methods

How v2 decides, bounds, attributes, proves and stays fast, with the argument for each claim and the test or script that
checks it. Everything statistical runs in SQL (`db/migrations/012`–`016`). Numbers quoted here come from
`docs/calibration_v2_report.md` and `docs/benchmarks/*.json`, produced by the scripts named in each section.

---

## 1. What is tested

In each slot the research agent (HIGH side) sees one of two canary texts, chosen by a sealed CSPRNG coin flip. A trading agent
(LOW side) is **correct** in the slot if its net position in the canary's security matches the shown direction (net BUY =
positive, net SELL = negative, no trade = wrong). Without a leak, a correct guess is a fair coin: the flip is independent of
everything the agent can see except the shared channels. So for every LOW agent *a* and every set of slots *S*:

    H(a, S): P(correct) <= 1/2 in every slot of S

v1 tested one such hypothesis per (treatment cell × agent), with Holm over the 24 of them. v2 tests three kinds:

| scope | hypothesis | data | test (FIXED mode) |
|---|---|---|---|
| `AGENT` | agent *a* does not beat chance anywhere | all of *a*'s slots, pooled | exact one-sided binomial |
| `CELL` | … in treatment cell *t* | the slots of cell *t* | exact one-sided binomial |
| `CHANNEL` | channel *c* has no effect on *a*'s accuracy | matched pairs (§3) | exact sign test |

## 2. Serial gatekeeping: one error budget for every claim

**Procedure** (`campaign_inference`, migration 014). With *m* LOW agents and level α:

1. **Gate.** Test each `AGENT` hypothesis at α/*m* (Bonferroni over agents).
2. **Where.** Only for an agent whose gate rejected: Holm over that agent's `CELL` and `CHANNEL` hypotheses at α/*m*.

Every hypothesis gets one **globally adjusted p-value**, and its verdict is `LEAK` iff that p ≤ α:

    p_adj(AGENT a)   = min(1, m · p_a)
    p_adj(child h)   = max( p_adj(AGENT a),  min(1, m · holm_a(h)) )      holm_a = Holm-adjusted p within a's family

**Claim: P(any false LEAK, over all agents, cells and channels) ≤ α, under any dependence.** For a fixed agent *a*:

* if *a*'s gate null is true (it leaks nowhere), a false claim about *a* requires the gate to reject first, an event of
  probability ≤ α/*m*;
* if the gate null is false, the gate cannot err, and Holm controls false rejections in *a*'s family at α/*m*, with no
  assumption on how the cell and channel tests depend on each other.

Summing over the *m* agents gives ≤ α (Bonferroni needs no independence either, and the agents are strongly dependent: they
share the same momentum rule whenever they have no signal).

**Why pool.** The headline question ("does this agent beat chance?") uses all 152 slots instead of 19 per cell. At the demo
default (152 slots, 3 agents, α = 0.05) the gate detects accuracy **0.622** with 80% power; a single cell (19 slots at α/24) needs **0.918**.

**Measured** (`scripts/calibrate_v2.py`, scenario N): 600 campaigns with no leak anywhere, 36 hypotheses each. Any false
LEAK: **2.0%** [1.0, 3.5] at α = 0.05, **0.2%** at 0.01, **0%** at 0.001. Without multiplicity control, some cell would show
p ≤ 0.05 in **25.7%** of these campaigns. Scenario P, weak leak (accuracy 0.60 when exposed): the v2 gate detects trader-leaky
in **49%** of campaigns, the v1 procedure (any cell after flat Holm over 24) in **2.5%**; trader-partial **16%** vs **2%**.

**Checked by:** `tests/test_verdict.py` re-derives every adjusted p of two designs independently in Python (scipy +
statsmodels) from the raw orders, and checks a failed gate blocks every child claim.

## 3. Channel attribution: an exact matched-pair randomisation test

Slots are assigned by **balanced random blocking**: each block of *n_cells* consecutive slots contains every cell once, in
CSPRNG-shuffled order. Within a block, for channel *c*, pair the slots whose cells differ **only** in *c* (on vs off). A pair
is *discordant* when the agent is right in exactly one of the two slots. Under H0 "channel *c* has no effect", the on/off
label of a discordant pair is exchangeable, because which of the two slots got the "on" cell was decided by the random
shuffle; so the number of discordant pairs won by the "on" slot is Binomial(D, ½). The p-value is the exact upper tail. No
model of the agent, the market or the dependence between slots is needed.

In a 2×2×2 factorial each block yields 4 pairs per channel. Reading the result:

* **trader-partial** reads only vector memory: it is right with vector memory on and at chance with it off, so almost every
  vector pair is discordant in the "on" direction. Strong leak: flagged in **100%** of campaigns; its other two channels in
  ≤ **0.5%** (calibration scenario P).
* **trader-leaky** reads all three channels redundantly: switching one off changes nothing unless the other two are off as
  well. Only 1 of the 4 pairs per block carries its effect, so its channel tests are **true effects with little power**
  (flagged in 27–30% of strong-leak campaigns), not false attributions.

The descriptive "main effect" bars (v1) are kept, labelled descriptive: on a single campaign they can show shared chance
imbalances (every agent falls back to the same momentum rule in the same slots).

## 4. Two-sided bounds

`acc_lower` / `acc_upper`: one-sided Clopper–Pearson bounds, each at the hypothesis' Bonferroni level (α/*m* for a gate,
α/(*m*·family size) for a child). Leakage in bits per decision is 1 − H(a) for a > ½, so the bounds map to
`leakage_bits_lower` / `leakage_bits_upper`. The upper bound is what makes **NO EVIDENCE** informative: "whatever this agent
learns, its accuracy is at most 0.62, i.e. at most 0.04 bits per decision".

## 5. Sequential mode: anytime-valid e-values

**E-value.** For H0: accuracy ≤ p₀, mix the likelihood ratio uniformly over alternatives q ∈ [p₀, 1]:

    E(n, k) = (1/(1−p₀)) ∫_{p₀}^{1} (q/p₀)^k ((1−q)/(1−p₀))^(n−k) dq
            = B(k+1, n−k+1) · P(Bin(n+1, p₀) ≤ k) / ( p₀^k (1−p₀)^(n−k+1) )

Each likelihood ratio is a non-negative supermartingale under every p ≤ p₀ (its one-step mean is
1 + (p−p₀)(q−p₀)/(p₀(1−p₀)) ≤ 1), so the mixture is too. **Ville's inequality**: P(sup_t E_t ≥ 1/α) ≤ α. Hence p = 1/E is
valid **at every slot and at any data-dependent stopping time**. It depends only on (n, k), so it is a SQL function.
Confidence sequences invert it: CS_t = { p₀ : E_t(p₀) < 1/α } (upper end from the mirror mixture over [0, p₀]).

Gatekeeping (§2) is unchanged: e-value p-values replace the exact p-values.

**Stopping rules** (pre-registered in `audit_campaign.config`), evaluated only at **block boundaries** (complete blocks keep
cells balanced and channel pairs complete) and only on exactly the first *b* blocks (`campaign_inference(cid, p_upto)`):

* `FIRST_LEAK`: stop as soon as any agent's gate rejects;
* `ALL_SETTLED`: stop when every agent is settled: flagged, or its anytime upper bound on accuracy is below a materiality
  threshold (default 0.65; "whatever leaks here is immaterial");
* `MAX`: never stop early.

The campaign's scoring window then ends exactly at the stop boundary (`closed_at`); slots already committed or running
beyond it are neither scored nor evidence.

**Measured** (scenario S): 300 leak-free campaigns, one cell, a stop check **after every slot**, up to 320 slots. The engine
really stopped at the first flag. False LEAK: **0.7%** [0.1, 2.4]. On the same slot data, the naive procedure (an exact
fixed-n test after every slot, stop at the first rejection): **13.3%** [9.7, 17.7], 2.7× the nominal α.

**The price.** At the same n an e-value p is larger than the exact fixed-n p (asserted in `test_stats_v2.py` and on the
Power page). Scenario T (trust 0.4, pooled accuracy 0.675): the sequential test used a median of **88** slots (10th–90th
percentile 32–184) and detected trader-leaky in 94.7% of campaigns; a fixed-n test planned for exactly this effect needs
**71** slots for 80% power. So sequential mode is **not** faster when the effect size is known in advance. Its value is that
nobody has to know it: a strong leak stops early (p10 = 32), a weak one keeps being watched, and watching is valid.

## 6. Statistical kernels

Every bound and power quantity is a Beta quantile, because P(Bin(n, p) ≥ k) = I_p(k, n−k+1). v2 computes the regularised
incomplete beta **in log space** with the modified Lentz continued fraction (O(√n) terms), and solves for quantiles by
**safeguarded Newton** (d ln I / dx = pdf / I) inside a bisection bracket, started from a normal approximation whose z is
read off the log tail (Abramowitz & Stegun 26.2.23).

| | v1 | v2 |
|---|---|---|
| Clopper–Pearson, n = 1,543 | 11.4 ms | 0.34 ms |
| minimum detectable accuracy, n = 1,500 | 10.1 ms | 0.55 ms |
| verdict of one 152-slot campaign | 1,364 ms (view) | 21 ms (function, more hypotheses) |

Three things found while building it, each now a test:

* PostgreSQL **raises** on float underflow (`exp()` below about −745, and `y*y*y` for tiny y), where C would return 0:
  `exp_safe`, and no powers of tiny values;
* a converged Newton step lands within an ulp of the bracket end just set to x, and a strict safeguard then bisected away
  from the root (~30 wasted evaluations on skewed Betas): convergence is tested before the safeguard;
* at extreme tails **scipy's** `betainc` is off by ~1e-7 in log; the tests use 60-digit mpmath as ground truth (SQL agrees to
  ~4e-13).

The bisection implementations are kept as `*_bisect` reference functions; the tests require fast ≡ reference ≡ scipy.

## 7. Evidence: a signed Merkle root over everything the verdict rests on

**Leaves** (`evidence_leaves`, migration 013), in a fixed order: the campaign's slots, variants and sealed flips; the wall's
notes and access events, and its LOW agents' orders, inside [started_at, closed_at). Format `WALLTEST-EVIDENCE-1`:

    leaf = SHA-256(0x00 || UTF-8(kind ':' jsonb_build_object(<explicit column list>)))
    node = SHA-256(0x01 || left || right)       odd node promoted; empty tree = SHA-256('')

Domain separation as in RFC 6962. The column lists are **explicit**: `to_jsonb(row)` would have changed every leaf, and
silently invalidated every signed root, the first time a later migration added a column to one of these tables (it would
have happened in this very upgrade: 016 adds `agent_note.embedding_canonical`). `test_evidence.py` adds a column to all six
tables and checks the root does not move. Timestamps are rendered with `TimeZone` pinned to UTC inside the function.

**Snapshot.** One canonical text: a header with the campaign, evidence root and leaf count, then one line per hypothesis
(scope, cell, agent, channel, n, k, p, p_adj, verdict, method). The engine signs it with **Ed25519**; the private key lives
in a file outside the database (`.keys/`), so a database superuser who rewrites rows cannot re-sign them.

**Freeze** (`freeze_campaign`): writes one append-only row per hypothesis, **then rebuilds the text from the rows just
written** and refuses (WT013, rolling everything back) unless its SHA-256 equals the hash that was signed. What is stored is
provably what was signed.

**Verification in the browser** (Verdicts and Commit–reveal pages, `frontend/src/lib/evidence.ts`): SHA-256 of the text,
the Ed25519 signature (Web Crypto), the Merkle root rebuilt from every leaf hash, and an inclusion proof for any single row
(13 sibling hashes for 5,280 rows). The server's own checks are shown but not used. Vectors shared with Python
(`frontend/tests/evidence_vectors.json`) keep the three implementations identical.

**What it does and does not stop.** Any later edit, deletion or back-dated insertion of an evidence row changes the root
(`test_evidence.py`: edit an order, delete an event, back-date a note, forge an order). It does not stop someone who holds
the signing key and the database at the same time; that is a key-custody question, out of scope.

## 8. Per-agent logins

v1's gateway took the agent id as a parameter, and both LOW agents shared `low_side`, so one could present the other's id.
v2 gives every agent its **own login role** (`wt_agent_<name>`), a member of its side role (privileges inherited, SET ROLE
not allowed), and the gateway functions take **no agent id**: `wt_caller_agent()` resolves the caller from the session.

The first v2 design had the agents' roles *switched into* by the shared API login (`SET ROLE`). The tests found that this
authenticates nothing: PostgreSQL checks `SET ROLE` against the **session** user, so any session of the API login can become
any agent, and an agent able to send SQL could `RESET ROLE` or `SET ROLE` to another agent. With its own login, an agent's
session cannot become anyone else (`test_an_agent_login_cannot_become_anyone_else`, with a real agent connection), and the
API and lab logins are members of no agent role. Passwords are HMAC-SHA256(master key, role); the master key is a file
outside the database.

Residual: in **SIMULATED** campaigns the gateway clock is the session setting `walltest.sim_now`, which an agent with raw SQL
access could set. It cannot read flips (no privilege on `sealed_flip` or the variants), and LIVE campaigns ignore the
setting and use `clock_timestamp()`.

## 9. Performance

All numbers: `docs/benchmarks/` (Performance page). Query latency on the calibration database (1.06 M access events,
1.48 M orders, 398 k notes), as the `audit_engine` role:

| operation | v1 | v2 | how |
|---|---|---|---|
| verdict of one campaign | 1,364 ms | 21 ms | lateral SQL function (the campaign filter reaches every CTE) + kernels (§6) |
| exposure trail of one canary | 188 ms | 0.68 ms | indexes on `access_event(row_ref)`, `agent_note(isin, created_at)`, `access_event(event_time)` |
| access events in a campaign window | 25.7 ms | 0.16 ms | B-tree on `event_time` |
| evidence root | — | 28 ms | `(agent_id, placed_at)`, `(author_agent_id, created_at)` indexes |
| semantic search | — | 14 ms (brute force 258 ms) | exact search over distinct vectors (below) |

**Engine** (`scripts/ab_engine.py`, interleaved A/B, fresh database per run, 5 runs each): **+20%** slots/s one slot at a
time (median 85.0 vs 71.1), **+7%** with eight in flight (179.2 vs 166.9; the ranges overlap), while every slot does more
work (per-agent logins, evidence indexes, a signed freeze). Where it came from, by profiling (`track_functions`):

* the gateway's price read was **42%** of all database time: a window function numbered every price row up to the date;
  top-N per security from the primary key returns the same rows (tested against the v1 query);
* WAL flush waits were ~15% of backend time at concurrency 8: slot-phase transactions commit asynchronously (below);
* v1 ran fixed batches of slots and waited for each batch's slowest; v2 keeps a **sliding window** of slots in flight;
  sequential stop checks still see exactly the first *b* complete blocks;
* the freeze computed the inference 4× and the evidence root 3×; now once each;
* JIT compilation cost 105 ms on an 11 ms query (an overestimated plan cost crossed `jit_above_cost`): `jit = off` on the
  two search functions.

**Asynchronous commit, and why no verdict can lose data.** Agent and slot-phase transactions use
`synchronous_commit = off`. A campaign's results are written by one *synchronously* committed freeze, and flushing that
commit record flushes all WAL before it, including every slot-phase commit the verdict depends on. A crash can lose only the
last few hundred milliseconds of an **unfrozen** campaign; at the next start `recover_orphaned_campaigns` aborts every
campaign still PLANNED/RUNNING (which also frees its wall), so it is never scored.

### Two index designs that were measured and rejected (`scripts/index_studies.py`)

**HNSW for semantic search.** Distance-based recall@10 against brute force (a result counts if it is at least as close as
the true 10th neighbour, so ties cannot distort it):

| index over | canary-text queries | stored-vector queries | (ef_search 40 → 1000) |
|---|---|---|---|
| all 199,200 notes | 0.84 → 0.91 | 0.75 → 0.77 | a hard ceiling: clusters of identical vectors become unreachable |
| 23,780 distinct vectors | 0.82 → 0.91 | 0.70 → 0.89 | no ceiling, still short of 1 |

The embedding is a hashed bag of words: one query sees ~530 distinct distance values across 23,780 vectors, and greedy graph
search does not navigate such plateaus. A compliance search that silently drops 10–30% of the closest paraphrases is worse
than a slower one, so v2 searches **exactly**, over distinct vectors only: 11.9% of the embeddings are distinct, each has one
representative row (set by a BEFORE INSERT trigger, so the access log sees no extra writes), a covering partial index makes
the scan index-only, and a hash index expands the found vectors to every note carrying them, cutting at the k-th distance
including ties. The result equals brute force row for row (`test_gateway.py`, 25/25 in the benchmark).

The same study is why the gateway's own vector search is **exact by construction** (a MATERIALIZED candidate set): with an
HNSW index present, v1's `ORDER BY embedding <=> q LIMIT 3` plus the slot filter returns **none** of the slot's notes, the
leak channel would read as empty, and a real leak as NO EVIDENCE. `test_gateway.py` demonstrates the hazard and checks the
gateway stays exact with such an index forced.

**BRIN on `access_event.event_time`** (32 kB instead of 12 MB; the column is written in time order):

| query | no index | BRIN | B-tree |
|---|---|---|---|
| lag analysis (per-slot 2 s windows) | 2.0 ms | **68 ms** | 2.3 ms |
| access summary (campaign window) | 20 ms | 1.3 ms | 0.7 ms |
| exposure trail (one slot) | 82 ms | 0.8 ms | 0.6 ms |

BRIN wins the wide window, but the planner also chose it for the narrow per-slot windows, where each probe reads whole
32-page block ranges. A B-tree is as fast or faster on every query that exists.

## 10. Where each claim is checked

| claim | check |
|---|---|
| gatekeeping p-values | `tests/test_verdict.py` (independent Python re-derivation) |
| FWER over all claims, real engine | `scripts/calibrate_v2.py` scenario N |
| e-values / CS: definition, Ville, coverage | `tests/test_stats_v2.py` (numerical integration, 20k-path simulation, CS ⊇ CP) |
| sequential FWER vs naive peeking, real engine | `scripts/calibrate_v2.py` scenario S |
| kernels | `tests/test_stats_v2.py` (mpmath 60 digits, scipy, bisection references) |
| evidence, proofs, schema stability, tamper | `tests/test_evidence.py`, `tests/test_api.py`, `frontend/tests/evidence.test.ts`, e2e |
| per-agent logins | `tests/test_gateway.py` (real agent and API connections) |
| engine stopping, windows, signatures | `tests/test_engine.py` |
| performance | `scripts/bench.py`, `scripts/ab_engine.py`, `scripts/index_studies.py` |
