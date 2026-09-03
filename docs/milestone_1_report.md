# Milestone 1 report — engine core (no UI)

Date: 2026-09-03 · Tag: `m1` · Python 3.11.9, pywaterflood 0.3.4 · Windows 10

## What was built

| module | content (§) |
|---|---|
| `ingest/schema.py`, `mapping.py`, `connectors.py` | canonical datasets, Loader-reference hints and auto-mapping, CSV/Parquet/Excel readers, unmatched-ID and date reporting (§5) |
| `ingest/units.py` | unit tags → internal SI, PVT constants/table, reservoir volumes q_o·B_o + q_w·B_w (+ free gas), GOR > R_s flag, datum shift, time base (§6) |
| `ingest/welltype.py` | P/I/B per step, PROD/INJ/MIXED with conversion dates, role split into `<well>@P` / `<well>@I` entities, commingled split, ID normalisation with alias table (§5) |
| `prep/grid.py`, `clean.py`, `events.py`, `sectors.py`, `gates.py`, `pressure_prep.py`, `split.py` | monthly grid, Hampel on producers (raw retained), event windows, distance-graph + block sectors, O_d / points-per-parameter / CV / τ⁄Δt gates, per-source pressure handling, 20 % blind split (§7, §8) |
| `models/base.py`, `solver.py`, `crmt.py`, `crmp.py`, `crmip.py` | `CRMModel` interface; in-house solver with analytic gradients, variable projection, multi-start, joint Σ_j f_ij ≤ 1, BHP term, distance mask; pywaterflood engine (§9) |
| `models/fractional_flow.py`, `verify.py`, `uq.py`, `tournament.py` | power-law WOR–CWI and Koval; blind R² / MAPE / AICc / autocorrelation / plausibility; multi-start spread; eligibility rules of the Tournament Selector, composite score, ensemble, confidence badge (§9, §13) |
| `messaging/condition_map.yaml`, `conditions.py` | the 12 rows of §17 plus 24 loader/QC/model conditions, each with one sentence and one action; `Condition` objects everywhere (§17) |
| `engine.py` | `run_engine`: L1 → L2 → L3 driver with data / config hashes and seeds (§1, §18) |
| `config/thresholds.yaml` | every gate, weight, solver and badge threshold (§5 engineering rules) |

## Test results

```
python -m pytest -q        68 passed   (≈ 9 min; the two engine suites dominate)
python -m ruff check .     All checks passed
python -m mypy             Success: no issues found in 46 source files
```

Acceptance (prompt, Milestone 1):

| criterion | result |
|---|---|
| tournament selects the expected variant on the five fixtures | CRMP on streak, noisy streak, converted, allocated, sectored (both sectors); aquifer: signature recognised, CRM-Aquifer eligible and recommended, fitted in M2 (DECISIONS) |
| CRMP blind R² ≥ 0.9 noise-free / ≥ 0.75 noisy | 1.000 / 0.974 |
| in-house solver matches pywaterflood within 1 % | f_ij, τ and blind R² within 1 % on `streak_5x4` (free-primary form); blind R² not worse by more than 0.01 on the noisy case |
| every emitted Condition has a map entry | enforced by `tests/test_condition_map.py` (source scan + enum + YAML) |
| property-based unit tests | hypothesis on conversions, reservoir volumes, datum shift |
| `converted_wells` well types | MIXED with conversion at 2014-01 (M0 suite) and 2020-01 (external); two windows; role entities |

Engine results per case (latest window; blind metrics on producing days):

| suite | case | winner | blind R² | blind MAPE % | spread | badge | runtime s | recovery / note |
|---|---|---|---|---|---|---|---|---|
| M0 | streak_5x4 | CRMP | 1.000 | 0.0 | 0.000 | HIGH | 20 | f_ij and τ exact |
| M0 | streak_5x4_noise5 | CRMP | 0.974 | 3.7 | 0.001 | HIGH | 26 | f_ij ≤ 31 % on pairs ≥ 0.05 (worst is the 0.075 pair), τ ≤ 10 % |
| M0 | aquifer_6x9 | CRMIP* | 0.861 | 5.5 | 0.000 | LOW | 33 | SUM_F_HIGH raised (ratio 1.43); CRMPA pending M2; τ at bound |
| M0 | converted_wells | CRMP | 1.000 | 0.0 | 0.000 | HIGH | 12 | two windows, phase-B entities `P-5@I`, `P-6@I` |
| M0 | allocated_noisy | CRMP | 0.651 | 5.7 | 0.000 | LOW | 39 | days_on mask honoured; 8 % allocation noise + shut-ins |
| M0 | sectored_60 (2 sectors) | CRMP | 1.000 | 0.0 | 0.000 | HIGH | 65 | two sectors from the block column; O_d 9.2 per sector |
| EXT | streak_5x4_clean | CRMP | 1.000 | 0.02 | 0.000 | HIGH | 31 | f_ij ≤ 5 %, τ ≤ 1 %, BHP term active |
| EXT | streak_5x4_noisy | CRMP | 0.938 | 4.8 | 0.000 | HIGH | 34 | |
| EXT | allocated_noisy | CRMP | 0.876 | 5.6 | 0.064 | HIGH | 22 | Excel and CSV paths identical |
| EXT | aquifer_9x9 | CRMP* | 0.850 | 4.8 | 0.020 | MEDIUM | 60 | flank wells P-1, P-2 named; injector rows up to 1.79 → SUM_F_HIGH |
| EXT | converted_wells | CRMP | 0.885 | 3.4 | 0.119 | HIGH | 11 | phase-B f_ij not identifiable to 15 % (see below) |
| EXT | sectored_60 (2 sectors) | CRMP | 0.96 | 2.6 | 0.000 | MEDIUM | 32 | O_d = 6.0 per sector fails "> 6" by definition |

\* best *available* model; the aquifer variant is recognised and recommended.

Figure: `docs/screens/m1_external_streak_fit.svg` — external `streak_5x4_clean`, producer liquid truth vs. engine fit with the J·dp_wf/dt term.

## What the external fixture set broke, and what changed (all in DECISIONS.md)

1. **Injection smoothing corrupted the model input.** Hampel/MAD on injection replaced short shut-ins and re-targets. Injection is now never smoothed; producer cleaning replaces isolated spikes only.
2. **Multi-start gradient descent alone found local minima** (f_ij errors up to 72 % on the external streak with the residual 200× the truth's). A variable-projection stage (τ search with exact bounded least squares inside) now precedes the gradient polish. Recovery is within 5 %.
3. **A free primary-decay term hid the aquifer.** pywaterflood-style (g_p, τ_p) absorbed the declining influx; the Atlas form q(t₀)e^{−t/τ} is now the default and the aquifer signature appears (flank sums 1.34 / 1.25 / 1.04, matching the fixture's own verifier's 1.35 / 1.26 / 1.09).
4. **The §9 influx signature is per injector.** Gate = field ratio ΣQ/ΣI > 1.15 or any unconstrained injector row Σ_j f_ij > 1.15 from a quick distance-masked CRMP; a streak does not trigger it, both aquifer fixtures do.
5. **Their `tau_days` are time steps**, not days, and their BHP term differs from Sayarpour's by a rescaling of J. Both are handled in the tests, not in the engine.
6. **Their `converted_wells` phase-B tolerance (15 %) is not reachable**: 48 steps, 4 injectors, 3 % noise; the fit reaches the noise floor with non-unique pairs. Their verifier skips the case. Reported, not chased.
7. **Free gas was flagged on every step** whenever a gas column existed with no gas PVT; detection now requires R_s or B_g.

## Not done / open

- CRM-Aquifer, two-phase, crossflow, MPI and ML variants: eligibility and exclusions are implemented; fits are Milestone 2 (`VARIANT_NOT_AVAILABLE` is emitted so the Details panel can say so).
- Confidence thresholds are the §9 defaults; calibration against outcomes is §16 Tier 2/3 (later milestones). Note that the external `converted_wells` earns HIGH by those rules although pair-level parameters are non-unique — the spread test (12 %) sits just under the 15 % limit.
- Runtime: the two engine test modules take about 3.5 min each on this machine; the 60-well cases run in about a minute per field (well under the 10-minute bound).
- SQL connectors and the API are Milestone 3.

## Next

Milestone 2 — aquifer (CRMPA), two-phase, crossflow, MPI, rolling windows and change detection, the optimizer with postures and the change list, workflow state machine and store. Waiting for review.
