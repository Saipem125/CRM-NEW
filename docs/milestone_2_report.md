# Milestone 2 report — extended models, optimization, workflow logic

Date: 2026-09-03 · Tag: `m2` · Python 3.11.9 · Windows 10

## What was built

| package | content (§) |
|---|---|
| `models/aquifer.py` | CRMPA: Fetkovich tank reduced to one influx ODE, influx as a tank-driven pseudo-injector with allocation closure Σ_j a_j = 1, pore volumes from static surveys when present (§9, Atlas CRM–Aquifer) |
| `models/twophase.py`, `crossflow.py` | Cao–Luo–Lake coupled saturation balance with Corey fractional flow and τ(t); Olenchikov–Posvyanskii productivity-coefficient / crossflow tank network (§9) |
| `models/mpi.py` | closed-rectangle line-source influence matrix, [J], MPI prior scaled to the sector's distance–f_ij trend; new injector, conversion and infill bridges (§11, Atlas MPI) |
| `models/forecast.py`, `rolling.py`, `change_detect.py` | forecast continuation from the last state with P10/P50/P90 fans; 36-month rolling windows blended with the full fit by blind score; CUSUM change alerts in plain language (§10, §13) |
| `optimize/` | objectives (cumulative oil, NPV with price deck, min water for target oil), constraints, postures, SLSQP multi-start solver with ensemble no-loss constraints, scenarios (base, reallocation, water-budget sweep, shut-in, new well, conversion, pattern balancing), ramping, "change this week" action list with revert conditions (§12–§14) |
| `workflow/` | Draft → Reviewed → Approved → Implemented → Evaluated with immutable approved snapshots, separation policy, LOW override, evaluation records at 3/6 months with outcome classes and recalibration rule, the §15 rules table, alerts (§3, §15, §16) |
| `store/` | Parquet snapshots + SQLite catalogue, run registry keyed by (data hash, config hash, code version), audit log, validation record (§18) |

## Test results

```
python -m pytest -q        90 passed   (≈ 22 min; the external suite dominates)
python -m ruff check .     All checks passed
python -m mypy             Success: no issues found in 73 source files
```

Acceptance (prompt, Milestone 2):

| criterion | result |
|---|---|
| optimizer on `streak_5x4` increases oil vs. proportional allocation by the truth amount ± 5 % | 6.98 % vs. truth 6.98 % (relative error 0.02 %); rates at the truth optimum |
| robust posture never selects a plan that loses oil in any ensemble member | asserted on the 11-member noisy ensemble and checked on every case (column "robust no-loss" below) |
| tournament selects the aquifer variant on the aquifer fixtures | M0 `aquifer_6x9`: CRMPA wins, blind R² 0.9998, HIGH; external `aquifer_9x9`: CRMPA fitted with the best blind R² (0.869 vs 0.850) but ranked 2nd on the composite by ≈ 0.01 (parsimony/runtime terms); recorded as an open finding, weights not tuned |

Optimizer per M0 case (balanced posture unless LOW confidence forces robust; 24-month horizon, same water):

| case | winner | badge | posture | gain vs equal % (truth) | gain vs hold % (truth) | ensemble members | robust no-loss | actions | engine s | optimizer s |
|---|---|---|---|---|---|---|---|---|---|---|
| streak_5x4 | CRMP | HIGH | balanced | 6.98 (6.98) | 5.05 (5.05) | 1 | yes | 4 | 27 | 2.7 |
| streak_5x4_noise5 | CRMP | HIGH | balanced | 5.27 (6.98) | 3.65 (5.05) | 11 | yes | 5 | 65 | 25.1 |
| aquifer_6x9 | CRMPA | HIGH | balanced | 16.76 (15.62) | 18.40 (17.11) | 5 | yes | 6 | 119 | 41.6 |
| converted_wells | CRMP | HIGH | balanced | 1.87 (1.87) | 2.11 (2.11) | 1 | yes | 6 | 9 | 0.4 |
| allocated_noisy | CRMP | LOW | robust (forced) | 9.24 (6.85) | 15.68 (11.14) | 10 | yes | 5 | 28 | 13.6 |
| sectored_60 (S1 / S2) | CRMP | HIGH | balanced | 9.59 / 6.87 | 7.40 / 6.78 | 1 | yes | 12 / 12 | 52 | 25 / 29 |

The noisy case's balanced plan is more conservative than the truth optimum (5.3 % vs 7.0 %) because the ensemble spread is priced in (mean − 0.5 σ). The allocated-noisy case over-states the gain (9.2 % vs 6.9 %): its LOW-confidence model is explicitly "screening only" and cannot be approved without an override.

CRMPA on `aquifer_6x9` (with static surveys): influx 5 331 → 3 097 vs truth 5 600 → 3 287; allocations within 0.01 of truth; f_ij within 6 %; c_tV_r within a factor 1.6 (the survey-scale regression is approximate).

## Findings and changes (all in DECISIONS.md)

1. **M0 optimizer truth re-frozen (generator 1.1.0).** The generator counted the last historical month as the first horizon step. Corrected; rates and every other truth field are byte-identical.
2. **A free primary term is not the only aquifer loophole:** the influx × allocation product is what the rates identify. The closure Σ_j a_j = 1 was needed to pin the influx scale (external case went from a 4× over-estimate to within 7 % of truth at the start of history).
3. **SLSQP needed a greedy vertex start** to reach the bound-constrained optimum; the polish step closes the last 0.3 points.
4. **Oil-cut fit needed the pre-history cumulative offset** to be exact; without it the WOR power law was only approximate and the optimum shifted.
5. **Static surveys are collected from any well**, not only producers (the M0 surveys are tagged "FIELD").
6. **Economics on the M0 fixtures is negative at $70/bbl** (99 % water cut); the objective still ranks plans, and the price tornado is monotone.

## Not done / open

- Two-phase and crossflow are exercised only on synthetic unit cases: no fixture is eligible (water cut > 0.5 everywhere; fewer than 4 events per well). The crossflow variant keeps injection allocation as fixed weights (simplification, DECISIONS).
- CRMPA fits take 60–120 s per sector on this machine (outer loop × VarPro inner fits); the external suite now runs ~12 min. Acceptable under the 10-minute-per-field bound, but the outer grid was trimmed to keep it so.
- Rolling windows and CUSUM are implemented and tested, but no fixture contains a real connectivity change, so the alert wording has only been exercised on a synthetic doubling.
- Confidence recalibration (§16 Tier 3) has the rule and the record, not the data.
- SQL connectors, API, jobs and users are Milestone 3.

## Next

Milestone 3 — FastAPI, auth and roles, jobs, OpenAPI docs, state-machine endpoints. Waiting for review.
