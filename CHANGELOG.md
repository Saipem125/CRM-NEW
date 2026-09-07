# Changelog

## [0.5.1] — 2026-09-04 — review follow-up

- Engine: rolling re-fit + CUSUM change alerts inside standard runs (`rolling.mode`, `rolling.max_wells_in_run`); forecast ensemble blended with the latest window (§10).
- Store: PostgreSQL backend through `store/db.py` (`WFO_DB_URL`), snapshots/artefacts on the shared folder; `pg_dump`/`pg_restore` in `scripts/backup.py`.
- Jobs: `WFO_JOBS_MODE=external` + `python -m waterflood_app.api.worker`; run artefacts persisted under `artifacts/` and reloaded on demand (scenarios survive restarts).
- Deployment: `prod` profile = postgres + api + worker + ui + backup; CI runs the store tests against a postgres service; `pip install ".[postgres]"`.
- Tests: `test_surveillance.py`, `test_store_postgres.py`, `test_worker.py`.
- CI (first run on GitHub, `Saipem125/CRM-NEW`): compose validation steps get the mandatory passwords; mypy target no longer pinned to 3.11 (numpy stubs use the 3.12 `type` statement).

## [0.5.0] — 2026-09-04 — Milestone 5: reports, deployment, hardening

- `outputs/`: figures (SVG + PNG scene graph), HTML/PDF/DOCX report (`GET /runs/{id}/report.{pdf,docx,html}`, `GET /recommendations/{id}/report.*`), XLSX / CSV-zip / model-JSON exports, approver-gated writeback of injection targets (`POST /recommendations/{id}/writeback`, `GET …/writebacks`).
- `optimize/sensitivity.py`: economic tornado stored with every recommendation; NPV unit factor fixed; action-list expected oil gain always a volume.
- API: `/health/ready` readiness probe, `create_root_app` (single-process API + UI), version 0.5.0; `writebacks` table; `integration` config section.
- UI: Download menu on Result (PDF, DOCX, XLSX, CSV, JSON), report buttons and writeback panel on Workflow, NPV economics inputs on Run, Admin → System health tab, vendored fonts (offline).
- Deployment: `Dockerfile` (api / offline targets, WeasyPrint runtime), `ui/Dockerfile` + nginx, `docker-compose.yml` profiles `dev` / `prod` / `offline` with health checks and a backup sidecar, `.env.example`.
- Scripts: `backup.py` (backup / verify / restore), `load_test.py` (300 wells, 8 cores), `sample_report.py` (docs/samples from `streak_5x4`).
- Docs: `README.md`, `docs/USER_GUIDE.md`, `docs/ADMIN_GUIDE.md`, sample report set under `docs/samples/`.

## [0.4.0] — 2026-09-03 — Milestone 4: UI

- `ui/`: Vite + React 18 + TypeScript app with the seven screens (Loader, Run, Result, Details drawer, Workflow, Admin, Advanced), §4.1 design tokens, one Plotly template (forecast fan, history match with residuals, Δt/τ, leaderboard, injector efficiency, tornado), D3 well map with connectivity arrows and connectivity matrix, pipeline and state-machine schematics, virtualised sortable tables, plain-language errors from the messaging map, Playwright end-to-end tests with committed screenshots.
- API: `GET /runs/{id}/details` result bundle; display-unit conversion at the edge.

## [0.3.0] — 2026-09-03 — Milestone 3: API and users

- `api/`: FastAPI application (`/auth`, `/users`, `/projects`, `/connections`, `/mapping`, `/wells`, `/runs` with async jobs, `/scenarios`, `/recommendations/{id}` transitions, `/evaluations`, `/admin/thresholds`, `/admin/audit`, `/webhooks`, `/health`), bearer-token auth with roles, acting-role header, SSO pass-through, bootstrap admin, persistent job runner, signed webhooks.
- `ingest/sql.py`: SQLAlchemy connectors (SQLite, PostgreSQL, SQL Server, Oracle, MySQL), secrets store, audited query override.
- Store schema extended (users, connections, mappings, jobs, webhooks, settings); project-level and global threshold overrides.

## [0.2.0] — 2026-09-03 — Milestone 2: extended models, optimization, workflow

- `models/`: CRM–Aquifer (CRMPA, Fetkovich tank as a tank-driven pseudo-injector), two-phase coupled CRM (Corey fractional flow, τ(t)), crossflow / productivity-coefficient CRM, MPI prior and new-well bridge (§11), forecast continuation with P10/P50/P90 fans, rolling windows and CUSUM change alerts (§10).
- `optimize/`: objectives (cumulative oil, NPV with price deck, min water for target oil), constraints, postures (aggressive / balanced / robust with no-loss rejection), SLSQP multi-start solver, water-budget / shut-in / new-well / conversion / pattern-balancing scenarios, ramping and the "change this week" action list with revert conditions (§12–§14).
- `workflow/`: Draft → Reviewed → Approved → Implemented → Evaluated state machine with immutable approved snapshots, separation policy, LOW-confidence override, evaluation records at 3/6 months, the §15 rules table, alerts.
- `store/`: project store (Parquet snapshots + SQLite), run registry keyed by the reproducibility triple, audit log, validation record.
- M0 optimizer truth re-frozen (generator 1.1.0, horizon accounting); tournament now fits aquifer, two-phase and crossflow when eligible.

## [0.1.0] — 2026-09-03 — Milestone 1: engine core

- `ingest/`: canonical schema + Loader-reference auto-mapping (CSV folder, Excel workbook, OFM-style names), units service (unit tags → SI, PVT constants/table, reservoir volumes, free gas, datum), well types from rates (P/I/B per step, PROD/INJ/MIXED with conversion dates, role split, commingled split, ID normalisation).
- `prep/`: monthly grid with the M0 time convention, Hampel cleaning (producers only), event windows, distance-graph + block sectorization, identifiability gates emitting Conditions, per-source pressure preparation, train/blind split.
- `models/`: CRMT/CRMP/CRMIP with an in-house analytic-gradient solver (VarPro + multi-start, joint Σ_j f_ij ≤ 1 refinement, optional BHP term, distance mask) and a pywaterflood engine; power-law WOR–CWI and Koval oil cut; verification (blind R², MAPE, AICc, autocorrelation, plausibility); multi-start UQ; tournament with the Tournament-Selector rules and the HIGH/MEDIUM/LOW badge.
- `messaging/condition_map.yaml` with all §17 rows plus 24 loader/QC/model conditions; a test keeps codes and map in sync.
- `engine.run_engine` end-to-end driver; external fixture set vendored and tested.

## [0.0.1] — Milestone 0 (2026-09-03)
- Repository scaffold per §19; pyproject with ruff / mypy --strict / pytest; GitHub Actions CI.
- `docs/spec/` holds the four source-of-truth HTML documents; `docs/BUILD_PROMPT.md` the build prompt.
- Synthetic ground-truth suite (§16 Tier 1): `streak_5x4` (noise-free + 5 % noise), `aquifer_6x9`, `converted_wells`, `allocated_noisy`, `sectored_60`, each frozen as Parquet with `truth.json`, produced by `waterflood_app/validation/synthetic_suite/generate.py`.
- `tests/test_fixtures.py`: pywaterflood recovers `streak_5x4` f_ij within 10 % and τ within 20 %.
