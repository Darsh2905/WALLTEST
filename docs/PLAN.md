# WALLTEST demo — build plan

Source of truth: `WALLTEST_Project_Proposal_DB_Design.pdf` (sections 1–9). Every divergence is logged in `DEVIATIONS.md`.

| # | Milestone | Done when |
|---|-----------|-----------|
| M0 | Scaffold, docker compose (pgvector/pgvector:pg16), git | `docker compose up db` healthy, extensions load |
| M1 | Schema: 20 tables, roles, RLS, triggers, exclusion constraint, composite FKs | migrations apply on a clean DB; pytest negative-constraint suite green |
| M2 | Statistics in SQL (log-space binomial, CP bound, Holm, MDA, power) | matches scipy / statsmodels on a grid incl. n = 1,543 |
| M3 | Real NIFTY 50 EOD data (ISINs verified), seed, hashed-BoW embedding, gateway functions, audit engine (FAST) | full FAST campaign runs end-to-end through the gateway |
| M4 | LIVE clock, FastAPI + SSE, scripted agents (research, leaky, clean, partial) | live campaign streams real access_events |
| M5 | Frontend: Wall, Run, Verdicts, Inspector, Rules Lab, Compliance, Power, Schema, Show-SQL drawers | `npm run build` clean, pages render from API only |
| M6 | Calibration (>= 2,000 null campaigns at α = 0.05 and 0.001) + power-table reproduction | false-alarm rate <= α within Monte-Carlo error |
| M7 | Playwright e2e + screenshots at 1440x900 and 1366x768, visual fixes | leaky = LEAK, clean = NO_EVIDENCE, partial → correct channel |
| M8 | README, DEVIATIONS, SQL_SHOWCASE, fresh-clone test, final report | clone → README only → demo works |
| M9 | Stretch: optional LLM agents behind an env flag | never required, untuned |
