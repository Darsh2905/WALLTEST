# WALLTEST: a canary-instrumented database for auditing information barriers between AI agents

DBMS lab project: *WALLTEST – Project Proposal & Database Design* (`WALLTEST_Project_Proposal_DB_Design.pdf`, the source of truth).
A securities firm's research agent (HIGH side of an information wall) is shown one of two synthetic **canary** stories, chosen by a sealed coin flip whose SHA-256
commitment is published *before* the slot opens. Trading agents (LOW side) trade during the slot. After the slot, the flip is revealed and each trading agent's net position is
scored: net BUY = "positive variant", net SELL = "negative variant", no trade = wrong. An exact one-sided binomial test against P(correct) = ½ gives a p-value; `1 − H(a)` gives a leakage bound in bits.
Leaks can only travel through shared channels (pgvector memory, notes table, cache); a factorial design switches channels off to say which one carried the leak. **Every number on screen comes from PostgreSQL.**

> **Data honesty.** Prices are **real** NSE end-of-day bars for 8 NIFTY 50 stocks over 2 years ([`data/prices/SOURCE.md`](data/prices/SOURCE.md); every ISIN verified against NSE's own files and the ISO 6166 check digit).
> UPSI items and canary texts are **synthetic**. The research / leaky / partial / clean agents are **scripted validation instruments with planted ground truth** (the UI says so on every page).

## What's new in v2 (branch `v2`)

v2 upgrades the inference, the security architecture and the performance, and measures each change against v1
(`docs/METHODS.md`, `docs/calibration_v2_report.md`, `docs/benchmarks/`, the **Performance** page). Still exactly 20 tables, every statistic in SQL.

| | v1 | v2 |
|---|---|---|
| **Verdict** | one Holm family over 24 cells × agents | **serial gatekeeping**: a pooled test per agent (all 152 slots) gates its cells and **channel tests**; one error budget for every claim. Weak-leak detection **49% vs 2.5%**; FWER over 36 claims 2.0% at α = 0.05 |
| **Attribution** | descriptive main-effect bars | **exact matched-pair sign test** within randomised blocks: trader-partial → vector memory in 100% of campaigns, other channels ≤ 0.5% |
| **Bounds** | lower bound only | **two-sided** accuracy and leakage-bits bounds: NO EVIDENCE says how much could leak at most |
| **Peeking** | forbidden | still forbidden in FIXED mode; new **SEQUENTIAL** mode with anytime-valid **e-values**, confidence sequences and pre-registered early stopping: false alarms under continuous monitoring 0.7% vs 13.3% for naive peeking |
| **Agent identity** | per-side roles; an agent id was a parameter | **every agent logs in as itself**; the gateway takes no id |
| **Tamper evidence** | per-row hash (D-06) | **Merkle root over every evidence row + Ed25519-signed snapshot**, verified in the browser, with inclusion proofs |
| **Speed** | | verdict 1,364 → 21 ms, exposure trail 188 → 0.7 ms, bounds 15–33× faster, exact paraphrase search 14 ms; engine +20% / +7% slots/s (interleaved A/B) |

Two designs were measured and **rejected**, and the page shows why: HNSW (recall@10 0.70–0.91 on this embedding) and BRIN (one query 30× slower than a B-tree).

## Quick start (macOS, Linux, Windows)

Prerequisite: **Docker** with the compose plugin (Docker Desktop on macOS/Windows; Docker Engine + compose plugin on Linux). Nothing else: Python, Node and PostgreSQL all run in containers.

```bash
git clone <this repository> walltest && cd walltest
docker compose up --build          # PostgreSQL 16 + pgvector, migrations, seed, API + UI
# → open http://localhost:8000
```
Same thing with a health-wait and a browser launch, on any OS (including Windows without `make`):
```bash
python scripts/demo.py             # or:  make demo
python scripts/demo.py --down      # stop
python scripts/demo.py --reset     # stop and delete the database volume
```
First start builds the images (a few minutes: Node build + Python deps); afterwards it starts in seconds. It also creates `./.keys/` (git-ignored): the engine's Ed25519 signing key and the agents' login master key, kept outside the database on purpose. Ports: UI/API `8000`, PostgreSQL `5433` (change with `WALLTEST_PORT` / `WALLTEST_DB_PORT` in a `.env`).
The compose file's credentials are demo-only defaults for a local machine.

## The 3-minute demo script

The default campaign is a **live** 2×2×2 factorial of **152 one-second slots** (≈ 2 min 40 s), chosen from an exact power calculation (see below). Start it first, talk while it runs.

| Time | Click | Say |
|---|---|---|
| 0:00 | **Wall** page | "Left: the inside area, where a research agent holds synthetic deal information. Right: the trading desk. In the middle, the wall. The three channels research and trading share (vector memory, notes table, cache) are the gates. The badges say what is real: NSE prices; and what is not: the UPSI, and the scripted agents." |
| 0:15 | **Run live audit** | "Before each slot opens, a SHA-256 commitment to a secret coin flip is published. During the slot, the research agent sees the canary the flip selected and writes a paraphrase to the shared channels." |
| 0:25 | watch the hero + timeline | "Each dot is a real `access_event` row read from the database. Hatched gates are switched off for this slot's treatment cell; blocked attempts stop at the gate and are logged as DENIED. Timeline: sealed → committed hash → revealed with a verified tick." |
| 0:50 | **Run audit** page | "The chart is descriptive only: repeated looks at a fixed-n test inflate false alarms, so no p-value exists yet. The verdict is computed once, at the planned n. Right side: exact power of this design, computed in SQL; *Show SQL* shows the query." |
| 1:15 | **DB Rules Lab** → *low_side SELECT on upsi_item*, then *UPDATE trade_order* | "The wall is enforced by the database: first the privilege refuses, then, even if a privilege leaked, row-level security returns nothing. Orders are append-only: a trigger refuses even the table owner. These are PostgreSQL's real error messages, in rolled-back transactions." |
| 1:45 | **Commit–reveal** | "The browser re-hashes every revealed slot (WebCrypto) and compares it with the commitment published before the slot opened. *Tamper* flips one bit and fails visibly." |
| 2:15 | **Power** | "The proposal's table (94 / 384 / 1,543) is the normal approximation; the exact binomial needs 95 / 386 / 1,551." |
| 2:40 | back to **Wall** | "Verdicts appear only now. The leaky agent: LEAK. The partial agent: LEAK. The price-only clean agent: NO EVIDENCE, and that is not 'proven clean': the card shows the minimum detectable accuracy." |
| 2:50 | **Verdicts** | "Step 1, the gate: one pooled test per agent over all 152 slots, at α/3 each. Step 2, only for agents that passed: which cells, and which channel, by an exact matched-pair test. One error budget covers every claim on the page. Two-sided bounds: clean is NO EVIDENCE *and* its accuracy is at most the upper bound. Then *Verify in browser*: the hash, the Ed25519 signature and the Merkle root over all logged rows; *Tamper with a verdict* fails." |
| after | **Run audit → Sequential** | "Anytime-valid e-values: the chart may be watched after every slot, and the campaign stops at the first leak, still at family-wise α." |
| after | **Run audit → Null control** | "Only the clean agent: false-alarm control." |
| after | **Compliance**, **Schema** | "SDD report, grant review returns zero rows (the channels are owned by a public department: access control looks clean, the leak is not in the grants), exposure trail, lag; the ER diagram is introspected live from the catalog." |

## Architecture

```
 browser (React/TS/Tailwind/D3) ──HTTP + SSE──▶ FastAPI (thin) ──psycopg 3──▶ PostgreSQL 16 + pgvector + pgcrypto + btree_gist
                                                   │ login: walltest_api (not superuser, not owner, NOINHERIT)
                                                   │ per request: SET LOCAL ROLE high_side | low_side | audit_engine | compliance
   audit engine (Python) ── draws CSPRNG flips, commits, drives scripted agents ──▶  gateway functions gw_*  ─▶ access_event (append-only)
         │                    each agent connects with its OWN login wt_agent_<name> (v2); the gateway knows who calls
         └── freeze: evidence Merkle root + one line per hypothesis, signed with Ed25519 (key in ./.keys, not in the DB)
                                                                                    RLS · triggers · exclusion constraint · views v_slot_score / v_verdict
```
The logic lives in SQL (views, functions, triggers, RLS); the API is thin and runs named queries. The engine's agents reach shared channels **only through the gateway**.
Two clock modes: **LIVE** (real-time ~1–2 s slots; commit-before-expose provable on the wall clock; the default) and **FAST/SIMULATED** (back-dated clock for big runs, labelled "simulated clock" everywhere).

## Mapping the proposal (sections 1–9) to the code

| § | Proposal | Where |
|---|---|---|
| 1–3 | title, background, problem | this README; UI copy |
| 4(1) | model walls, agents, assets, grants | `db/migrations/001_org_walls_access.sql`, `backend/walltest/seed.py` |
| 4(2) | enforce the wall natively | `010_rls_grants.sql`, `005_triggers.sql` (wall trigger), `tests/test_roles_rls.py`, `tests/test_wall.py` |
| 4(3) | tamper-evident audit trail | `005_triggers.sql` (append-only), `tests/test_append_only.py` |
| 4(4) | randomized canary audits | `backend/walltest/engine.py`, `009_engine.sql`, `docs/COMMITMENT_FORMAT.md` |
| 4(5) | verdict in SQL | `006_statistics.sql`, `007_views.sql` (`v_verdict`), `tests/test_stats.py`, `tests/test_verdict.py` |
| 4(6) | attribute the leak to a channel | factorial designs (`create_campaign_cells`), `v_channel_effect`, Verdicts page |
| 4(7), 6.4 | SDD-style compliance queries | `007_views.sql`, Compliance page |
| 5 | scope | seed data, README "Data honesty" |
| 6.1–6.3 | data, users, operations | schema + seed; bulk load with `COPY` (`seed.py`) |
| 6.4 | analyses (verdict, attribution, lag, exposure, SDD, grant review, model comparison) | Verdicts + Compliance pages |
| 6.5 | integrity, tamper evidence, confidentiality, reproducibility, scale | triggers, RLS, `result_hash`, `config jsonb`, indexes; Power page for the 1,500-slot claim |
| 7 | PostgreSQL 16 + pgvector/pgcrypto/btree_gist | `docker-compose.yml`, `000_bootstrap.sql` |
| 8.1–8.4 | the 20 relations, keys, constraints | `001`–`004`, `005`; `tests/test_schema.py`, `tests/test_constraints.py` |
| 8.5 | normalization (BCNF) | schema design; per-slot scores are *derived* (views), only `audit_result` stores derived values, on purpose |
| 9 | architecture, workflow of one slot (7 steps) | `engine.py` (steps 1, 2, 5, 6), `008_gateway.sql` (steps 3, 4), dashboard (step 7) |

Every divergence or addition is in [`DEVIATIONS.md`](DEVIATIONS.md); the SQL behind each requirement is in [`docs/SQL_SHOWCASE.md`](docs/SQL_SHOWCASE.md).

## Statistics (all in SQL)

v2 (details and proofs in [`docs/METHODS.md`](docs/METHODS.md)): **serial gatekeeping** (per-agent pooled gate at α/n_agents, then Holm over that agent's
cells and channels), an **exact matched-pair sign test** for channel attribution, **two-sided** Clopper–Pearson bounds, and an optional **SEQUENTIAL**
mode with beta-binomial mixture e-values and confidence sequences (valid under continuous monitoring and optional stopping, by Ville's inequality).
Bounds and power are Beta quantiles computed with a log-space incomplete beta and safeguarded Newton. The v1 items below still hold for FIXED mode:

* **Exact one-sided binomial p**, computed in **log space** (a naive `0.5^n · C(n,k)` underflows for large n and PostgreSQL then raises "value out of range"). Matches scipy to ≈ 1e-13 at n = 1,543.
* **Leakage** `1 − H(a)` bits for a > 0.5. **Clopper–Pearson** one-sided lower bound on accuracy (and the matching bits bound), because a point estimate is not a bound.
* **Multiplicity**: v1 used Holm–Bonferroni across the (treatment × LOW agent) family; v2 uses serial gatekeeping (above), and still reports the v1 flat-Holm p (`p_adj_flat_holm`) for comparison. Raw and adjusted p are both shown.
* **No peeking.** The verdict is computed once at the planned n; `v_verdict` returns NULL for every inferential column until then, and live charts are labelled descriptive.
* **NO EVIDENCE is not "proven clean"**: the minimum detectable accuracy (80% power, exact) is shown beside it; when n is too small to reject at all, the UI says "underpowered".
* **Defaults are derived, not tuned** (`scripts/choose_defaults.py` → `docs/defaults_derivation.json`): α = 0.05 family-wise, 2×2×2 factorial × 3 agents (K = 24), planted accuracy 0.95 (trust 0.9), and the smallest slots-per-cell for which P(every truly-leaky cell is flagged by Holm) ≥ 0.80 and stays there: **19 per cell = 152 slots**
  (joint power 0.859, family-wise false-alarm probability 0.031 ≤ α; exact per-cell power 0.933 at the conservative Bonferroni level).

## Verification (what ran, what passed)

```bash
docker compose up -d db && python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt && .venv/bin/python -m playwright install chromium
cd backend && ../.venv/bin/python -m pytest -q                 # real PostgreSQL, real roles, nothing mocked
cd frontend && npm test                                         # browser commitment + evidence code vs vectors shared with Python
make e2e                                                        # Playwright: drives a LIVE campaign through the UI (~5 min)
make calibrate                                                  # v1: >= 2,000 null campaigns through the real engine (~8 min)
python scripts/calibrate_v2.py                                  # v2: 1,450 campaigns (~25 min) -> docs/calibration_v2_report.md
python scripts/bench.py --label v2 && python scripts/ab_engine.py && python scripts/index_studies.py   # docs/benchmarks/
```

**v2 run (this branch):**

| Suite | Result |
|---|---|
| `backend/tests` (pytest, real PostgreSQL, nothing mocked): schema 8 · constraints 22 · append-only 14 · commitment 16 · wall 8 · roles/RLS 24 · **gateway 36** (incl. real agent and API logins attempting impersonation; the HNSW hazard; semantic search ≡ brute force) · stats 103 · **stats v2 80** (e-values vs integration, Ville by simulation, CS coverage, kernels vs 60-digit mpmath) · **verdict 17** (gatekeeping re-derived in Python) · **evidence 21** (Merkle vs Python, proofs, tamper, schema evolution, signatures) · engine 20 · lab 13 · api 16 · llm 6 | **404 passed** |
| `frontend/tests` (vitest): commitment 5 · evidence 4 (Merkle roots for 0–9 leaves, proofs incl. a promoted step, Ed25519) | **9 passed** |
| `backend/e2e_tests` (Playwright, a real 152-slot LIVE campaign through the UI): the v1 checks, plus the gate / cells / channel tables (trader-partial attributed to vector memory only), **signature + Merkle root verified in Chromium and a tampered verdict failing**, an inclusion proof, paraphrase search, the Performance and peeking panels, and a **sequential campaign started from the Run page that stops early, signed** | **17 passed** |
| `scripts/calibrate_v2.py` | **PASS** (table below) |
| Docker image | built from this branch, run on a fresh database: simulated + LIVE campaigns frozen, all four snapshot checks true, all four agents seen connected under their own logins |

### Calibration v2 (`docs/calibration_v2_report.md`, 1,488 s, 1,450 campaigns through the real engine)

| Scenario | Result |
|---|---|
| **No leak, every claim** (600 campaigns × 36 hypotheses) | any false LEAK: **2.0%** at α = 0.05, 0.2% at 0.01, 0% at 0.001 (unadjusted: 25.7%) |
| **No leak, watched after every slot** (300 campaigns, up to 320 slots, engine stops at the first flag) | e-values **0.7%**; naive fixed-n test after every slot on the same data **13.3%** |
| **Weak leak** (accuracy 0.60 when exposed, 200 campaigns) | v2 gate detects trader-leaky **49%**, v1 procedure **2.5%**; trader-partial 16% vs 2% |
| **Strong leak** (accuracy 0.95, 200 campaigns) | both procedures 100%; channel test: trader-partial → vector memory 100%, its other channels ≤ 0.5% |
| **Slots to detection** (sequential, accuracy 0.675) | median 88 (p10–p90: 32–184); a fixed-n test planned for exactly this effect needs 71: sequential mode buys validity under watching, not speed at a known effect |

**v1 run (kept for reference):**
| Suite | What it proves | Result |
|---|---|---|
| `tests/test_schema.py`, `test_constraints.py` (30) | exactly 20 relations; keys, composite FKs, exclusion constraint, every domain / row / format CHECK, 1:2 and 1:1 cardinalities, SDD two-sharer rule: each negative test shows the real Postgres error | pass |
| `test_append_only.py` (14), `test_commitment.py` (16), `test_wall.py` (8) | append-only (trigger layer *and* revoked privileges, UPDATE/DELETE/TRUNCATE); commitment known-answer vector, SQL ≡ Python on 300 random inputs, microsecond sensitivity, commit-before-expose; the wall trigger and its two bypasses; grant review | pass |
| `test_roles_rls.py` (24) | `walltest_api` is not superuser / not owner / cannot `SET ROLE` the owner; `low_side` cannot read `upsi_item` (privilege layer, RLS second layer, and **through the API path**); sealed flips invisible until the slot ends | pass |
| `test_gateway.py` (23) | active-grant windows (`valid_from` inclusive, `valid_to` exclusive), per-channel treatment flags on reads *and* writes, denied attempts logged without raising, wrong side, slot-scoped memory, kNN, pinned `search_path` on every `SECURITY DEFINER` function | pass |
| `test_stats.py` (103) | SQL `binom_upper_p` vs `scipy.stats.binom` (grid incl. **n = 1,543**, underflow cases), Clopper–Pearson vs scipy and statsmodels (+ exact duality with the test), Holm vs statsmodels (ties, zeros), exact power / MDA vs scipy, the proposal's table | pass |
| `test_verdict.py` (11), `test_engine.py` (11), `test_lab.py` (13), `test_api.py` (13), `test_llm_agent.py` (6) | scoring rule, **no peeking**, Holm in `v_verdict` ≡ statsmodels, frozen snapshots + tamper detection; real engine runs (balanced blocks, independent variant bits, verified commitments, LIVE commit-before-expose, abort on cancel); Rules Lab leaves no trace; API + SSE; LLM plumbing with a stub | pass |
| **pytest total** | | **272 passed** |
| `frontend/tests/commitment.test.ts` (5) | browser SHA-256 (WebCrypto and the pure-JS fallback) equals Python `hashlib` on the shared known-answer vector | **5 passed** |
| `backend/e2e_tests` (Playwright, 14) | honesty badges on every page; the Power page recomputes 94/384/1,543 (+ exact 95/386/1,551); all 10 Rules Lab cases show Postgres's error text; a **Show SQL** drawer on every page; theme toggle; schema page lists 20 tables; a real **152-slot LIVE campaign driven through the UI**: pulses cross the gates, switched-off gates hatched, denied attempts stop at the gate, sealed → committed → revealed seen on the timeline, the verdict **withheld** while running; then **leaky = LEAK, clean = NO EVIDENCE (0 of 8 cells flagged), partial → leak only in vector-memory cells and a +0.45 vector main effect**; the browser verifies **152 of 152** commitments and each *tamper* button fails visibly; compliance reports; the **null-control** button; screenshots of every page at 1440×900 and 1366×768 in light and dark (no console errors, no horizontal overflow) | **14 passed** |
| `scripts/calibrate.py` | below | **PASS** |
| Fresh clone | `git clone` into a new directory, `docker compose up --build` using only this README → healthy, migrated, seeded, a 152-slot simulated campaign produced exactly the predicted pattern (leaky LEAK in 7 cells, partial in its 4 vector-on cells, clean 0 of 8) | **works** |

### Calibration (`docs/calibration_report.md`, 914 s, 2,500 campaigns through the real engine, gateway, RLS and views)

| Scenario | Result |
|---|---|
| **A: 2,000 null campaigns** (null-control wall, 40 slots, K = 1) | α = 0.05: **86 false alarms = 4.30%** (exact size of the discrete test 4.03%) · α = 0.001: **0 of 2,000** (exact size 0.034%) |
| **B: 400 null campaigns** (full wall, trust 0, 5 cells × 10 slots, Holm over K = 15) | α = 0.05: **2 = 0.5%** · α = 0.001: **0** |
| Independence | the price-only clean trader pools to **40,190 / 80,000 = 0.5024** correct (p = 0.18 vs ½): the canary direction is independent of prices |
| **C: 100 campaigns at the demo default** (152 slots, trust 0.9) | P(every truly-leaky cell flagged) = **0.850** vs the Monte-Carlo prediction 0.859; P(leaky all-on flagged) = 0.970. False alarms on null cells in 6/100 campaigns vs 3.1% predicted (1.7 SD high, within noise at n = 100; Holm's guarantee ≤ α = 0.05 is theoretical and distribution-free) |

Statistical honesty: the exact binomial test is discrete, so its true size (4.0% at n = 40) sits below α; the observed rates match those sizes within Monte-Carlo error. The proposal's own scale claim is reported as measured: 1,500 slots give **77.4%** power at 55% accuracy and α = 0.001 (minimum detectable accuracy 0.551), not a full 80%.

### Optional LLM agent (stretch, off by default)
`WALLTEST_LLM=1 ANTHROPIC_API_KEY=… python scripts/demo.py` seeds a second wall (`WALL-2`: research + an LLM-backed `trader-llm` + the clean baseline) and shows an opt-in checkbox on the Run page.
The agent sees **only** the note texts the gateway returned (never the flip or variants) and answers `BUY <ISIN>`, `SELL <ISIN>` or `HOLD`; errors or unparseable replies fall back to the momentum rule. It has no planted behaviour: whatever it does is reported as measured and was **not tuned**
(no prompt iteration against any verdict). Only its plumbing is tested (`test_llm_agent.py`, with a stub model); no API call was made in this build, so **no real-model result is reported here**.


## Repository layout

```
docker-compose.yml  Dockerfile  Makefile  scripts/demo.py       one-command demo
db/migrations/      000–011 the v1 schema, triggers, RLS, statistics, gateway, engine; 012–016 v2 (statistics, evidence, inference, agent logins, performance)
backend/walltest/   engine, scripted agents, FastAPI app, queries registry, Rules Lab, run manager, migrate/seed
backend/tests/      pytest suite (+ e2e_tests Playwright)
frontend/           Vite + React + TypeScript + Tailwind + D3
data/prices/        real NSE end-of-day CSV + SOURCE.md (provenance, fetch date, ISIN verification)
scripts/            fetch_prices, choose_defaults, calibrate, calibrate_v2, bench, ab_engine, index_studies, screenshots, reset_db, demo
docs/               METHODS.md (v2), PLAN_V2.md, SQL_SHOWCASE.md, COMMITMENT_FORMAT.md, calibration_report.*, calibration_v2_report.md,
                    benchmarks/*.json, defaults_derivation.json, screenshots/
```

## Known limitations

* **Scripted agents are instruments.** Leak rates are planted (accuracy = 0.5 + trust/2), not discovered; the audit is validated by recovering them and by *not* flagging the price-only agent. The optional LLM agent (see below) is a stretch, off by default, never tuned.
* **Agent identity (closed in v2).** v1's roles were per side and an agent id was a parameter; every agent now logs in as itself (D-26).
* **A PostgreSQL superuser can disable triggers** and rewrite rows. For a frozen campaign that is now *detectable* by anyone holding the published signature (Merkle root over every evidence row, Ed25519 key outside the DB), unless the attacker also holds the engine's signing key: key custody is out of scope.
* **SIMULATED campaigns cannot prove wall-clock commit-before-expose**: timestamps are back-dated by the engine. The hash check is equally strong; LIVE mode is the one that proves ordering. In SIMULATED mode the gateway clock is a session setting that an agent with raw SQL access could set; it still cannot read flips, and LIVE ignores the setting.
* **Sequential mode costs power.** At an effect size known in advance, a fixed-n test planned for it needs fewer slots (calibration scenario T). Use SEQUENTIAL when the effect is unknown or the evidence must be watched.
* **One audit clock per firm** (D-09) because orders carry no campaign id; and one RUNNING campaign per wall.
* **Dependence in the family.** Agents that follow the same momentum rule make identical decisions in cells where they have no signal, so the 24 tests are positively dependent. Holm needs no independence assumption; the false-alarm calibration is measured under exactly this dependence.
* **`leakage_bits_lower` is conservative** (Bonferroni level α/K); Holm can reject a cell whose bound is still ≤ 0.5 (shown as 0 bits).
* **Real UPSI, live trading and intraday data are out of scope**, as in the proposal. Embeddings are a deterministic hashed bag-of-words, not a learned model.
* Verified on macOS (Apple silicon, Docker via Colima). Linux and Windows are supported by the same compose file (the Windows-specific piece is the selector event-loop policy for psycopg's async API when running outside Docker); only the macOS path was exercised here.
