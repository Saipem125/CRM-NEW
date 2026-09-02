# Synthetic ground-truth suite (§16 Tier 1)

Frozen after Milestone 0. `manifest.json` carries the SHA-256 of every Parquet file and
`tests/test_fixtures.py` fails if any file changes. Regenerate only to re-freeze deliberately
(`python -m waterflood_app.validation.synthetic_suite.generate`, then bump `GENERATOR_VERSION`).

| case | wells | steps | what it tests | expected variant |
|---|---|---|---|---|
| `streak_5x4` | 5 I × 4 P | 120 | parameter recovery, one streak (I-3→P-3, f=0.75), two barriers | CRMP |
| `streak_5x4_noise5` | same | 120 | same with 5 % Gaussian noise on production rates | CRMP |
| `aquifer_6x9` | 6 I × 9 P | 160 | Fetkovich aquifer on the east edge; per-producer Σf signature > 1.15 | CRMPA |
| `converted_wells` | 4 I × 6 P → 6 I × 4 P | 96 | P-5/P-6 converted at month 48; simultaneous P+I month; window break | CRMP (two windows) |
| `allocated_noisy` | 5 I × 4 P | 120 | monthly-allocated volumes, days-on (shut-ins, partial months), 8 % allocation noise | CRMP (CRMIP should lose) |
| `sectored_60` | 24 I × 36 P | 120 | two sealed sectors, fault polyline, runtime | CRMP per sector |

## Files per case
- `rates.parquet` — long format: `well_id, date, days_on, q_oil, q_water, q_inj, bhp`.
  Rates are **calendar-day** bbl/d (monthly volume ÷ days in month); `days_on` is producing/injecting
  days; `bhp` is null (no pressure in these cases). Monthly volume = rate × days in month.
- `coords.parquet` — `well_id, x, y` (metres, project CRS, no distortion).
- `category.parquet` — `well_id, field, reservoir, block, alloc_factor`.
- `events.parquet` — `well_id, date, event, note` (conversions, shut-ins).
- `pressure.parquet` (aquifer case only) — quarterly static reservoir pressure surveys.
- `faults.parquet` (sectored case only) — fault polyline.
- `truth.json` — f_ij, τ, q₀, oil-cut parameters, gates the truth satisfies, expected tournament
  winner with notes, aquifer/sector/conversion details, and the optimizer ground truth
  (24-month max-cumulative-oil reallocation with the same water: optimal rates and gain vs.
  equal split and vs. hold-current).

## Forward model
Atlas "CRMP" tab (Sayarpour et al. 2009) discrete solution
`q_j(n) = q_j(n-1)·e^{-Δt/τ_j} + (1-e^{-Δt/τ_j})·Σ_i f_ij·i_i(n)`, per-injector constraint Σ_j f_ij ≤ 1,
power-law oil cut `f_o = 1/(1+α·CWI^β)` with CWI the cumulative water allocated to the producer (Mbbl).
Aquifer: Fetkovich tank from the Atlas "CRM–Aquifer" tab, explicit Euler. Injection signals are
piecewise-constant re-targets (CV ≈ 0.3) with 1 % meter noise.

## Future work
A 2-D single-phase finite-difference simulator for `streak_5x4` / `aquifer_6x9` (preferred by the
build prompt) is deferred; see DECISIONS.md (M0). It would add a case whose truth is *derived*
(tracer/perturbation) rather than parametric, which is a different kind of test.
