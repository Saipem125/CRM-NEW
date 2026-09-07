# Milestone 5 report — reports, deployment, hardening

Date: 2026-09-04 · Tag: `m5` · version 0.5.0

## What was built

| area (spec) | where | what it does |
|---|---|---|
| Report PDF / DOCX / HTML (§14, §4.2, §18) | `waterflood_app/outputs/report.py`, `templates/`, `figures.py` | one context from stored objects (registry row, result bundle, recommendation record); Jinja2 HTML with inline SVG figures → PDF through WeasyPrint or a headless Chromium/Edge; DOCX through python-docx with PNG figures; watermark run id · data hash · config hash · code version on every page; recommendation reports add workflow history, implemented vs recommended, evaluations, override reason and the "approved by originator" flag |
| Figures | `outputs/figures.py` | well map with f_ij arrows (width ∝ f, opacity ∝ confidence, scale bar, north arrow, legend), connectivity matrix with τ footer, forecast fan (P10–P90, P50, hold-current dashed, blind window), injector efficiency (current vs optimized), leaderboard with excluded methods and reasons, history match small multiples with residual strips and R²/MAPE, Δt/τ step response with adequacy-coloured sampling dots, outcome range, economic tornado |
| Tornado (§12) | `optimize/sensitivity.py` | ΔNPV plan − hold-current under oil price ±30 %, water cost ±50 %, injection cost ±50 %, discount ±5 pts, plus the P10/P90 member range; stored with every recommendation; economics inputs on the Run screen |
| Exports (§14) | `outputs/export.py` | XLSX (README + 13 sheets, unit-suffixed columns, frozen headers) and CSV zip; model JSON (f_ij, τ, τ_ij, J, extras, metrics per sector/window); `GET /runs/{id}/export.{xlsx,csv,json}`, `GET /recommendations/{id}/export.*` |
| Writeback (§18 Integration) | `outputs/writeback.py`, `POST /recommendations/{id}/writeback` | approver-gated, approved state only, deployment switch, connection flagged `wfo_writeback=1`; one traceable row per injector into a SQL table (created on first write) or CSV; stored in `writebacks`, audited, `writeback.done` webhook; Workflow screen panel |
| Health | `GET /health`, `GET /health/ready` | readiness = store query + write probe, snapshot folder, disk > 200 MB, job pool, PDF renderer; 503 when degraded; Admin → System tab |
| Deployment | `Dockerfile` (targets `api`, `offline`), `ui/Dockerfile` + `nginx.conf`, `docker-compose.yml`, `.env.example` | profiles `dev` (reload API + Vite), `prod` (nginx UI → API, persistent volume, nightly backup sidecar), `offline` (one container: API + built UI under one process, SQLite, in-process worker); health checks on every service; CI job builds all three images and validates the compose profiles |
| Backup | `scripts/backup.py` | SQLite online-backup copy + Parquet snapshots + SHA-256 manifest → tar.gz; `verify`, `restore` (refuses non-empty targets), `--retain N`, secrets excluded by default |
| Load test | `scripts/load_test.py`, `docs/load_test_300.json` | 300 wells / 10 sectors / 120 months full tournament + optimizer on 8 cores: 328 s; the optimizer's forecast now continues from a cached end-of-history state (exact, `tests/test_forecast_fast.py`) |
| Offline UI | `ui/public/fonts`, `src/styles/fonts.css` | fonts vendored (no Google Fonts request) |
| Guides (§6) | `README.md`, `docs/USER_GUIDE.md`, `docs/ADMIN_GUIDE.md` | quick start, engineer path, deployment / users / connections / writeback / health / backups / capacity |
| Samples | `docs/samples/` | `streak_5x4_report.pdf` / `.docx` / `.html`, `streak_5x4_tables.xlsx`, `_tables_csv.zip`, `_model.json` from `scripts/sample_report.py` |

## Verification

```
python -m pytest -q          133 passed (27:47; includes 7 report/export/writeback, 4 backup, 8 fast-forecast tests)
python -m ruff / mypy        clean (88 source files)
ui: npx tsc -b               clean
ui: npx playwright test      6 passed (57 s) — primary path now also downloads XLSX/JSON/DOCX from Result and the PDF from Workflow, creates a writeback connection and writes 5 targets
scripts/load_test.py         see below
docker                       images built and compose profiles validated on GitHub Actions (docker-build job); stack not started end to end
```

### Load test (300 wells, 10 sealed sectors × [12 inj + 18 prod], 120 months, full tournament, 8 cores)

| run | engine (gates + tournament) | optimizer (10 sectors) | total | bound |
|---|---|---|---|---|
| sector-serial engine and optimizer, machine also running Playwright + pytest | 280 s | 386 s | 666 s | fail |
| sector-parallel engine and optimizer (process per sector), idle machine | 306 s | 498 s | 805 s | fail |
| forecast continued from the cached history state (exact), per-producer parallelism, idle machine | 280 s | 47 s | 328 s | **pass** |

All 10 sectors fit with CRMP as winner, HIGH confidence, mean |f_ij − truth| = 0.000 (the case is noise-free).

### Report and export checks (tests/test_api_m5.py, 7 tests)

- HTML/PDF/DOCX for a run and for a recommendation; watermark contains the run id; 404 unknown run; 422 unknown format; audit rows for every report and export.
- XLSX sheet set and headers; CSV zip contents; model JSON shape and units; recommendation exports add `workflow` / `rates`; asset scope enforced (403).
- Writeback: 409 before approval, 403 for engineer / reviewer / operations / viewer, 409 on an unflagged read connection with the plain sentence, CSV target rows carry recommendation id and approver, SQL target (SQLite) creates the table and inserts 5 rows, list endpoint, audit with acting role, deployment switch → 409.
- Readiness probe content; single-process root app serves `/api/health`, `/`, SPA fallback and assets.
- Figure engine SVG/PNG, ticks and number formatting; economic tornado shape and non-negative oil gains on an NPV plan.

### Backup (tests/test_backup.py, 4 tests)

Round trip backup → verify → restore with two snapshots; secrets excluded/included; consistent copy while another connection holds an open transaction; retention keeps the newest N; verification detects a tampered Parquet; CLI.

## Findings and changes (see DECISIONS.md, M5)

1. **WeasyPrint does not import on the Windows build machine** (no GTK/Pango) — renderer chain WeasyPrint → headless Chromium/Edge → 409 with a plain sentence; Linux images install Pango/Cairo. Edge's launcher returns before the child has written the PDF, so the renderer polls for a stable file and cleans its temp profile best-effort.
2. **NPV magnitude was per m³, not per bbl** (`bbl_per_unit` defaulted to 1); rankings unaffected. Fixed. The action list's "expected oil gain" was in objective units (currency for NPV) — now always the ensemble-mean cumulative-oil gain.
3. **The 300-well run was 666 s.** Profiling showed the optimizer re-simulating the whole history for every one of ~6 000 objective evaluations per sector; the forecast now continues from a cached history state (identical numbers, 8× faster per sector). Sector-level process pools were tried for both phases and were not faster (kept behind `solver.sector_parallel: false`). Engine time is CRMIP-bound (22 of 28 s per sector).
4. **Compose profiles ship what was verified**: SQLite + Parquet store and the in-process job pool; PostgreSQL / Redis were not available to test a swap against and are documented swap points, not declared services. Docker is not installed on the build machine, so "working from a clean clone" is delegated to the CI `docker-build` job and a Docker host.

## Screens (docs/screens)

`m5_01_result_downloads.png` · `m5_02_workflow_writeback.png` · `m5_03_run_npv_economics.png`

## Review follow-up (same day)

| item | outcome |
|---|---|
| rolling re-fit + CUSUM alerts inside standard runs | done — `rolling.mode`, blended forecast ensemble, `tests/test_surveillance.py` (4 tests incl. a real synthetic shift) |
| PostgreSQL store | done — `store/db.py` shim, verified on PostgreSQL 16 (3 tests: store/registry/audit, full API flow, readiness — passed locally on PostgreSQL 16.4 portable binaries); CI postgres service; `pg_dump` backups |
| worker service | done — DB-table queue, `python -m waterflood_app.api.worker`, atomic claim, artefacts persisted (`tests/test_worker.py`) |
| Docker execution | not possible on the build PC (no Docker); verified on GitHub Actions instead — run https://github.com/Saipem125/CRM-NEW/actions (commit `2e8b0d1`): `docker-build` builds the api, offline and ui images and validates the `dev` / `prod` / `offline` profiles; Python 3.11 + 3.12 jobs (with a PostgreSQL service) and the UI build all green |

Load test after the follow-up (rolling re-fit active in every 30-well sector): engine 343 s (rolling windows in all 10 sectors) + optimizer 50 s = 394 s on 8 cores, bound 600 s — pass (`docs/load_test_300_rolling.json`).

## Not done / open

- The compose stacks have been built and validated on CI but not started end to end (no `docker compose up` on a Docker host yet).
- The report's tornado is economic only for the NPV objective (the oil objective carries the forecast range alone).
- No automated contrast audit of the UI; PNG report figures use host fonts (DejaVu / Arial / Consolas), not the brand fonts.

## Deliverables checklist (prompt §6)

- [x] Repo per §19 with CI (python matrix + ui-build + docker-build)
- [x] Five frozen synthetic fixtures with truth files and generator (M0)
- [x] Engine passing fixture acceptance criteria (M1–M2)
- [x] API with OpenAPI docs and state-machine tests (M3)
- [x] Seven UI screens meeting §4 with committed screenshots (M4)
- [x] Messaging map complete for every emitted Condition (M0–M2)
- [x] Report templates producing a sample PDF from `streak_5x4` (`docs/samples/`)
- [~] Docker Compose `dev` / `prod` / `offline` — written and CI-built, not executed on this machine
- [x] `docs/USER_GUIDE.md` and `docs/ADMIN_GUIDE.md`
- [x] `docs/DECISIONS.md`

## First field data

See `docs/field_run_ALFA.md` (2026-09-07): four runs on a 48-well field, what broke, what was fixed, and why the result is LOW.
