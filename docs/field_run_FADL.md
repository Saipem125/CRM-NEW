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

## Influence radius (2020 window)

`solver.distance_cutoff_factor` × the median nearest injector–producer distance (303 m on this block),
every producer keeping its nearest injector:

| factor | cutoff | allowed pairs | winner | blind R² | CRMP R² | Σf < 1 injectors |
|---|---|---|---|---|---|---|
| off | – | 91 | CRMP | 0.71 | 0.71 | none |
| 1.5 | 454 m | 24 | aquifer | 0.78 | 0.65 | ALFA-34 0.0, ALFA-12 0.0, ALFA-41 0.1 |
| 2.0 | 605 m | 32 | aquifer | 0.79 | 0.68 | ALFA-34 0.0, ALFA-12 0.2, ALFA-41 0.2 |
| 2.2 | 666 m | 39 | aquifer | 0.74 | 0.71 | ALFA-12 0.2, ALFA-41 0.3, ALFA-46 0.3 |
| 2.5 | 756 m | 43 | aquifer | 0.74 | 0.72 | same |
| 3.0 | 908 m | 60 | aquifer | 0.68 | 0.64 | ALFA-41 0.35, ALFA-02 0.4 |

The radius does improve the blind test, but not by sharpening CRMP: CRMP itself gets no better, and the
gain comes from the aquifer variant, which is free to absorb whatever the masked injectors can no
longer explain. At 454–605 m the model says ALFA-34's water reaches no producer at all (its only
in-radius neighbours are shut in) and drops the ALFA-34 → ALFA-09 link (623 m) that every other
configuration and both CRMP and CRMIP found. The honest reading is that the block's injection is
under-explained within 600 m — either the water travels farther than the spacing suggests or part of
it leaves the block — and the mask converts that into aquifer support. A radius of about 700 m (factor
2.2–2.5) keeps the consistent pairs, removes the > 900 m ones (ALFA-02 → ALFA-01, ALFA-12 → ALFA-48)
and scores 0.74; that is the setting to carry forward, with the Σf shortfall on ALFA-12, ALFA-41 and
ALFA-46 as an open question for the field team (out-of-block or out-of-zone injection).

## Operational transients (2020 window)

The user's hypothesis: shut-ins and restarts are not represented and drag the match. Split of the
CRMP training error by month type confirms part of it — the first month back on stream carries a
median error of 35 % against 11 % for an ordinary month (16 months, 8 % of the fit error). The larger
finding is in the data: the producer "days in month" column equals the calendar days in all 2 640
producing months, so shut-ins shorter than a month are invisible and read as rate drops (the injector
column is a real operating-day count). Two fit-weight settings were added, `solver.restart_transient_months`
and `solver.pre_shutin_months` (default 0):

| after / before | radius | winner | blind R² | CRMP R² | ALFA-09 blind error | ALFA-33 blind error |
|---|---|---|---|---|---|---|
| 0 / 0 | – | CRMP | 0.71 | 0.71 | 28 % | 31 % |
| 1 / 0 | – | aquifer | 0.75 | 0.65 | 23 % | 28 % |
| **1 / 1** | – | **CRMP** | **0.78** | **0.78** | **9 %** | 32 % |
| 2 / 1 | – | CRMP | 0.72 | 0.72 | 27 % | 26 % |
| 1 / 1 | 756 m | aquifer | 0.72 | 0.69 | – | – |

Dropping one month on each side of every shut-in is the setting to carry: CRMP stays the winner with
Σf = 1 for every injector, the blind score rises to 0.78 and ALFA-09's held-out error falls from 28 % to
9 %. The other main producers keep their 25–30 % under-prediction in the held-out year, which contains
no restart or partial month for any of them — that drift is not an operations artefact. Combining the
transient weights with the radius loses the gain (the aquifer term takes over again).

## Simulation start per well (2020 window)

The user's second point: the simulated rate should start when the well starts. Five of the thirteen
producers come on stream inside the 2020 window (ALFA-29 at month 2, ALFA-04 / ALFA-09 at 13–14,
ALFA-08 at 14, ALFA-48 at 31). The per-producer recursion used to run from the window's first step
for every well, so a late starter opened with a fully developed injection response, no primary term,
and a drawn model rate through its pre-production months. Each producer is now simulated from its
first producing month: zero before, state zero at that step, primary decay from the well's initial
potential (the largest rate of its first three producing months — the first month is usually partial).

| start rule | winner | blind R² | ALFA-08 | ALFA-09 | ALFA-27 | ALFA-33 |
|---|---|---|---|---|---|---|
| window start (old), transients 1 / 1 | CRMP | 0.78 | 29 % | 9 % | 41 % | 32 % |
| well start, transients 1 / 1 | CRMP | 0.76 | 10 % | 22 % | 42 % | 27 % |

Field score within noise of the previous rule; ALFA-08's held-out error falls from 29 % to 10 % and
ALFA-09's rises from 9 % to 22 % (its 2021 opening at plateau rate was previously "explained" by
thirteen months of support accumulated before the well existed). The well-start rule is the physical
one and is now the only behaviour; the aquifer variant (0.74) and CRMIP (0.71) sit just behind CRMP.
(The initial potential is the median of the first three producing months: the maximum picked up the
restart flush, e.g. ALFA-09's 950 bbl/d second month against a 750–800 plateau.)

**The pair map is not identifiable on monthly data.** At the same blind score the two start rules
give different allocations: window start puts ALFA-34 → ALFA-09 (0.65) and ALFA-18 → ALFA-08 (0.72)
on top; well start sends ALFA-34 → ALFA-01 (0.71, 1 233 m) and ALFA-18 → ALFA-33 (0.73), drops
ALFA-09's total allocation from 1.4 to 0.4 and gives ALFA-01 (shut in since late 2024) 1.9. The
mechanism is the tied primary term: a late starter now decays from its initial potential with the
same τ as its injection response, and with τ free up to 3 650 d (ALFA-09: 2 134 d, ALFA-01: 1 448 d)
that term substitutes for injection. Capping τ at 730 d does not help — CRMP falls to 0.62 with τ
pinned at the bound for six producers and the same map. Pairs strong under every formulation and in
both CRMP and CRMIP, the only ones to put weight on: ALFA-41 → ALFA-29 / ALFA-39, ALFA-45 → ALFA-29,
ALFA-02 → ALFA-09 / ALFA-08, ALFA-12 → ALFA-29, ALFA-46 → ALFA-01. The ALFA-34 and ALFA-18 links flip
between formulations and need a rate-change test to settle.

**The influence radius resolves the flip.** With the well-start formulation the long-distance links
are the artefact, and a 756 m radius (factor 2.5) removes them: the aquifer variant wins at blind R²
0.76 (CRMP 0.68) with a constant influx (k₁ = k₂ = 0) and a nearest-neighbour map — ALFA-18 → ALFA-08
(0.70, 280 m), ALFA-34 → ALFA-09 (0.63, 623 m), ALFA-02 → ALFA-09 (0.55), ALFA-41 → ALFA-39 (0.65),
ALFA-46 → ALFA-01 (0.60), ALFA-45 → ALFA-29 / ALFA-48 / ALFA-27. That is the window-start map's
short-arrow picture recovered under the physical formulation, at the same score. Σf per producer
ALFA-09 1.27 and ALFA-39 1.06 (support beyond injection), ALFA-01 0.6. **Recommended project settings
for this block:** `solver.restart_transient_months: 1`, `solver.pre_shutin_months: 1`,
`solver.distance_cutoff_factor: 2.5`. The 666 m radius gives the same result.

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
3. **The influence radius was not applied to the fits.** `solver.distance_cutoff_factor` only
   partitioned sectors; the tournament never received the mask. It is now carried on the fit data into
   every CRMP / CRMIP / aquifer fit and the rolling windows (tested).
4. **Wells were simulated before they existed** (see above): per-producer start step in the solver,
   reproduced exactly by the forecast continuation; wells with no producing training month return
   f = 0 instead of an arbitrary fit.
5. The rate doubling and the missing operating-days column are data-preparation findings; the loader
   convention (calendar-day rates + `days_on`) is unchanged and the rates were corrected before loading.

Tested in `tests/test_surveillance.py` (`test_closed_producer_has_no_forecast_and_idle_injector_is_not_restarted`);
the fixture suites have no closed wells at the end of history and are unaffected.
