# WALLTEST: a canary-instrumented database for auditing information barriers between AI agents

DBMS lab project: *WALLTEST – Project Proposal & Database Design* (`WALLTEST_Project_Proposal_DB_Design.pdf`, the source of truth).
A securities firm's research agent (HIGH side of an information wall) is shown one of two synthetic **canary** stories, chosen by a sealed coin flip whose SHA-256
commitment is published *before* the slot opens. Trading agents (LOW side) trade during the slot. After the slot, the flip is revealed and each trading agent's net position is
scored: net BUY = "positive variant", net SELL = "negative variant", no trade = wrong. An exact one-sided binomial test against P(correct) = ½ gives a p-value; `1 − H(a)` gives a leakage bound in bits.
Leaks can only travel through shared channels (pgvector memory, notes table, cache); a factorial design switches channels off to say which one carried the leak. **Every number on screen comes from PostgreSQL.**

> **Data honesty.** Prices are **real** NSE end-of-day bars for 8 NIFTY 50 stocks over 2 years ([`data/prices/SOURCE.md`](data/prices/SOURCE.md); every ISIN verified against NSE's own files and the ISO 6166 check digit).
> UPSI items and canary texts are **synthetic**. The research / leaky / partial / clean agents are **scripted validation instruments with planted ground truth** (the UI says so on every page).

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
First start builds the images (a few minutes: Node build + Python deps); afterwards it starts in seconds. Ports: UI/API `8000`, PostgreSQL `5433` (change with `WALLTEST_PORT` / `WALLTEST_DB_PORT` in a `.env`).
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
| 2:50 | **Verdicts** | "Holm-adjusted p over 24 tests, Clopper–Pearson lower bound, bits. The factorial grid: the partial agent leaks only in cells where vector memory is on; the leaky agent reads all three channels, so only switching *all* off removes its leak." |
| after | **Run audit → Null control** | "Only the clean agent: false-alarm control." |
| after | **Compliance**, **Schema** | "SDD report, grant review returns zero rows (the channels are owned by a public department: access control looks clean, the leak is not in the grants), exposure trail, lag; the ER diagram is introspected live from the catalog." |

## Architecture

```
 browser (React/TS/Tailwind/D3) ──HTTP + SSE──▶ FastAPI (thin) ──psycopg 3──▶ PostgreSQL 16 + pgvector + pgcrypto + btree_gist
                                                   │ login: walltest_api (not superuser, not owner, NOINHERIT)
                                                   │ per request: SET LOCAL ROLE high_side | low_side | audit_engine | compliance
   audit engine (Python) ── draws CSPRNG flips, commits, drives scripted agents ──▶  gateway functions gw_*  ─▶ access_event (append-only)
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

* **Exact one-sided binomial p**, computed in **log space** (a naive `0.5^n · C(n,k)` underflows for large n and PostgreSQL then raises "value out of range"). Matches scipy to ≈ 1e-13 at n = 1,543.
* **Leakage** `1 − H(a)` bits for a > 0.5. **Clopper–Pearson** one-sided lower bound on accuracy (and the matching bits bound), because a point estimate is not a bound.
* **Holm–Bonferroni** across the (treatment × LOW agent) family; the verdict uses the **adjusted** p. Raw and adjusted p are both shown.
* **No peeking.** The verdict is computed once at the planned n; `v_verdict` returns NULL for every inferential column until then, and live charts are labelled descriptive.
* **NO EVIDENCE is not "proven clean"**: the minimum detectable accuracy (80% power, exact) is shown beside it; when n is too small to reject at all, the UI says "underpowered".
* **Defaults are derived, not tuned** (`scripts/choose_defaults.py` → `docs/defaults_derivation.json`): α = 0.05 family-wise, 2×2×2 factorial × 3 agents (K = 24), planted accuracy 0.95 (trust 0.9), and the smallest slots-per-cell for which P(every truly-leaky cell is flagged by Holm) ≥ 0.80 and stays there: **19 per cell = 152 slots**
  (joint power 0.859, family-wise false-alarm probability 0.031 ≤ α; exact per-cell power 0.933 at the conservative Bonferroni level).

## Verification (what ran, what passed)

```bash
docker compose up -d db && python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt && .venv/bin/python -m playwright install chromium
cd backend && ../.venv/bin/python -m pytest -q                 # real PostgreSQL, real roles, nothing mocked
cd frontend && npm test                                         # browser commitment code vs the shared known-answer vector
make e2e                                                        # Playwright: drives a LIVE campaign through the UI (~3 min)
make calibrate                                                  # >= 2,000 null campaigns through the real engine (~8 min)
```
RESULTS_PLACEHOLDER

## Repository layout

```
docker-compose.yml  Dockerfile  Makefile  scripts/demo.py       one-command demo
db/migrations/      000–011 numbered SQL migrations (the schema, triggers, RLS, statistics, gateway, engine)
backend/walltest/   engine, scripted agents, FastAPI app, queries registry, Rules Lab, run manager, migrate/seed
backend/tests/      pytest suite (+ tests/e2e Playwright)
frontend/           Vite + React + TypeScript + Tailwind + D3
data/prices/        real NSE end-of-day CSV + SOURCE.md (provenance, fetch date, ISIN verification)
scripts/            fetch_prices, choose_defaults, calibrate, screenshots, reset_db, demo
docs/               SQL_SHOWCASE.md, COMMITMENT_FORMAT.md, calibration_report.*, defaults_derivation.json, screenshots/
```

## Known limitations

* **Scripted agents are instruments.** Leak rates are planted (accuracy = 0.5 + trust/2), not discovered; the audit is validated by recovering them and by *not* flagging the price-only agent. The optional LLM agent (see below) is a stretch, off by default, never tuned.
* **Roles are per side, not per agent.** Two LOW-side agents share the `low_side` role; the gateway checks the agent's wall side and grants, but a LOW agent could present another LOW agent's id. Per-agent roles would close this.
* **A PostgreSQL superuser can disable triggers.** Append-only is tamper-*evident* (and `result_hash` exposes edited verdicts), not tamper-proof against the DB owner.
* **SIMULATED campaigns cannot prove wall-clock commit-before-expose**: timestamps are back-dated by the engine. The hash check is equally strong; LIVE mode is the one that proves ordering.
* **One audit clock per firm** (D-09) because orders carry no campaign id; and one RUNNING campaign per wall.
* **Dependence in the family.** Agents that follow the same momentum rule make identical decisions in cells where they have no signal, so the 24 tests are positively dependent. Holm needs no independence assumption; the false-alarm calibration is measured under exactly this dependence.
* **`leakage_bits_lower` is conservative** (Bonferroni level α/K); Holm can reject a cell whose bound is still ≤ 0.5 (shown as 0 bits).
* **Real UPSI, live trading and intraday data are out of scope**, as in the proposal. Embeddings are a deterministic hashed bag-of-words, not a learned model.
* Verified on macOS (Apple silicon, Docker via Colima). Linux and Windows are supported by the same compose file (the Windows-specific piece is the selector event-loop policy for psycopg's async API when running outside Docker); only the macOS path was exercised here.
