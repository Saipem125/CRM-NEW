"""Event windowing — architecture §7 (workovers, conversions, long shut-ins → history windows).

Window breaks are global dates (the fit is per window); a break is taken when an event of a
``events.window_break_types`` type occurs, or a conversion is detected. Windows shorter than
``events.min_window_steps`` are merged with their neighbour. Short shut-ins are kept as signal;
shut-ins ≥ ``cleaning.long_shutin_months`` are reported per well (LONG_SHUTIN) and used by the
per-well mask, not as global breaks. Choke/ESP-frequency events are counted per well for the
crossflow gate (§9) and are the p_wf steps the J·dp_wf/dt term explains (§8).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.prep.grid import Grid


@dataclass(frozen=True)
class Window:
    start: int  # inclusive step index
    end: int  # exclusive
    reason: str = "full history"

    @property
    def n_steps(self) -> int:
        return self.end - self.start


def _step_of(grid: Grid, d: date) -> int:
    """First grid step at or after date d."""
    for k, gd in enumerate(grid.dates):
        if gd >= d:
            return k
    return grid.n_steps


def events_per_well(events: pl.DataFrame | None, n_wells: int) -> float:
    if events is None or events.is_empty() or n_wells == 0:
        return 0.0
    return float(events.height) / n_wells


def build_windows(grid: Grid, events: pl.DataFrame | None, cfg: Config, log: ConditionLog) -> list[Window]:
    """Global windows from break events (conversions, workovers, ...)."""
    break_types = {str(t).upper() for t in cfg["events.window_break_types"]}
    min_steps = int(cfg["events.min_window_steps"])
    breaks: dict[int, str] = {}
    if events is not None and not events.is_empty():
        for w, d, t in events.select(["well", "date", "type"]).iter_rows():
            if d is None or str(t).upper() not in break_types:
                continue
            k = _step_of(grid, d)
            if 0 < k < grid.n_steps:
                breaks.setdefault(k, f"{t} at {w} ({d})")
    bounds = [0, *sorted(breaks), grid.n_steps]
    windows: list[Window] = []
    for a, b in itertools.pairwise(bounds):
        reason = breaks.get(a, "full history" if a == 0 else "")
        windows.append(Window(a, b, reason if a == 0 else f"after {breaks[a]}"))
    # merge windows shorter than min_steps into the previous one (or the next when first)
    merged: list[Window] = []
    for w in windows:
        if merged and w.n_steps < min_steps:
            prev = merged.pop()
            merged.append(Window(prev.start, w.end, prev.reason))
        elif not merged and w.n_steps < min_steps and len(windows) > 1:
            merged.append(w)  # will be absorbed by the next
        else:
            if merged and merged[-1].n_steps < min_steps:
                prev = merged.pop()
                merged.append(Window(prev.start, w.end, w.reason))
            else:
                merged.append(w)
    return merged or [Window(0, grid.n_steps)]


def long_shutins(grid: Grid, cfg: Config, log: ConditionLog) -> dict[str, list[tuple[int, int]]]:
    """Per producer, runs of days_on = 0 at least ``cleaning.long_shutin_months`` steps long."""
    min_len = int(cfg["cleaning.long_shutin_months"])
    out: dict[str, list[tuple[int, int]]] = {}
    for j, w in enumerate(grid.producers):
        off = grid.days_on_prod[:, j] <= 0
        runs: list[tuple[int, int]] = []
        k = 0
        while k < grid.n_steps:
            if off[k]:
                s = k
                while k < grid.n_steps and off[k]:
                    k += 1
                if k - s >= min_len and s > 0 and k < grid.n_steps:
                    runs.append((s, k))
            else:
                k += 1
        if runs:
            out[w] = runs
            s0, e0 = runs[0]
            log.emit(
                ConditionCode.LONG_SHUTIN,
                scope=f"well:{w}",
                well=w,
                months=e0 - s0,
                when=grid.dates[s0].strftime("%Y-%m"),
            )
    return out


def lift_events_per_producer(
    events: pl.DataFrame | None, producers: list[str], well_of_entity: dict[str, str]
) -> np.ndarray:
    """Count of choke / ESP / lift events per producer (used by the crossflow gate)."""
    counts = np.zeros(len(producers))
    if events is None or events.is_empty():
        return counts
    lift = {"CHOKE", "ESP_FREQ", "ESP", "LIFT", "PUMP", "FREQUENCY"}
    per_well: dict[str, int] = {}
    for w, t in events.select(["well", "type"]).iter_rows():
        if str(t).upper() in lift:
            per_well[str(w)] = per_well.get(str(w), 0) + 1
    for j, e in enumerate(producers):
        counts[j] = per_well.get(well_of_entity.get(e, e), 0)
    return counts
