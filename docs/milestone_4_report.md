# Milestone 4 report — UI

Date: 2026-09-03 · Tag: `m4` · React 18 · TypeScript 5.6 · Vite 5.4 · Plotly 2.35 · D3 7 · Playwright 1.48 · Node 24 (portable)

## What was built (`ui/`)

| screen (reference) | file | what it does |
|---|---|---|
| 1 Loader (Data Loader) | `src/screens/Loader.tsx` | source tabs (files / SQLite / PostgreSQL / SQL Server / Oracle), connection test, table + column mapping with auto-suggest and required-field highlighting, cascading field → reservoir → block filter, well multi-select table with derived type, conversion dates, timeline bars, XY / pressure coverage, quick-select groups, project config save |
| 2 Run | `src/screens/Run.tsx` | objective, posture, horizon, one button; progress with plain-language stages on the five-layer pipeline schematic; previous runs |
| 3 Result | `src/screens/Result.tsx` | confidence badge, D3 well map with f_ij arrows (width ∝ f, opacity ∝ confidence, τ colour, layers, hover, click-select, zoom/pan, scale bar, north arrow, legend, SVG export), forecast fan, "change this week" list with ramp steps, setting hints and revert conditions, data-quality light, create recommendation |
| 4 Details (Tournament Selector) | `src/screens/Result.tsx` (right drawer) | data-quality traffic light and coverage, gates pass/fail, all conditions, leaderboard with excluded methods and reasons, connectivity matrix, injector efficiency, history match per well with residual sub-panel and R²/MAPE, Δt/τ plot with verdict, pressure handling, change alerts map, tornado |
| 5 Workflow | `src/screens/Workflow.tsx` | recommendation list with states, state diagram, snapshot / hashes, recommended vs implemented rates with deviations, history with actor and acting role, review / approve (override reason required for LOW) / reject / implement (operations) / evaluate at 3–6 months |
| 6 Admin | `src/screens/Admin.tsx` | users and roles (add by e-mail, deactivate never delete), connections with secret status, thresholds (global / per project) and separation policy, validation dashboard (Tier 3 hit rate per badge), audit log, webhooks |
| 7 Advanced (reviewer+) | `src/screens/Advanced.tsx` | manual variant, solver settings, sector editor, gate overrides; saved per project for the next run and shown in the approval view |

Shared: `src/styles/tokens.css` (§4.1 tokens, fonts, focus rings, skeletons, tables, drawer), `src/plotTemplate.ts` (one Plotly template, hover with well/date/value + unit, PNG 2× and SVG export, copy data), `src/plots/Plots.tsx`, `src/maps/WellMap.tsx` (well map + connectivity matrix), `src/components/*` (badge with icon + label, plain-language messages, virtualised sortable filterable table, pipeline and state schematics), `src/api/client.ts` (typed client, token + acting role, plain-language error mapping).

Backend additions for the UI: `GET /runs/{id}/details` result bundle (per-well series, fans, map data, gates, leaderboard, conditions) and display-unit conversion at the API edge.

## Verification

```
ui: npx tsc -b              clean
ui: npx vite build          ok (5.0 MB bundle, Plotly dominates)
ui: npx playwright test     6 passed (50 s; primary path incl. a full engine run in 33 s)
python -m pytest -q         100 passed (API tests include the result bundle and unit conversion)
python -m ruff / mypy       clean
```

Playwright (Edge channel, against the dev API and Vite server) covers the primary path — login → create project → connect to the `streak_5x4` folder → suggested mapping → derived wells → run → result (badge, map, action list, fan) → Details tabs → create recommendation → review → approve → implement — plus failure paths (wrong password, no project, viewer on Admin), the admin user flow with audit entries, the tablet-width loader and the advanced-mode save. Screenshots are written to `docs/screens/m4_*.png` by the suite.

§4.6 checklist per screen: tokens applied, no default browser styling, every number carries a unit (from the project's unit system), every plot has axis titles, legend, hover and export, empty / loading (skeleton) / error (messaging-map sentence + action) / partial-data states designed, screenshot committed, Playwright primary + failure path.

## Screens (docs/screens)

`m4_01_loader_mapping.png` · `m4_02_loader_wells.png` · `m4_03_run_progress.png` · `m4_04_result.png` · `m4_05_details_tournament.png` · `m4_06_details_dt_tau.png` · `m4_07_details_fit.png` · `m4_08_workflow_approved.png` · `m4_09_admin_users.png` · `m4_10_loader_tablet.png` · `m4_11_advanced.png`

## Findings and changes (DECISIONS.md, M4)

1. **Units.** The loader converts to SI on load and nothing converted back; the first Result screen showed m³/d labelled as bbl/d. Conversion now happens at the API edge (bundle, recommendations, action list) and inputs are converted back on the way in.
2. **Node.** Installed as a portable zip under the tools folder; no system-wide change.
3. **Playwright** uses the installed Edge, so it runs offline without a browser download.
4. **Bundle size.** Plotly is 5 MB minified; accepted for now (every screen after Run uses it).

## Not done / open

- The tornado on Details is a placeholder sensitivity of the plan's objective; the NPV objective is not yet exposed on the Run screen with economics inputs (M5 reports/economics).
- The change-alert map draws CUSUM alerts from the run's conditions, but the engine does not yet run the rolling re-fit inside a standard run (surveillance wiring, M5).
- No dark/light toggle: the product is dark-only by design (§4.1 tokens).
- Fonts load from Google Fonts; offline deployments must vendor them (M5).
- Accessibility was checked by construction (labels, roles, focus rings, keyboard on tables and drawer); no automated contrast audit yet.

## Next

Milestone 5 — reports (PDF/DOCX), exports, Docker Compose profiles, backup, load test. Waiting for review.
