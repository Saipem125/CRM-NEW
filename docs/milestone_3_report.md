# Milestone 3 report — API and users

Date: 2026-09-03 · Tag: `m3` · Python 3.11.9 · FastAPI 0.141 · Windows 10

## What was built

| module | content (§) |
|---|---|
| `api/app.py` | FastAPI application, 36 paths / 44 operations: `/auth`, `/users`, `/projects`, `/connections` (test, tables, columns, query override), `/mapping` (suggest, save, get), `/wells` (derived types, category tree), `/runs` (async job, status, results), `/scenarios`, `/recommendations/{id}` with `review / approve / reject / implement`, `/evaluations` (+ calibration), `/admin/thresholds`, `/admin/separation-policy`, `/admin/audit`, `/webhooks`, `/health` (§18) |
| `api/auth.py` | users (bcrypt or SSO pass-through), six roles, multiple roles per user, admin acts in any role, `X-Acting-Role`, JWT bearer tokens, deactivate-never-delete, bootstrap admin (§3.1) |
| `api/service.py` | the operations behind every route: role and asset-scope enforcement, audit of every action with actor and acting role, snapshots, jobs, recommendation lifecycle, evaluations, thresholds, webhooks |
| `api/jobs.py` | persistent job table + in-process worker pool behind a queue interface (Celery/RQ swap later); inline mode for tests (§19) |
| `ingest/sql.py` | SQLAlchemy connectors (SQLite, PostgreSQL, SQL Server, Oracle, MySQL), secrets store, read-only reflection and table reads, audited SELECT override (§5, §18) |
| `store/project.py` | schema extended with users, connections, mappings, jobs, webhooks, settings; project config updates; settings for global threshold overrides and the separation policy |

## Test results

```
python -m pytest -q        100 passed
python -m ruff check .     All checks passed
python -m mypy             Success: no issues found in 80 source files
```

Acceptance (prompt, Milestone 3):

| criterion | result |
|---|---|
| routes listed in the prompt | all present (see the table above); OpenAPI document generated with tags and summaries |
| user management as amended §3 | roles, multi-role users, admin acting in any role, add by e-mail/account name, asset scope, deactivate, `actor` + `acting_role` on every audit entry, `approved_by_originator` visible on the recommendation, separation policy `log` / `enforce` switchable by admin |
| database connectors | SQLite tested end-to-end (create → test → tables → columns → suggest → query override); PostgreSQL/SQL Server/Oracle/MySQL through SQLAlchemy URLs with the driver packages left to deployment; passwords only in the secrets store |
| Playwright-free API tests cover every state transition | `tests/test_api.py`: DRAFT→REVIEWED→APPROVED→IMPLEMENTED→EVALUATED, DRAFT→REJECTED, REVIEWED→REJECTED, every illegal transition and wrong-role refusal, originator approval under both policies, LOW-confidence override |

The API test module runs the engine on `streak_5x4` through the whole path (files connection → suggested mapping → wells → run job → results → scenarios → recommendation → evaluation) in about a minute with synchronous jobs.

## Decisions worth noting (DECISIONS.md, M3)

- SSO is a trusted-header pass-through switched on explicitly; unknown identities are refused, no self-registration.
- Fitted model objects live in process memory per run id; results and parameters persist in the registry, scenarios after a restart return 409 with an explanation.
- Evaluation bands through the API scale the horizon ensemble proportionally to the evaluation window; a re-forecast on realised injection is deferred to the surveillance milestone.
- `variants` and `threshold_overrides` on a run are the API face of advanced mode and are reviewer/admin only.

## Not done / open

- No live PostgreSQL / SQL Server / Oracle test on this machine (no servers); the dialect URLs and the driver names are in place, the driver packages are not pinned.
- Job execution is in-process; a multi-node worker (Celery/RQ) and the monthly scheduler wiring are deployment work (M5).
- No rate limiting, HTTPS termination or CORS policy yet (deployment).
- The React UI (M4) still needs Node.js, which is not installed here.

## Next

Milestone 4 — the seven UI screens against §4. Waiting for review.
