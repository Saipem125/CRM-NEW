# CLAUDE CODE — BUILD PROMPT
## Waterflood Injection Evaluation & Optimization Application

You are building a production-grade application from a finished architecture. Do not redesign; implement. Where the spec is silent, choose the simplest option that keeps the user flow unchanged, and record the choice in `docs/DECISIONS.md`.

---

## 0. Read first

The following files are in `docs/spec/` and are the source of truth, in this priority order:

1. `Waterflood_Optimizer_Architecture.html` — the complete architecture (21 sections). Every requirement below traces to a section number (§).
2. `CRM_Data_Loader.html` — interactive reference for the load / mapping / field-filter / well-selection screens (§5). Match its behaviour, not its styling.
3. `CRM_Model_Tournament_Selector.html` — reference for the "Details" panel: eligibility gates, exclusions with reasons, ranking, Δt/τ check, pressure handling (§8, §9).
4. `CRM_Atlas_Waterflood_Optimization_Dashboard.html` — methodology reference and equations for every CRM variant (§9). Use its formulations; do not invent alternatives.

Open all four in a browser before writing code. Extract every equation you implement from the Atlas and cite the section in a docstring.

---

## 1. Mission and non-negotiables

Deliver a web application in which a reservoir engineer can: connect to data → confirm column mapping → pick a field and wells → press Run → receive a connectivity map, a forecast with uncertainty, and a ramped "change this week" injection list with a confidence badge, then route it through Draft → Reviewed → Approved → Implemented → Evaluated.

Non-negotiables:
- **Encapsulation** (§1, §2): the main path never shows method names, τ, O_d, solver settings. All of that lives in a collapsed "Details" panel and a role-gated advanced mode.
- **Physics first** (§1, §9): every deployed model exposes f_ij, τ, J. Pure black-box forecasters are benchmark-only and never drive the optimizer.
- **Evidence before recommendation** (§9, §16): nothing is shown without a blind test and a HIGH/MEDIUM/LOW confidence badge. LOW cannot be approved without approver override with a reason.
- **Reproducibility** (§1, §18): every run stores data-snapshot hash, config hash, code version. Re-running the same triple yields identical results (fixed seeds).
- **Plain language** (§17): every technical condition maps to one sentence + one action from `messaging/condition_map.yaml`. No raw metric ever reaches the main path.
- **Gas injection / WAG is out of scope** (§20). Detect free gas, flag reduced confidence, do not model it.

---

## 2. Stack (decided — do not substitute)

| Layer | Choice |
|---|---|
| Language | Python 3.12, type-hinted, `ruff` + `mypy --strict` clean |
| Numerics | NumPy, SciPy (`least_squares`, `minimize` SLSQP / trust-constr), `pywaterflood` as Phase-1 CRMP/CRMIP/MPI engine and permanent regression baseline |
| Data | polars for pipelines, pandas only at library boundaries; Parquet snapshots; SQLite for dev, PostgreSQL for deploy (SQLAlchemy 2.x, Alembic migrations) |
| API | FastAPI, Pydantic v2, background jobs via RQ (Redis) with a synchronous fallback for offline mode |
| UI | **React 18 + TypeScript + Vite**. State: TanStack Query. Charts: **Plotly.js** for all quantitative plots, **D3** for the well map and connectivity graph, **react-flow** is NOT to be used. Styling: Tailwind with the design tokens below. No component library that imposes its own look (no MUI/AntD). |
| Reports | WeasyPrint (HTML→PDF) and python-docx, templates in `outputs/templates/` |
| Auth | Local accounts (argon2) with an SSO pass-through hook (OIDC) behind a feature flag |
| Tests | pytest, hypothesis for numerics, Playwright for UI, GitHub Actions CI |
| Packaging | Docker Compose (api, worker, redis, postgres, ui); single-node `--offline` profile with SQLite and in-process worker |

---

## 3. Build order — follow strictly

Do not start the UI before Milestone 3 is green. Each milestone ends with a tagged commit and a passing CI run.

### Milestone 0 — Repository and fixtures (before any feature)
1. Scaffold `waterflood_app/` exactly as §19. Add `docs/DECISIONS.md`, `docs/spec/` (copy the four HTML files), `CHANGELOG.md`.
2. **Synthetic ground-truth fixtures** (§16 Tier 1) in `validation/synthetic_suite/`:
   - `streak_5x4`: 5 injectors, 4 producers, one high-permeability streak; known f_ij, τ; 120 monthly steps; noise-free and 5 % noise variants.
   - `aquifer_6x9`: partial natural water drive; Σf_ij ≈ 1.4 signature; 160 steps.
   - `converted_wells`: two producers converted to injectors at month 48; 96 steps.
   - `allocated_noisy`: monthly-allocated rates with days-on and 8 % allocation noise.
   - `sectored_60`: 60 wells, two sealed sectors, for runtime and sectorization tests.
   Generate them with a documented generator (CRM-forward simulation is acceptable; a simple 2-D single-phase finite-difference simulator is preferred for `streak_5x4` and `aquifer_6x9`). Freeze as Parquet with a `truth.json` per case. Fixtures are immutable after this milestone.
3. `tests/test_fixtures.py`: pywaterflood recovers `streak_5x4` f_ij within 10 % and τ within 20 % on the noise-free case. This test must pass before proceeding.

### Milestone 1 — Engine core (no UI)
- `ingest/units.py` (§6): unit tags, SI storage, reservoir-volume conversion with PVT table or constants, free-gas detection, datum shift. Property-based tests.
- `ingest/welltype.py` (§5): per-step P/I/B classification, PROD/INJ/MIXED with conversion dates, role splitting, commingled split factors, ID normalisation with alias table. Test on `converted_wells`.
- `prep/` (§7, §8): Hampel/MAD cleaning with raw retained, event windowing, distance-graph sectorization, identifiability gates (O_d, points/params, CV, τ/Δt), pressure preparation per source (§8), train/blind split. Every gate emits a `Condition` object keyed to `condition_map.yaml`.
- `models/` (§9): `base.CRMModel` interface (`fit`, `predict`, `params`, `residuals`), then `crmt`, `crmp`, `crmip` (wrap pywaterflood first, then in-house solver with analytic gradients that must match pywaterflood on fixtures within 1 %), `fractional_flow` (power-law WOR–CWI and Koval), `verify` (blind R², MAPE, residual autocorrelation, plausibility checks), `uq` (multi-start ensemble), `tournament` (gates → fit eligible → AICc/blind/stability composite → winner or top-3 ensemble), confidence badge with thresholds in `config/thresholds.yaml` (§9).
- `messaging/condition_map.yaml` seeded with all rows of §17; a test asserts every `Condition` code emitted anywhere in the code base has an entry.
- Acceptance: tournament on all five fixtures selects the expected variant (documented in each `truth.json`), CRMP blind R² ≥ 0.9 on noise-free, ≥ 0.75 on noisy.

### Milestone 2 — Extended models, optimization, workflow logic
- `models/aquifer.py` (CRMPA, Fetkovich tank), `twophase.py`, `crossflow.py`, `mpi.py` (prior and new-well bridge §11), `rolling.py` + `change_detect.py` (rolling windows, CUSUM alerts §10).
- `optimize/` (§12–§14): objectives (cumulative oil, NPV with price deck, min-water-for-target-oil), constraints, postures (aggressive / balanced / robust as defined in §13), SLSQP for short horizons, ensemble optimizer for robust posture, `ramping.py` and `action_list.py` producing the "change this week" list with revert conditions.
- `workflow/` (§3, §15): state machine Draft→Reviewed→Approved→Implemented→Evaluated, immutable approved snapshots, evaluation records at 3/6 months, scheduler rules table from §15 implemented literally, alerts.
- `store/`: project store, run registry, audit log (actor, acting_role, data hash, config hash), validation record.
- Acceptance: optimizer on `streak_5x4` increases oil vs. proportional allocation by the amount in `truth.json` ± 5 %; robust posture never selects a plan that loses oil in any ensemble member.

### Milestone 3 — API and users
- FastAPI routes: `/auth`, `/users` (admin), `/projects`, `/connections` (test + list tables + columns), `/mapping`, `/wells` (derived types), `/runs` (async job, status, results), `/scenarios`, `/recommendations/{id}` with state transitions, `/evaluations`, `/admin/thresholds`, webhooks.
- **User management** (§3 as amended): roles = admin, approver, reviewer, engineer, operations, viewer; a user may hold several; admin may act in any role. Admin adds users by email or account name, assigns roles and asset scope, deactivates (never deletes). Every action logs `actor` and `acting_role`. If originator == approver, the approval carries `approved_by_originator=true`, visible in reports. Separation policy is a setting: `log` (default) or `enforce`.
- Database connectors: SQLite and PostgreSQL first, then SQL Server and Oracle via SQLAlchemy dialects; read-only credentials in a secrets store; query override restricted to reviewer/admin and logged (§18).
- OpenAPI docs complete; Playwright-free API tests cover every state transition.

### Milestone 4 — UI (the only milestone that touches React)
Build these screens, in this order, each matching its reference file's behaviour:
1. **Loader** (§5; reference: Data Loader) — source tabs, connection test, table/column mapping with auto-suggest and required-field highlighting, cascading field filter, well multi-select table with derived type, conversion dates, timeline bars, XY/pressure coverage, project config save.
2. **Run** (§2) — objective, posture, horizon; one button. Progress with plain-language stages ("Cleaning data", "Testing models", "Optimizing").
3. **Result** (§2, §14) — confidence badge, well map with connectivity, forecast fan, change-this-week list, approve/send buttons per role.
4. **Details** (§9; reference: Tournament Selector) — data-quality traffic light, gates with pass/fail, excluded methods with reasons, leaderboard, fit plots per well, Δt/τ plot, pressure handling, change alerts.
5. **Workflow** (§3) — recommendation list with states, approval dialog (reason required for LOW override), implementation entry for Operations, evaluation view.
6. **Admin** — users and roles, assets, connections, thresholds, validation dashboard (§16 Tier 3), audit log.
7. **Advanced mode** (reviewer+) — manual variant, solver settings, sector editor, parameter overrides; every override appears in the approval view.

### Milestone 5 — Reports, deployment, hardening
- PDF/DOCX report with the figures below; XLSX/CSV exports; model JSON; optional writeback endpoint behind approver gate.
- Docker Compose profiles `dev`, `prod`, `offline`. Health checks. Backup script for Parquet + DB.
- Load test: 300-well project runs a full tournament in under 10 minutes on 8 cores.

---

## 4. UI quality bar — this is where most tools fail; do not

### 4.1 Design tokens (apply everywhere; no exceptions)
```
bg #0B121C · bg2 #0F1826 · panel #131E2E · panel2 #17253A · border #25344B
text #E8E6DC · dim #93A0B4 · faint #5E6C82
water/injection #4C9BD4 · oil/production #E0973F · accent #5CC2AE
danger #D2685A · good #7DB88A · violet/mixed #9F8FD0
Fonts: Space Grotesk (headings), IBM Plex Sans (body), IBM Plex Mono (data, IDs, units)
Radius 6 px · borders 1 px · no drop shadows · no gradients except the brand mark
```
Injection is always blue, production always orange, mixed always violet, confidence HIGH/MEDIUM/LOW always good/oil/danger. Never rely on colour alone: pair with an icon or label.

### 4.2 Plots (Plotly)
- Consistent template registered once (`ui/src/plotTemplate.ts`): dark background matching tokens, gridlines `#25344B`, axis titles with units in mono, legend below plot, no Plotly logo, hover shows well ID, date, value with unit.
- **Forecast fan**: P10–P90 band (accent at 18 % opacity), P50 line, history in dim, blind-test window shaded, implemented-plan overlay dashed, event markers as vertical ticks with hover text.
- **History match per well**: raw (faint points), cleaned (dim line), model (oil line), residual sub-panel; blind window shaded; R² and MAPE in the corner in mono.
- **Δt / τ plot**: exponential step response with sampling dots coloured by adequacy, exactly as the Selector reference.
- **Leaderboard**: horizontal bars, method colour, excluded methods greyed with reason on hover.
- **Injector efficiency**: bar chart oil per bbl injected, sorted, with current vs. optimized side by side.
- **Tornado**: economic sensitivity, centred, labelled.
- All plots exportable as PNG (2× DPI) and SVG; all have a "copy data" button.

### 4.3 Maps and schematics (D3, SVG)
- **Well map**: true coordinates (project CRS, no distortion), injectors as downward triangles, producers as circles, mixed as split circle/triangle; f_ij as arrows whose width ∝ f_ij and opacity ∝ confidence; τ optionally as arrow colour scale; sector boundaries as dashed polygons; faults if provided; hover shows pair values; click selects a well and highlights its connections; toggle layers; zoom/pan; scale bar; north arrow; legend.
- **Connectivity matrix**: heat-map f_ij with τ in cell tooltip; rows injectors, columns producers; sortable; excluded pairs hatched.
- **Change-alert map**: same base map, alerts as pulsing rings on the affected pair, side panel listing plain-language alerts.
- **Pipeline schematic** on the Run screen: the five layers lighting up as the job progresses.
- **Workflow state diagram** on the recommendation page: current state highlighted.
- All SVGs use CSS variables for colour so they theme correctly and export cleanly.

### 4.4 Tables
- Mono for IDs, dates, numbers; right-aligned numbers with thousands separators and unit in header; sticky headers; virtualised for > 200 rows; sortable; column filters; row selection with count.

### 4.5 Interaction and accessibility
- Every screen usable at 1280 px and 1920 px; loader and result screens also at tablet width.
- Keyboard navigation for tables and dialogs; focus rings visible; contrast ≥ 4.5:1 for text.
- Loading states are skeletons, never spinners alone; errors are the plain-language sentence + action from the messaging map, never a stack trace.
- No modal for anything the user might want to compare side by side (Details opens as a right drawer).

### 4.6 Definition of "excellent" for a screen (checklist, all required)
- Matches reference behaviour; tokens applied; no default browser styling visible.
- Every number has a unit; every plot has axis titles, legend, hover, export.
- Empty, loading, error, and partial-data states all designed.
- Screenshot committed to `docs/screens/` and reviewed against the reference.
- Playwright test covers the primary path and one failure path.

---

## 5. Engineering rules

- Equations: implement from the Atlas; unit-test each against an analytic case (e.g. single-tank step response = q₀e^{-t/τ} + i(1−e^{-t/τ})).
- Seeds fixed everywhere; multi-start seeds derived from config hash.
- No silent drops: unmatched well IDs, unmapped columns, unparseable dates are reported as Conditions.
- Thresholds, weights, ramp limits, posture parameters live in `config/*.yaml`, never in code.
- Logging: structured JSON; every job logs data hash, config hash, git SHA.
- Performance: per-sector parallelism with `joblib`; tournament caches fitted models by (data hash, config hash, variant).
- Docstrings cite the architecture section and, for models, the paper.
- Commit messages reference the milestone and section, e.g. `M1 §7: identifiability gates`.
- After each milestone, update `docs/DECISIONS.md` and `CHANGELOG.md`, and write a short `docs/milestone_N_report.md` with test results and screenshots.

---

## 6. Deliverables checklist

- [ ] Repo per §19 with CI green on all milestones
- [ ] Five frozen synthetic fixtures with truth files and generator script
- [ ] Engine passing fixture acceptance criteria (Milestones 1–2)
- [ ] API with OpenAPI docs and state-machine tests
- [ ] Seven UI screens meeting §4 checklist with committed screenshots
- [ ] Messaging map complete for every emitted Condition
- [ ] Report templates producing a sample PDF from `streak_5x4`
- [ ] Docker Compose `dev` / `prod` / `offline` working from a clean clone
- [ ] `docs/USER_GUIDE.md` (engineer path, 2 pages) and `docs/ADMIN_GUIDE.md`
- [ ] `docs/DECISIONS.md` listing every choice made where the spec was silent

---

## 7. How to work

- Work milestone by milestone. At the end of each, stop, run the full test suite, produce the milestone report, and ask for review before starting the next.
- If a requirement in this prompt conflicts with the architecture document, the architecture document wins; note the conflict in `DECISIONS.md`.
- If real field data is provided during the build, run the loader and engine on it immediately (Milestone 1 onward) and report what broke before continuing.
- Do not add features not in the spec. Do not add gas/WAG. Do not replace Plotly/D3 with another library.

Begin with Milestone 0.
