# Waterflood Injection Evaluation & Optimization

CRM-based (capacitance–resistance model) waterflood evaluation and injection optimization —
architecture v2.1. Load production, injection and pressure data; the app derives well types,
checks data quality, runs a model tournament with a blind test, optimizes the injection split and
turns the result into a ramped, approved change list with a confidence badge. Reports (PDF/DOCX),
tables (XLSX/CSV), the fitted model (JSON) and an approver-gated writeback of targets close the loop.

* Engineer path: [docs/USER_GUIDE.md](docs/USER_GUIDE.md)
* Deployment, users, connections, backups: [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md)
* Every choice made where the spec was silent: [docs/DECISIONS.md](docs/DECISIONS.md)
* Milestone reports: `docs/milestone_N_report.md` · changelog: [CHANGELOG.md](CHANGELOG.md)

## Quick start (developer workstation)

```bash
python -m pip install -e ".[dev]" weasyprint python-docx jinja2 xlsxwriter   # Python 3.11+ (WeasyPrint needs GTK on Windows; Edge/Chrome is used as the PDF fallback)
python scripts/dev_api.py                                                 # API on http://127.0.0.1:8000 (OpenAPI at /docs), admin / admin-pass-123
cd ui && npm ci && npx vite                                               # UI on http://localhost:5173 (proxies /api)
```

Tests: `pytest` (Python, ≈ 25 min, the fixture suites dominate), `cd ui && npx tsc -b && npx playwright test` (Edge channel).

## Docker Compose

```bash
cp .env.example .env            # set WFO_ADMIN_PASSWORD
docker compose --profile offline up --build     # one container, SQLite + in-process worker → http://localhost:8000
docker compose --profile prod    up --build     # nginx UI :8080 → API, persistent volume, nightly backups
docker compose --profile dev     up --build     # live-reload API + Vite dev server :5173
```

Health: `GET /health` (liveness) and `GET /health/ready` (store, filesystem, disk, jobs, PDF renderer).
Backups: `python scripts/backup.py backup --store ./wfo_store --out ./backups --retain 14` (`verify`, `restore`).
Load test: `python scripts/load_test.py --cores 8` (300-well full tournament, target < 10 min).

## Layout (architecture §19)

```
waterflood_app/  ingest/ prep/ models/ optimize/ outputs/ workflow/ validation/ messaging/ store/ api/
config/thresholds.yaml   every gate, weight and default (overridable globally / per project from Admin)
ui/                      React 18 + TypeScript + Vite; Plotly plots, D3 map; Playwright tests
scripts/                 dev_api.py · backup.py · load_test.py
docs/                    spec/ (source of truth), DECISIONS.md, milestone reports, screens/, samples/
```
