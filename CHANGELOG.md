# Changelog

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
