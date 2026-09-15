# Second field package — Fadl / ALFA Main block (2026-09-15)

A second package for the same field arrived as three CSVs (`Main_INJ - 2.csv`, `Main_Prod - 2.csv`,
`XY_2.csv`): 27 wells of the "Main" block, monthly rates 2008-06 → 2025-01, surface coordinates with a
current role. The data stay outside git (`Data/FADL Main Block*`); this note records the review, the
runs and what the app had to learn.

## Data review

| check | result |
|---|---|
| structure | no duplicates, no negatives, no unparsed dates, every id in all three files, conversions cleanly separated |
| **rates** | **every value is exactly 2 × (monthly volume ÷ calendar days)** — 1 515 producer and 1 038 injector months matched against the raw monthly volumes, ratio 2.00 at the 5th, 50th and 95th percentile; the same provider's files of 28 Nov carry the correct values |
| operating days | column absent; restored from the raw monthly file (`days_on` mask) |
| coordinates | shifted 34 m (median) to 140 m (ALFA-45) against the earlier ALFA set, on 150–400 m spacing — one of the two sets is wrong |
| roles | XY file types ALFA-25 as injector; it has produced since 2017-08 and is dead since 2021-03 |
| single-month spikes | ALFA-01 and ALFA-39 each produce ≈ 30 000 bbl water in 2024-09 only, surrounded by shut-in (flow-back or test) — loaded as OTHER events |
| gaps | ALFA-20 has no row for 2014-08 inside a shut-in period |
| gas | 8 non-zero values of 2 420, all < 0.13: not provided |
| scope | 22 ALFA wells are outside the block, including ALFA-43 and ALFA-35 of the earlier 4 × 6 group |

The run used the corrected rates (÷ 2), the package's coordinates and `days_on` from the raw file.
Two points matter more than the defects: the block is far better supported than the whole field
(liquid ÷ injection 0.7–1.3 through most of the history against 2.5 for all of ALFA), and the active
set is small and shrinking — 6 producers and 6 injectors on in the last months, 8 producers dead or
idle, history ending 20 months before the run.

## Runs (after the fixes below)

| run | window | wells | winner | blind R² | badge | plan |
|---|---|---|---|---|---|---|
| 2020 onward, active wells, no conversion breaks | 2020-01 → 2025-01, 61 months | 7 I / 13 P | CRMP (aquifer 0.71, CRMT 0.35; CRMIP excluded at 1.9 pts/param) | 0.71 | LOW | +52 % oil vs hold (robust posture) |
| whole history, no conversion breaks | 2008-06 → 2025-01, 200 months | 12 I / 18 P | aquifer (CRMIP 0.34, CRMP 0.30, CRMT 0.18) | 0.58 | LOW | +58 % |
| app defaults (history cut at conversions) | 2017-09 → 2025-01, 89 months | 9 I / 14 P | CRMP by a hair over aquifer (0.71); CRMIP eligible, 0.72 on twice the parameters | 0.70 | LOW | +34 % |

Blind R² 0.71 is the best this field has given (the whole-field ALFA runs were 0.34–0.66 with the
tank model winning). For the first time an allocation model beats the field tank in every window, so
the connectivity picture is readable:

- 2020 onward: ALFA-34 → ALFA-09 (0.77), ALFA-18 → ALFA-08 (0.74), ALFA-41 / ALFA-02 → ALFA-01
  (0.53 / 0.50, a producer now shut in), ALFA-45 → ALFA-29 (0.40), ALFA-12 → ALFA-48 (0.38),
  ALFA-46 → ALFA-33 (0.33). τ 28–150 d for the producers with a response, at the bound for the
  wells that are mostly shut in.
- App defaults (2017-09 onward): ALFA-18 → ALFA-09 (0.84), ALFA-45 → ALFA-39 (0.63), ALFA-41 →
  ALFA-33 (0.59), ALFA-46 → ALFA-08 (0.44), ALFA-02 → ALFA-09 (0.42) — ALFA-18's strongest link moves
  from ALFA-08 to ALFA-09 and ALFA-34's from ALFA-09 to ALFA-39 between two windows that overlap for
  five years, and the robust-posture gain (+34 %) sits on a P50 difference of only 2 %.
- Whole history: the strongest links belong to wells that no longer operate (ALFA-13 → ALFA-33 0.83,
  ALFA-10 / ALFA-02 → ALFA-01 0.8); the live pairs agree with the recent window for ALFA-45 → ALFA-09
  and ALFA-46 → ALFA-08 but not for ALFA-34 and ALFA-41.

Why it is still LOW, and why the plan is screening only:

- No pressure, no events, monthly allocation: the same three gaps as before (see the data request).
- The model under-predicts the last year: the forecast starts ≈ 45 % below the last observed field
  oil rate and every producer scores a negative R² on its own held-out months. The plan "gain" is
  relative to that forecast, not to the field's current rate.
- The two windows recommend opposite moves for ALFA-34 and ALFA-41: the allocation is not stable
  across windows.
- Injection cannot be told apart from the aquifer: the aquifer variant matches CRMP exactly in the
  recent window and wins the longer ones.

What would change it is unchanged: producer flowing pressure, daily injection and well-test rates, and
a deliberate rate change on the 34 / 18 / 41 / 02 injectors while ALFA-09, 08, 27, 29, 33 are watched.

## What broke in the app, and was fixed

1. **Producers closed at the end of history kept flowing in the forecast.** The continuation carried
   every producer's model rate through the horizon, so wells shut in for years supplied 26 % of the
   "hold current" oil in the recent window and 45 % in the whole-history run. A producer with no
   producing day over the last `optimize.idle_lookback_months` (3) steps now has no forecast liquid
   (`optimize.forecast_shut_in_producers: false`) and the recommendation lists them.
2. **Idle injectors were "restarted" by the plan.** The whole-history plan moved 374 bbl/d each to
   ALFA-24, ALFA-13 and ALFA-45, which have been closed for one to five years, as if they were
   set-point changes; the defaults run restarted ALFA-10. Injectors with no injection over the same
   look-back are held at zero (`optimize.allow_restart_idle_injectors: false`), the per-injector cap
   is 2 × the mean of the injectors in use, and the recommendation names them. Reopening a well is a
   scenario decision.
3. The rate doubling and the missing operating-days column are data-preparation findings; the loader
   convention (calendar-day rates + `days_on`) is unchanged and the rates were corrected before loading.

Tested in `tests/test_surveillance.py` (`test_closed_producer_has_no_forecast_and_idle_injector_is_not_restarted`);
the fixture suites have no closed wells at the end of history and are unaffected.
