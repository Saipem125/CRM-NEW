# First real field data — ALFA (2026-09-07)

The build prompt asks to run the loader and engine on real field data as soon as it arrives and to
report what broke. The data itself is confidential and lives outside git (`Data/`, ignored); this
note records the findings.

## The package

48 wells, monthly volumes 2008-06 → 2026-07 (production: oil, water, gas, days; injection: water,
days), surface coordinates for every well, six wells converted between producer and injector. The
provider had already built pywaterflood-style rate matrices with rate = volume ÷ reported days.

## What the loader did

- Suggested the whole mapping from the column names; the only correction was the coordinate unit
  (metres, not the field-unit default of feet).
- Derived 26 producers, 16 injectors, 6 mixed wells and the six conversion months from the rates
  alone — identical to the provider's README.
- No unmatched ids, no simultaneous production-and-injection months, 94 producer outliers replaced.

## What the data needed

- **Calendar-day rates.** The grid treats rate columns as calendar-day rates and `days_on` as an
  on/off mask. 46 injection months carry a full month's volume against fewer than 5 reported days
  and 277 carry a volume with no day count; volume ÷ reported days turns those into 90 000 bbl/d
  spikes (present in the provider's matrices). Volume ÷ calendar days is the material-balance rate
  and removes them. Documented in USER_GUIDE.
- Two negative water volumes clipped to zero.

## What the engine said (four runs)

| run | history | wells | winner | blind R² | badge |
|---|---|---|---|---|---|
| defaults (history cut at every conversion) | 2023-06 → 2026-07, 38 months | 11 I / 15 P | CRMT | 0.66 | LOW |
| whole history, no conversion breaks | 2008-06 → 2026-07, 218 months | 22 I / 32 P | CRMT | −0.16 | LOW |
| 2020 onward, wells active ≥ 12 months | 79 months | 13 I / 26 P | CRMT | 0.37 (aquifer 0.66, CRMP −0.13) | LOW |
| WI cluster 41–46 + producers within 1.5 km | 79 months | 13 I / 25 P | CRMT | 0.49 | LOW |

Every run is LOW and the field tank beats every allocation model in the blind test, so no
connectivity map or recommendation is defensible. The diagnostics explain why:

- Withdrawal is ≈ 2.5 × injection (apparent Σf 2.5): the drive is aquifer / depletion, injection is
  a minor and intermittent input (11 of 22 injectors active in the last three years, on/off rather
  than rate changes, field total 6 000 → 500 bbl/d).
- No pressure data → constant-BHP mode; lift and choke changes are read as reservoir response.
- τ pins at the bounds (28 d or ≈ 6 years): monthly sampling cannot resolve the response.
- Only 1.1–3.8 data points per CRMP parameter; CRMIP never eligible.

What would change the outcome: producer BHP/WHP and static surveys, calendar-day injection rates
from the surveillance system, a period of deliberate injection rate changes (or an injection test
on the 41–46 WI cluster).

## What broke in the app, and was fixed

1. **Global window break at single-well conversions** threw away 15 years of history; conversions
   already split a well into separate producer/injector entities, so the break is now a per-project
   choice (`events.window_break_types`, default unchanged for the fixture suite).
2. **Oil-cut fit overflow** on degenerate wells produced NaN forecasts, which crashed the report's
   forecast fan and the DOCX and gave a recommendation with no numbers. The fit is clamped and the
   oil-cut evaluation is finite; figure primitives ignore non-finite coordinates.
3. **Change detector** raised hundreds of "strengthened" alerts on pairs that are unconnected in
   most windows; alerts now need a pair that is connected in ≥ 50 % of windows and a consistent
   change of at least 25 %.
4. **Runtime.** The whole-history run took 87 min: the aquifer variant (15 min), plus the in-run
   rolling re-fit over 30 windows on a 54-well sector. `rolling.max_wells_in_run` (80) is a per-
   sector well count; for long histories the surveillance job is the right place. Lowered to 40.
5. The tornado guard for a plan without values.
