# Admin guide

## Deployment

Three Docker Compose profiles (`docker-compose.yml`, settings in `.env` — start from `.env.example`):

| profile | what runs | use |
|---|---|---|
| `offline` | one container: API + built UI + SQLite store + in-process worker, port 8000 | single node, no internet, field office |
| `prod` | `postgres` · `api` (queues runs) · `worker` (runs them, scalable) · `ui` (nginx, port 8080, proxies `/api`) · `backup` sidecar (nightly `pg_dump` + Parquet archive, 14 kept) | on-prem / private cloud |
| `dev` | API with live reload (source bind-mounted) · Vite dev server (5173) | developers |

```bash
cp .env.example .env && edit .env        # WFO_ADMIN_PASSWORD is mandatory
docker compose --profile prod up --build -d
docker compose ps                        # both services must be "healthy"
```

Data sources: put CSV / Excel / Parquet folders under `WFO_SOURCES` (default `./data`); inside the
container they appear read-only under `/data/sources/<folder>` — that is the path to enter in the
Loader. Database sources are reached by host name from the container network.

Without Docker: `python -m pip install -e . weasyprint python-docx jinja2 xlsxwriter`, then
`python -m waterflood_app.api.app` (env `WFO_STORE`, `WFO_ADMIN_PASSWORD`, `WFO_HOST`, `WFO_PORT`,
`WFO_UI_DIST=ui/dist` to serve the built UI from the same process). On Windows WeasyPrint needs the
GTK runtime; without it PDF reports are printed through the installed Edge or Chrome
(`integration.report_pdf_renderer`, `WFO_CHROMIUM`).

## Health checks

- `GET /health` — liveness (version, code version).
- `GET /health/ready` — readiness: store reachable and writable, snapshot folder, free disk
  (> 200 MB), job pool alive, PDF renderer available. Returns 503 when degraded. Compose health
  checks poll it; the **Admin → System** tab shows the same probe.

## First start and users

The first admin is created from `WFO_ADMIN_USER` / `WFO_ADMIN_PASSWORD` (or a generated password
printed once in the API log). **Admin → Users**: add users by e-mail or account name, assign one or
more roles (admin, approver, reviewer, engineer, operations, viewer) and the assets they may see;
deactivate — never delete — accounts. Corporate SSO: run behind a proxy that sets the
`X-SSO-User` header and start the API with `WFO_SSO_TRUST=1`; unknown identities are refused.

Roles: engineer runs; reviewer marks reviewed and may use Advanced mode; approver approves / rejects
and may override a LOW badge with a reason; operations records what was set in the field; admin
does everything and may act in any role. **Separation policy** (Admin → Thresholds): `log` records
when the originator approves their own recommendation (visible in reports); `enforce` blocks it.

## Connections and secrets

**Admin → Connections**: file folders, SQLite, PostgreSQL, SQL Server, Oracle, MySQL. Passwords are
written to the secrets store under the store root (`secrets.json`, or `WFO_SECRET_<NAME>`
environment variables) and are never returned by the API. Read connections should use read-only
database accounts (§18). A reviewer or admin may set a per-connection SELECT override; it is audited.

**Writeback** (optional, approver-gated): create a connection for the surveillance system with the
option `wfo_writeback = 1` and write credentials. Approvers can then push an approved
recommendation's injection targets from the Workflow screen (`POST /recommendations/{id}/writeback`);
rows land in `wfo_injection_targets` (SQL table or CSV, configurable) with recommendation, run and
data-hash identifiers. Disable globally with `integration.writeback_enabled: false` or the
`writeback_enabled` setting.

## Thresholds, validation, audit, webhooks

- **Thresholds**: every gate and default from `config/thresholds.yaml` can be overridden globally
  or per project; the effective configuration hash is stored with each run.
- **Validation dashboard**: hit rate of the confidence badges against evaluated recommendations
  (Tier 3); re-tune `confidence` thresholds quarterly so that HIGH means ≥ 80 % within band.
- **Audit**: every load, run, override, approval, implementation, report, export and writeback with
  actor, acting role, data hash and config hash. PII-free (user ids only).
- **Webhooks**: `run.finished`, `recommendation.state_changed`, `recommendation.evaluated`,
  `writeback.done`, `alert`; HMAC-SHA256 signed (`X-WFO-Signature`) when a secret is set.

## Backups and retention

The store is one SQLite file plus immutable Parquet snapshots. `scripts/backup.py` copies the
database with SQLite's online backup API (consistent while the API runs), adds the snapshots and a
SHA-256 manifest, and keeps the newest N archives:

```bash
python scripts/backup.py backup  --store /data/store --out /backups --retain 14
python scripts/backup.py verify  --archive /backups/wfo_<stamp>.tar.gz
python scripts/backup.py restore --archive /backups/wfo_<stamp>.tar.gz --store /data/store_restored
```

Secrets are excluded unless `--include-secrets`. The `prod` profile runs the backup nightly in the
`backup` service; on the `offline` profile schedule it on the host (`docker compose exec wfo python
scripts/backup.py …`). Raw snapshots are immutable and runs are retained for the asset's life.

## PostgreSQL store and the worker service (prod profile)

`WFO_DB_URL=postgresql://user:password@host:5432/db` moves the metadata (projects, runs,
recommendations, users, audit, jobs …) to PostgreSQL; Parquet snapshots and run artefacts stay on
the store folder (`WFO_STORE`), which every API and worker container must share (the `wfo_store`
volume in the compose file). The schema is created on first start. Install the driver with
`pip install ".[postgres]"`; the images include it.

`WFO_JOBS_MODE=external` makes the API queue runs only; start one or more workers with
`python -m waterflood_app.api.worker --store /data/store` (same `WFO_DB_URL`). Scale with
`docker compose --profile prod up --scale worker=3`. A worker claims the oldest queued job
atomically, so several workers never run the same job. With a SQLite store keep one worker.
`/health/ready` reports the queue length in external mode.

Backups of a PostgreSQL store: `python scripts/backup.py backup --store /data/store --out /backups`
with `WFO_DB_URL` set (or `--db-url`) writes `store.pgdump` (custom format) next to the Parquet
snapshots; `restore … --db-url postgresql://…` runs `pg_restore --clean` into that database.

## Change alerts in standard runs

Every run re-fits the rolling windows (36 months, step 6 by default) on the winning model and
raises a plain-language "connection strengthened / weakened since <month>" alert when the CUSUM
detector confirms a shift; the Details → Change alerts map shows the affected pairs. Sectors above
`rolling.max_wells_in_run` (80) skip this inside the run (`rolling.mode: always` forces it).

## Capacity

`python scripts/load_test.py --cores 8` builds a 300-well, 10-sector synthetic field and times the
full tournament and optimization; the acceptance bound is 10 minutes on 8 cores. Set
`WFO_CORES` / `solver.n_jobs` to the cores you give the container; keep `OMP_NUM_THREADS=1`
(joblib parallelises across producers and sectors, BLAS threads only fight with it).
