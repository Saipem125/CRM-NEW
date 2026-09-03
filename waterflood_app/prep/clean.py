"""Outlier cleaning — architecture §7 (Hampel / MAD per well, physical caps, raw retained)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.prep.grid import Grid

FArray = npt.NDArray[np.float64]


def hampel(
    x: FArray,
    window: int = 7,
    k: float = 3.0,
    active: npt.NDArray[np.bool_] | None = None,
    min_rel_dev: float = 0.0,
) -> tuple[FArray, int]:
    """Replace isolated spikes farther than k·1.4826·MAD from the centred rolling median.

    Rules (§7 "short shut-ins retained as signal"): zero-rate steps and inactive steps are never
    replaced and never enter the window statistics; a point is a spike only if it also deviates
    from *both* neighbours by more than k·MAD, so a genuine level change (a new injection target)
    is kept. Windows with fewer than 3 usable points are left untouched. Returns (cleaned, n).
    """
    x = np.asarray(x, dtype=np.float64)
    out = x.copy()
    n = len(x)
    act = np.ones(n, dtype=bool) if active is None else np.asarray(active, dtype=bool)
    usable = act & (x > 0)
    half = max(1, window // 2)
    replaced = 0
    for t in range(n):
        if not usable[t]:
            continue
        lo, hi = max(0, t - half), min(n, t + half + 1)
        seg = x[lo:hi][usable[lo:hi]]
        if len(seg) < 3:
            continue
        med = float(np.median(seg))
        mad = 1.4826 * float(np.median(np.abs(seg - med)))
        if mad <= 0 or abs(x[t] - med) <= k * mad or abs(x[t] - med) < min_rel_dev * abs(med):
            continue
        neighbours = [x[s] for s in (t - 1, t + 1) if 0 <= s < n and usable[s]]
        if neighbours and all(abs(x[t] - v) > k * mad for v in neighbours):
            out[t] = med
            replaced += 1
    return out, replaced


def clean_grid(grid: Grid, cfg: Config, log: ConditionLog) -> Grid:
    """Hampel-clean producer liquid on active steps; cap negatives; keep raw copies.

    Injection is a control input, not a response: it is never smoothed (a spike is real water),
    only capped at zero (DECISIONS.md, M1).
    """
    window = int(cfg["cleaning.hampel_window"])
    k = float(cfg["cleaning.hampel_k"])
    rel = float(cfg.get("cleaning.hampel_min_rel_dev", 0.0))
    inj = grid.inj.copy()
    liq, oil, water = grid.liq.copy(), grid.oil.copy(), grid.water.copy()
    for j, w in enumerate(grid.injectors):
        neg = int((inj[:, j] < 0).sum())
        if neg:
            inj[:, j] = np.maximum(inj[:, j], 0.0)
            log.emit(ConditionCode.NEGATIVE_RATES_CAPPED, scope=f"well:{w}", well=w, count=neg)
    for j, w in enumerate(grid.producers):
        neg = int(((oil[:, j] < 0) | (water[:, j] < 0)).sum())
        if neg:
            oil[:, j] = np.maximum(oil[:, j], 0.0)
            water[:, j] = np.maximum(water[:, j], 0.0)
            log.emit(ConditionCode.NEGATIVE_RATES_CAPPED, scope=f"well:{w}", well=w, count=neg)
        act = grid.days_on_prod[:, j] > 0
        new_liq, n = hampel(liq[:, j], window, k, act, rel)
        if n:
            # scale oil and water with the liquid correction so the water cut is preserved
            with np.errstate(divide="ignore", invalid="ignore"):
                scale = np.where(liq[:, j] > 0, new_liq / np.where(liq[:, j] > 0, liq[:, j], 1.0), 1.0)
            oil[:, j] *= scale
            water[:, j] *= scale
            liq[:, j] = new_liq
            log.emit(ConditionCode.OUTLIERS_REMOVED, scope=f"well:{w}", well=w, count=n)
    return replace(grid, inj=inj, liq=liq, oil=oil, water=water)
