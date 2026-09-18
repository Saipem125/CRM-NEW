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

## Shut-in redistribution (user's proposal)

"If the simulated rate switches to zero when the well is at zero, the offset injector's water is
distributed over the other wells." Implemented as a model change (`solver.shut_in_redistribution`,
see DECISIONS): closed producers at zero with frozen state, their share of each injector to that
injector's open producers, capped at 3 × a well's normal share, allocation re-fitted with the
redistributed injection. Same window and settings as above (well start, transients 1 / 1, 756 m):

| model | train R² | blind R² | ALFA-08 | ALFA-09 | ALFA-27 | ALFA-33 | ALFA-28 | ALFA-29 |
|---|---|---|---|---|---|---|---|---|
| before (aquifer) | 0.93 | 0.76 | 25 % | 23 % | 18 % | 28 % | 51 % | 32 % |
| uncapped, CRMP | 0.90 | 0.79 | 2 % | 7 % | 25 % | 30 % | 800 % | 19 % |
| **capped × 3, CRMP** | 0.91 | **0.84** | 3 % | 6 % | 25 % | 30 % | 67 % | 22 % |
| capped, CRMIP | 0.92 | 0.81 | 7 % | 9 % | 23 % | 33 % | 41 % | 28 % |
| capped, aquifer | 0.93 | 0.55 | 11 % | 18 % | 25 % | 3 % | 96 % | 111 % |

The 2024 rise of ALFA-08 and ALFA-09 that every earlier configuration missed is the water freed by
ALFA-01, ALFA-04 and ALFA-33 closing; with it accounted for, CRMP reaches 0.84 — one point below the
MEDIUM threshold — and CRMIP 0.81 with the same map (10 of CRMP's 12 strong pairs, mean difference
0.035): ALFA-46 → ALFA-01, ALFA-34 → ALFA-09, ALFA-18 → ALFA-08, ALFA-41 / ALFA-45 → ALFA-29,
ALFA-02 → ALFA-09 / ALFA-39. CRMIP's one disagreement is ALFA-12 → ALFA-33 (0.93 against 0.25), the
injector whose water CRMP cannot place (Σf 0.5). ALFA-33 itself is still 23–30 % under in its 2024
comeback, ALFA-28 and ALFA-48 remain the erratic small wells. The aquifer variant no longer helps:
once the shut-ins are handled the constant-influx term has nothing left to explain.

## Optimisation plans on the final configuration (oil objective, 24 months, robust posture)

Total water held at 2 243 bbl/d, per-injector cap 2 × the mean of the injectors in use, idle
injectors held at zero unless allowed to restart.

| injector | today | CRMP | CRMIP | CRMP, ALFA-45 allowed | CRMIP, ALFA-45 allowed |
|---|---|---|---|---|---|
| ALFA-02 | 545 | 748 | 748 | 641 | 641 |
| ALFA-41 | 565 | 748 | 748 | 641 | 512 |
| ALFA-34 | 230 | 748 | 0 | 320 | 0 |
| ALFA-46 | 436 | 0 | 748 | 0 | 450 |
| ALFA-18 | 228 | 0 | 0 | 0 | 0 |
| ALFA-12 | 239 | 0 | 0 | 0 | 0 |
| ALFA-45 | 0 | 0 | 0 | 641 | 641 |
| plan oil vs hold-current, P50 | | +43 000 bbl | +22 000 bbl | +71 000 bbl | +67 000 bbl |
| field oil at month 24, P50 | | 331 bbl/d | 319 bbl/d | 354 bbl/d | 358 bbl/d |

Both models agree on raising ALFA-02 and ALFA-41 to the cap and shutting ALFA-18 (feeds low-oil-cut
ALFA-08) and ALFA-12 (water unplaced); they disagree on ALFA-34 versus ALFA-46 — the same pair the
maps disagree on — and that is the injector to step first, watching ALFA-09. Nearly all the gain is
ALFA-09 (+35 000 / +21 000 bbl). ALFA-45, shut in since 2024-09, has the highest marginal value in
both models (feeds ALFA-29 / ALFA-27 / ALFA-48 at short range); allowing it to restart is worth more
than the reallocation and makes the two plans converge (354 vs 358 bbl/d).

**These plans were built on a wrong oil cut** (found while quantifying the ALFA-45 restart): the
whole-window WOR power law gave ALFA-27 — the largest oil producer, observed oil cut 0.43 — a forecast
oil cut of 0.09, ALFA-29 (observed 0.02) 0.13 and ALFA-33 (0.05) 0.00, which is why the forecast
opened 35 % below the last month and why the gain was booked to ALFA-29. The forecast oil cut is now
anchored to the last three producing months (see DECISIONS). With the anchor:

| scenario (24 months, oil cut anchored) | water bbl/d | CRMP oil m1 → m24 | CRMP cum | CRMIP oil m1 → m24 | CRMIP cum |
|---|---|---|---|---|---|
| hold current | 2 243 | 395 → 345 | 266 600 | 333 → 306 | 234 100 |
| hold + ALFA-45 at 641 (extra water) | 2 884 | 434 → 479 | 362 000 (+95 500) | 437 → 424 | 329 800 (+95 700) |
| optimised plan, same water | 2 243 | 428 → 416 | 325 400 (+58 800) | 343 → 343 | 257 500 (+23 400) |
| optimised plan with ALFA-45, same water | 2 243 | 443 → 456 | 359 300 (+92 700) | 440 → 387 | 318 100 (+84 000) |

Restarting ALFA-45 on top of current rates is worth ≈ 95 000 bbl over two years in both models, 0.20
bbl of oil per barrel injected, and the gain lands on ALFA-27 (+76 000 / +84 000 bbl), the well with the
oil cut to pay for it — not on ALFA-29 as the un-anchored forecast said. Restarting is an operations
decision (`optimize.allow_restart_idle_injectors`); ALFA-45 ran at a median 513 bbl/d over 54 of the
window's 61 months and was closed in 2024-09.

**WOR curve on the last 24 months (user's request).** The anchor fixed the level but not the trend:
the history oil model still ran at 41 bbl/d on ALFA-27 against 163 observed. Fitting the curve on each
producer's last 24 producing months (`optimize.oil_cut_fit_months`) — and fixing a slope-clip defect
it exposed, see DECISIONS — gives the history oil match below (median error over the last 12 months)
and makes the two models' plans converge:

| well | observed oil, last 12 m | whole-window fit | 24-month fit | error before → after |
|---|---|---|---|---|
| ALFA-27 | 163 bbl/d | 41 | 105 | 75 % → 37 % |
| ALFA-09 | 108 | 135 | 104 | 24 % → 24 % |
| ALFA-08 | 48 | 17 | 34 | 71 % → 41 % |
| ALFA-33 | 43 | 0 | 33 | 100 % → 23 % |
| ALFA-29 | 15 | 70 | 21 | 472 % → 43 % |
| field | 407 | 285 | 323 | |

Final plans (oil objective, 24 months, robust, anchored oil cut, 24-month WOR fit): **both models**
raise ALFA-18 and ALFA-41 to the cap and ALFA-02 to 666–748, shut ALFA-34 and ALFA-12, and take
ALFA-46 to 0–81 bbl/d; plan oil +82 000 bbl (CRMP) / +50 000 bbl (CRMIP) over hold-current. With
ALFA-45 allowed, both models give the same plan — ALFA-45 641, ALFA-18 641, ALFA-41 641, ALFA-02 320,
the rest off — worth +132 000 / +114 000 bbl at the same water; ALFA-45 on top of current rates with
extra water is +110 000 / +103 000 bbl, almost all on ALFA-27.

## Pywaterflood engine on the same block (like-for-like baseline)

The app's `engine="pywaterflood"` path (library CRMP / CRMIP, training window, constant BHP, no
shut-in handling, "up-to-one" gains per pair) was run on the same 2020–2025 window and split as the
enhanced configuration (`scratchpad/run_pywaterflood_baseline.py`, results in
`results_pywaterflood_x2/`):

| engine / model | blind R² | train R² | Σf per injector |
|---|---|---|---|
| Pywaterflood CRMP | 0.47 | 0.85 | up to 4.3 (ALFA-18) |
| Pywaterflood CRMIP | 0.62 | 0.86 | up to 4.3 |
| enhanced CRMP | 0.84 | 0.91 | ≤ 1 |
| enhanced CRMIP | 0.83 | 0.93 | ≤ 1 |

The library's per-pair "up-to-one" constraint lets an injector's water be allocated several times
over, which is what produces its every-injector-to-every-producer map; five of the enhanced map's
twelve strong pairs survive in it. This is the comparison the MOC deck now shows instead of the old
18-run study. (The direct-CSV loader in that script feeds rates without unit conversion, so its
figure axes are 6.29 × the display units; R², fractions and maps are unaffected.)

## Out-of-sample validation against Feb-2025 → Jun-2026 (2026-09-18)

The field team supplied `New CRM/Recent prod-inj data` (monthly rates Jan-2025 → Jul-2026, 13 injector and 19
producer names). Jan-2025 matches the corrected inputs exactly, so the file is at the true scale in the same units;
Jul-2026 is a partial month and was dropped, leaving 17 months. The six new injector names and four new producer
names carry zeros throughout (not yet on stream); ALFA-45 shows only trace injection (14–37 bbl/d in three months),
so the recommended restart was not made. Injection was otherwise held at the Jan-2025 allocation (mean total
2 137 bbl/d vs 2 243) until May-2026, when ALFA-41 was cut to ≈ 290 and ALFA-02 raised to 600–655 bbl/d. The plan was
not implemented, so only the base model can be validated, not the plan value.

Method (`validate_oos.py` in the scratchpad, outputs in `Data/FADL Main Block/validation_oos/`): the models fitted
through Jan-2025 (`recent_all`, ensemble medians, oil cut anchored as in the report) are driven by the **actual**
injection of the 17 months and compared with the observed producer rates on open well-months. Pooled R² is the
app's blind-test statistic; "field R²" is on monthly field totals. The pywaterflood engine was refitted on the same
inputs (plain CRMP/CRMIP, 12 s) and driven the same way.

| model (fitted to Jan-2025) | pooled R² liquid | pooled R² oil | field R² liquid | field oil bbl/d obs / pred | cum. oil 17 m obs / pred |
|---|---|---|---|---|---|
| enhanced CRMP, as forecast (ALFA-29 open) | 0.60 | 0.52 | −0.24 | 468 / 330 | 242 k / 171 k (−29 %) |
| enhanced CRMP + known ALFA-29 shut-in | **0.64** | 0.43 | **0.61** | 468 / 421 | 242 k / 217 k (−10 %) |
| enhanced CRMIP | 0.46 | 0.26 | −1.5 | 468 / 273 | 242 k / 141 k (−42 %) |
| aquifer CRMPA / CRMT | −1.0 / −0.6 | | | 97 / 177 | 50 k / 92 k |
| pywaterflood CRMP / CRMIP | −0.28 / −0.24 | −0.08 / −0.11 | −9.9 | 209 / 209 | 108 k (−55 %) |
| report base case (hold-current, CRMP) | | | | 347 | 179 k (−26 %) |

Per well, enhanced CRMP with the known shut-in (liquid bias over open months): ALFA-27 0 %, ALFA-08 −11 %,
ALFA-28 −12 %, ALFA-33 −29 %, ALFA-09 +55 %. Library CRMP: −38 % to −72 % on every well.

Findings:

- **The liquid response is validated; the oil cut is not.** With the ALFA-29 shut-in applied (an operation the
  forecast could not know, but the fitted redistribution rule handles it), field liquid tracks within 5–10 % from
  Jun-2025 to Feb-2026 and drifts to −12 % by mid-2026 as observed liquid rises at constant injection. The field
  oil cut rose from 0.17 to 0.25 from Oct-2025 (ALFA-08 0.2 → 0.45 at unchanged liquid, ALFA-33 0.04 → 0.12,
  ALFA-28 0.50 → 0.55), which no rate-driven WOR curve can anticipate; the anchored oil cut stays at 0.19. Oil is
  under-predicted by ≈ 10 % to Sep-2025 and by 25–30 % after. The rise looks like a completion or allocation
  change on ALFA-08 — the events file for 2025 is needed.
- **Pair-level allocation is confirmed as the weak point** (identifiability section above). The model keeps
  ALFA-09 at ≈ 950 bbl/d after its Mar–Apr-2025 shut-in while it came back at 750–800, and gives ALFA-33 too
  little; ALFA-27 is exact. ALFA-09 is probably lift-limited after the restart.
- **The P10–P90 band is too narrow.** Observed field oil fell inside it in 0 % of months (6 % for CRMIP). The
  ensemble spreads only the connectivity fit; oil-cut uncertainty and operational uncertainty are not in it, so the
  report's band overstates confidence. This is a method item: add WOR-curve and shut-in scenarios to the ensemble.
- **The ranking holds out of sample.** Enhanced CRMP > CRMIP > library on every statistic; the library's 0.47 blind
  R² in the study translates to a 55 % under-prediction of both liquid and oil over the following 17 months, and
  the enhanced CRMP's 0.84 to −10 % on cumulative oil once the shut-in is known.
- **The recommended plan was not run**, so its value (+82 000 bbl / 24 months) remains a model statement. The
  May–Jun-2026 change (ALFA-41 down, ALFA-02 up) is half of the plan's direction; ALFA-27/33 liquid did not fall
  when ALFA-41 was cut, consistent with the map's ALFA-41 support going to ALFA-29 (closed) rather than to them.

Next: extend the window to Jun-2026 and refit (17 more months, one more shut-in/restart cycle on ALFA-09, the
ALFA-41/02 swap as an injection signal), and obtain the 2025 events file before interpreting the oil-cut rise.

## Feature ablation on the final code (2026-09-19)

The build-up chart in the deck followed the order the features were developed, and its middle bars mixed models
(0.74 for the well start was the aquifer variant, CRMP scored 0.76; at the 756 m radius the aquifer variant won at
0.76 while CRMP fell to 0.68). To replace it, CRMP was re-fitted for every on/off combination of the three
switchable features (`ablation_fadl.py` in the scratchpad, `Data/FADL Main Block/ablation/ablation.json`; the
per-well start is permanent in the code and is in every run; same window, data, split and seed).

| transient months 1/1 | 756 m radius | shut-in redistribution | blind R² | train R² |
|---|---|---|---|---|
| – | – | – | 0.72 | 0.94 |
| on | – | – | 0.75 | 0.92 |
| – | on | – | 0.70 | 0.92 |
| – | – | on | 0.84 | 0.93 |
| on | on | – | 0.72 | 0.92 |
| on | – | on | 0.84 | 0.93 |
| – | on | on | 0.77 | 0.92 |
| on | on | on | 0.84 | 0.91 |

- The shut-in redistribution carries the score: +0.09 to +0.12 wherever it is switched on, and 0.84 on its own.
- The radius never raises the score (−0.03 to −0.07, and −0.003 in the full configuration). It is kept for the map:
  without it the fit is as good and the allocation goes long-distance (identifiability section).
- The transient months are worth +0.03 without redistribution and matter again once the radius is on
  (0.77 → 0.84); with redistribution alone they change nothing.
- Seeds 1–4 of the full configuration: 0.838, 0.838, 0.839, 0.844 — differences under 0.01 are noise.

The deck (draft 5, slide 8) now shows the cumulative re-fits in the order baseline 0.72 → + transient months 0.75
→ + shut-in redistribution 0.84 → + 756 m radius 0.84, each bar an actual run, with the caption that the radius is
kept for identifiability and not for the score.

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
