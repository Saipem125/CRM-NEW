"""Pressure preparation per source — architecture §8 ("Pressure handling by source").

| source          | handling                                                                    |
|-----------------|-----------------------------------------------------------------------------|
| downhole gauge  | flowing pressure, averaged to the rate grid, datum-shifted if depths known   |
| ESP intake (PIP)| trips / gas-lock spikes removed (Hampel), shifted to mid-perf with a        |
|                 | water-cut-dependent mixture gradient, averaged to the grid                  |
| WHP only        | calculated BHP not available here → constant-BHP mode unless WHP varies     |
|                 | strongly (then WHP is used as a proxy, flagged)                              |
| static surveys  | not p_wf(t): kept aside to validate CRMPA / bound τ (M2); constant-BHP mode |
| none            | constant-BHP formulation (J·dp_wf/dt term dropped)                          |

Output: p_wf on the grid for producers that have it (NaN → forward-filled, then mean), or
None when the constant-BHP formulation applies to every producer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt
import polars as pl

from waterflood_app.config import Config
from waterflood_app.ingest.units import mixture_gradient
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.prep.clean import hampel
from waterflood_app.prep.grid import Grid

FArray = npt.NDArray[np.float64]

_GAUGE = {"GAUGE", "DHG", "PDG", "DOWNHOLE", "PERMANENT"}
_ESP = {"ESP", "PIP", "INTAKE", "PUMP"}
_WHP = {"WHP", "THP", "WELLHEAD"}
_STATIC = {"STATIC", "SBHP", "SURVEY", "SHUT-IN", "SHUTIN", "PBU"}


def classify_source(src: str | None, has_bhp: bool, has_whp: bool) -> str:
    s = (src or "").strip().upper()
    if any(k in s for k in _STATIC):
        return "static"
    if any(k in s for k in _ESP):
        return "esp"
    if any(k in s for k in _GAUGE):
        return "gauge"
    if any(k in s for k in _WHP) or (has_whp and not has_bhp):
        return "whp"
    return "gauge" if has_bhp else "none"


@dataclass
class PressurePrep:
    bhp: FArray | None  # (M, Np) on the grid, or None → constant BHP everywhere
    source_per_producer: dict[str, str]
    static_surveys: pl.DataFrame | None
    field_source: str  # dominant source for the profile

    def apply(self, grid: Grid) -> Grid:
        return replace(grid, bhp=self.bhp)


def _to_grid(frame: pl.DataFrame, col: str, grid: Grid, producers_wells: list[str]) -> FArray:
    """Monthly mean of ``col`` per well on the grid (NaN where absent)."""
    out = np.full((grid.n_steps, len(producers_wells)), np.nan)
    idx = {d: k for k, d in enumerate(grid.dates)}
    widx = {w: k for k, w in enumerate(producers_wells)}
    monthly = (
        frame.filter(pl.col(col).is_not_null())
        .with_columns(pl.col("date").dt.truncate("1mo").alias("_m"))
        .group_by(["well", "_m"])
        .agg(pl.col(col).mean().alias(col))
    )
    for w, d, v in monthly.select(["well", "_m", col]).iter_rows():
        if w in widx and d in idx:
            out[idx[d], widx[w]] = float(v)
    return out


def _fill(x: FArray) -> FArray:
    """Forward/backward fill NaNs along time; all-NaN columns stay NaN."""
    y = x.copy()
    for j in range(y.shape[1]):
        col = y[:, j]
        if np.all(np.isnan(col)):
            continue
        last = np.nan
        for t in range(len(col)):
            if np.isnan(col[t]):
                col[t] = last
            else:
                last = col[t]
        first = col[~np.isnan(col)][0]
        col[np.isnan(col)] = first
        y[:, j] = col
    return y


def prepare_pressure(
    pressure: pl.DataFrame | None,
    grid: Grid,
    cfg: Config,
    log: ConditionLog,
    coords: pl.DataFrame | None = None,
    datum: float | None = None,
) -> PressurePrep:
    """Build p_wf on the grid for the producers, following §8 rules per source."""
    wells = [grid.well_of_entity.get(e, e) for e in grid.producers]
    if pressure is None or pressure.is_empty() or not ({"bhp", "whp"} & set(pressure.columns)):
        log.emit(ConditionCode.NO_PRESSURE)
        return PressurePrep(None, dict.fromkeys(grid.producers, "none"), None, "none")

    has_bhp_col = "bhp" in pressure.columns
    has_whp_col = "whp" in pressure.columns
    src_col = "src" if "src" in pressure.columns else None
    per_well_src: dict[str, str] = {}
    for w in wells:
        sub = pressure.filter(pl.col("well") == w)
        if sub.is_empty():
            per_well_src[w] = "none"
            continue
        src = (
            str(sub.get_column(src_col).drop_nulls().mode().to_list()[0])
            if src_col and sub.get_column(src_col).drop_nulls().len()
            else None
        )
        hb = has_bhp_col and sub.get_column("bhp").drop_nulls().len() > 0
        hw = has_whp_col and sub.get_column("whp").drop_nulls().len() > 0
        per_well_src[w] = classify_source(src, hb, hw)

    static_rows = None
    if src_col:
        flags = [
            classify_source(v, has_bhp_col, has_whp_col) == "static" for v in pressure.get_column(src_col).to_list()
        ]
        static_rows = pressure.filter(pl.Series(flags)) if any(flags) else None
    bhp = _to_grid(pressure, "bhp", grid, wells) if has_bhp_col else np.full((grid.n_steps, len(wells)), np.nan)
    whp = _to_grid(pressure, "whp", grid, wells) if has_whp_col else None
    window = int(cfg["pressure.esp_hampel_window"])
    wc = grid.water_cut
    grads = cfg.section("pressure")["mixture_gradient_psi_per_ft"]
    depth_by_well: dict[str, float] = {}
    if coords is not None and "tvd" in coords.columns:
        depth_by_well = {str(w): float(v) for w, v in coords.select(["well", "tvd"]).iter_rows() if v is not None}
    intake_depth: dict[str, float] = {}
    if "depth" in pressure.columns:
        for w, v in pressure.select(["well", "depth"]).drop_nulls().unique(subset=["well"]).iter_rows():
            intake_depth[str(w)] = float(v)

    out = np.full_like(bhp, np.nan)
    any_dynamic = False
    for j, (e, w) in enumerate(zip(grid.producers, wells, strict=True)):
        s = per_well_src[w]
        scope = f"well:{e}"
        if s == "none":
            continue
        if s == "static":
            log.emit(ConditionCode.PRESSURE_STATIC_ONLY, scope=scope, well=w)
            continue
        if s == "whp":
            log.emit(ConditionCode.PRESSURE_WHP_ONLY, scope=scope, well=w)
            if whp is not None:
                series = whp[:, j]
                vals = series[~np.isnan(series)]
                if (
                    len(vals) > 3
                    and vals.mean() > 0
                    and vals.std() / vals.mean() >= float(cfg["pressure.whp_variation_min_frac"])
                ):
                    out[:, j] = series
                    any_dynamic = True
            continue
        series = bhp[:, j].copy()
        valid = np.asarray(~np.isnan(series), dtype=bool)
        if valid.sum() == 0:
            continue
        if s == "esp":
            cleaned, _n = hampel(
                np.where(valid, series, np.nanmean(series)),
                window,
                float(cfg["cleaning.hampel_k"]),
                valid,
            )
            series = np.where(valid, cleaned, np.nan)
            # shift intake pressure to mid-perforation with a water-cut mixture gradient
            if w in intake_depth and w in depth_by_well:
                grad = mixture_gradient(wc[:, j], float(grads["water"]), float(grads["oil"]))
                series = series + np.asarray(grad) * (depth_by_well[w] - intake_depth[w])
            elif w in intake_depth or datum is not None:
                log.emit(ConditionCode.DATUM_DEPTH_MISSING, scope=scope, well=w)
        if datum is not None and w in depth_by_well:
            series = series + float(cfg["units.datum_gradient_psi_per_ft"]) * (datum - depth_by_well[w])
        out[:, j] = series
        any_dynamic = True

    if not any_dynamic:
        return PressurePrep(
            None,
            {e: per_well_src[w] for e, w in zip(grid.producers, wells, strict=True)},
            static_rows,
            _dominant(per_well_src),
        )
    filled = _fill(out)
    # producers without pressure keep a constant (their column mean of others → constant 0 change)
    for j in range(filled.shape[1]):
        if np.all(np.isnan(filled[:, j])):
            filled[:, j] = 0.0
    return PressurePrep(
        filled,
        {e: per_well_src[w] for e, w in zip(grid.producers, wells, strict=True)},
        static_rows,
        _dominant(per_well_src),
    )


def _dominant(sources: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for s in sources.values():
        counts[s] = counts.get(s, 0) + 1
    for pref in ("gauge", "esp", "whp", "static", "none"):
        if counts.get(pref):
            return pref
    return "none"
