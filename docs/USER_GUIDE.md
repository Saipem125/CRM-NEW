# User guide — the engineer path (2 pages)

Waterflood Optimizer turns monthly production and injection data into an approved, ramped injection
plan. You never choose a model: the app tests the CRM family, runs a blind test, picks the method that
forecasts best and tells you how much to trust it with one badge — **HIGH · MEDIUM · LOW**.

## 1. Sign in and pick a project

Your admin creates your account (e-mail or account name) and assigns roles. The role selector at the
top right lets you act in any role you hold (engineer, reviewer, approver, operations, viewer);
every action is logged with your name and the role you acted in. Pick a project or create one
(name, asset, unit system: field or metric).

## 2. Load — connect and confirm the mapping

**Loader** screen, left to right:

1. **Source** — a folder of CSV / Excel / Parquet files or a database (SQLite, PostgreSQL,
   SQL Server, Oracle). Passwords go to the server's secret store; "Test" checks the connection.
2. **Mapping** — tables and columns are suggested automatically for rates, pressure, coordinates,
   category (field / reservoir / block) and events. Required fields are highlighted until mapped;
   units are read from the column names when possible and can be corrected here.
3. **Wells** — filter by field → reservoir → block, tick the wells you want. Well type
   (producer / injector / mixed) is derived from the rates, and conversion dates are shown on a
   timeline. The XY and pressure coverage columns tell you what the model will have to work with.
4. **Save project config** — the mapping and well list are stored with the project.

**Rates from monthly volumes.** Give the loader calendar-day rates (monthly volume ÷ days in the
month) and the reported operating days in a `days_on` column. Do not divide by operating days: a
month booked against one or two days becomes an enormous rate, and the model reads it as reservoir
behaviour. `days_on` is used only to tell shut-in months from producing ones.

Data are snapshotted on every load and referenced by content hash, so a result can always be traced
back to exactly the numbers it was built from.

## 3. Run — objective, posture, one button

- **Objective**: maximum oil, maximum NPV (enter price, water and injection cost, discount rate)
  or minimum water for a target oil volume.
- **Risk posture**: aggressive (mean), balanced (mean − ½σ), robust (P10, and the plan may never lose
  oil in any ensemble member).
- **Horizon** in months.

Press **Run**. The pipeline schematic lights up layer by layer ("Loading data", "Cleaning data",
"Testing models", "Optimizing", "Preparing results"). A 20-well field takes about a minute; a
300-well field with ten sectors a few minutes.

## 4. Result — what to change this week

- The **confidence badge** and its reasons. LOW means screening only: nobody can approve the plan
  without a stated override reason.
- The **well map** with connectivity arrows (width = strength of the injector→producer link, opacity =
  how sure the model is). Click a well to highlight its connections; toggle τ colouring, layers, export SVG.
- The **forecast fan** (P10–P90 band, P50 line, blind-test window shaded) and the gain over
  hold-current and over an equal split.
- The **change list**: for every injector, from → to, the step allowed this week, the target date,
  a setting hint, the expected oil gain and the condition under which to revert.
- The **data-quality light**. Amber or red: open Details before you trust the numbers.
- **Download**: PDF or DOCX report (all figures, plain-language conditions, reproducibility block),
  XLSX / CSV tables, and the fitted model as JSON. Every file carries the run id.

**Details** (right drawer) shows what happened behind the badge: gates passed or failed, which
methods were excluded and why, the leaderboard, the history match per well with residuals, the Δt/τ
sampling check, pressure handling and change alerts. Each technical condition is one sentence plus
one suggested action.

## 5. Workflow — from draft to evaluated

Press **Create recommendation (Draft)** on the Result screen. On the **Workflow** screen the plan
moves DRAFT → REVIEWED (reviewer) → APPROVED (approver; the person who ran it cannot normally
approve it) → IMPLEMENTED (operations enters the rates actually set) → EVALUATED (realised oil
at 3 and 6 months vs the forecast band). Approval freezes data hash, config, model version and
rates; later edits start a new draft. After approval an approver can **write the targets to the
surveillance system** (a connection your admin has flagged for writeback) — every row references
the recommendation, run and data snapshot.

## 6. Reading the badge honestly

| badge | meaning | what you should do |
|---|---|---|
| HIGH | blind test passed comfortably, parameters stable, physics plausible | implement with the weekly ramp; watch the revert conditions |
| MEDIUM | some gates marginal (short history, low injection variation, coarse sampling) | implement cautiously, prefer the balanced or robust posture |
| LOW | screening only | do not implement; add history, pressure data or an injection test first |

If the forecast error after implementation falls outside the P10–P90 band, the surveillance rules
re-fit the model or flag a data problem; a new draft is opened only when the parameters really shifted.
