# Synthetic validation fixtures — Waterflood Optimizer (§16 Tier 1)

| case | wells | months | tests |
|---|---|---|---|
| streak_5x4_clean | 5 inj / 4 prod | 120 | parameter recovery (f_ij ±10 %, tau ±20 %), blind R² ≥ 0.9 |
| streak_5x4_noisy | same | 120 | robustness to 5 % production noise |
| allocated_noisy | same + days-on, shut-ins, ESP/choke events | 120 | QC layer, event windowing, allocation noise 8 % |
| aquifer_9x9 | 9 / 9 | 160 | influx signature detection, CRM-Aquifer selection, PV & influx recovery |
| converted_wells | 2 inj + 5 prod, 2 converted | 96 | MIXED well-type derivation, role split, window segmentation |
| sectored_60 | 60 | 96 | sectorization, no cross-sector connectivity, runtime < 10 min |

All rates are surface volumes (bbl/d averaged over the month, scaled by days_on) generated from a CRM forward
model in reservoir volumes with Bo=1.25, Bw=1.02. Convert back to reservoir volumes before fitting.
Each folder: rates.csv, pressure.csv (ESP-style flowing BHP), coords.csv, category.csv, truth.json, optional events.csv.
Column names match the app's canonical schema so the loader auto-maps them; rename columns to test mapping.

## Extras
- `allocated_noisy/allocated_noisy_workbook.xlsx` — same case as one Excel workbook (sheets RATES, PRESSURE, HEADER, CATEGORY, EVENTS) for the Excel loader path.
- `streak_5x4_clean/ofm_style/` — same case with OFM-style column names (WELL_NAME, PROD_DATE, OIL_VOL, WINJ_VOL, FBHP, X_COORD …) to test column auto-mapping.
- `generate_fixtures.py` — regenerates everything deterministically (seeded). Do not regenerate after freezing; bump a version folder instead.
- `verify_fixtures.py` — independent two-stage (tau-grid + NNLS) CRMP check; prints recovery metrics vs truth.json. Use as the CI smoke test.

## Verified recovery (independent solver, distance-masked, producing days only)
| case | median blind MAPE | key check |
|---|---|---|
| streak_5x4_clean | 0.6 % | tau within 1–2 d of truth; streak I-3→P-4 largest f |
| streak_5x4_noisy | 4.6 % | at 5 % noise floor; streak identified |
| allocated_noisy | 5.5 % | passes only when days_on is honoured — a solver that ignores shut-ins fails |
| aquifer_9x9 | 3.6 % | flank apparent Σf 1.35 / 1.26 / 1.09 vs interior median 0.84 |
| converted_wells | 3.8 % | P-2, P-5 derived as MIXED with conversion at 2020-01 |
| sectored_60 | 4.4 % | runtime + no cross-sector connectivity |
