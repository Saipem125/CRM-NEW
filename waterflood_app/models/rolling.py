"""Rolling-window fitting — architecture §10 (time-varying connectivity).

Besides the full-history fit, overlapping windows (default 36 months, step 6) are fitted and
f_ij(t), τ(t) stored per window; event boundaries force a window break. The forecast and the
optimizer use the latest window's parameters blended with the full-history fit by their
blind-test scores (§10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Any

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionLog
from waterflood_app.models.base import FitData, ModelParams
from waterflood_app.models.crmp import CRMP
from waterflood_app.models.uq import parameter_spread, select_members
from waterflood_app.models.verify import verify
from waterflood_app.prep.grid import Grid
from waterflood_app.prep.split import train_blind_split

FArray = npt.NDArray[np.float64]


@dataclass
class WindowFit:
    start: int
    end: int
    start_date: date
    end_date: date
    params: ModelParams
    blind_r2: float
    f_spread: float
    tau_spread: float

    @property
    def mid_date(self) -> date:
        return self.start_date + (self.end_date - self.start_date) / 2


@dataclass
class RollingResult:
    windows: list[WindowFit]
    injectors: list[str]
    producers: list[str]
    full: ModelParams | None = None
    full_blind_r2: float = float("nan")
    blended: ModelParams | None = None
    weights: dict[str, float] = field(default_factory=dict)

    def f_series(self, i: str, j: str) -> tuple[list[date], FArray]:
        a, b = self.injectors.index(i), self.producers.index(j)
        return [w.mid_date for w in self.windows], np.array([w.params.f[a, b] for w in self.windows])

    def tau_series(self, j: str) -> tuple[list[date], FArray]:
        b = self.producers.index(j)
        return [w.mid_date for w in self.windows], np.array(
            [float(np.ravel(w.params.tau_per_producer())[b]) for w in self.windows]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "windows": [
                {
                    "start": w.start_date.isoformat(),
                    "end": w.end_date.isoformat(),
                    "blind_r2": w.blind_r2,
                    "f_spread": w.f_spread,
                    "tau_spread": w.tau_spread,
                    "params": w.params.to_dict(self.injectors, self.producers),
                }
                for w in self.windows
            ],
            "weights": self.weights,
        }


def window_bounds(n_steps: int, cfg: Config, breaks: list[int] | None = None) -> list[tuple[int, int]]:
    """[start, end) pairs: fixed length, fixed step, never crossing an event break."""
    r = cfg.section("rolling")
    length, step, min_len = int(r["window_months"]), int(r["step_months"]), int(r["min_window_steps"])
    cuts = sorted({0, n_steps, *(b for b in (breaks or []) if 0 < b < n_steps)})
    out: list[tuple[int, int]] = []
    for a, b in pairwise(cuts):
        s = a
        while s + min_len <= b:
            e = min(s + length, b)
            out.append((s, e))
            if e == b:
                break
            s += step
    return out


def fit_rolling(
    grid: Grid,
    cfg: Config,
    breaks: list[int] | None = None,
    seed: int = 0,
    full: ModelParams | None = None,
    full_blind_r2: float = float("nan"),
    variant: str = "crmp",
) -> RollingResult:
    """Fit CRMP on every window; blend the latest window with the full-history fit by blind score."""
    fits: list[WindowFit] = []
    for a, b in window_bounds(grid.n_steps, cfg, breaks):
        wgrid = grid.window(a, b)
        split = train_blind_split(wgrid.n_steps, cfg)
        model = CRMP(cfg)
        res = model.fit(FitData(wgrid, split), seed=seed, n_starts=3)
        rep = verify(
            wgrid, res.prediction, res.params, split, variant, res.n_params, res.sse_train, cfg, ConditionLog()
        )
        sp = parameter_spread(select_members(res, cfg))
        fits.append(
            WindowFit(
                a, b, grid.dates[a], grid.dates[b - 1], res.params, rep.blind_r2_field, sp.f_spread, sp.tau_spread
            )
        )
    out = RollingResult(fits, list(grid.injectors), list(grid.producers), full=full, full_blind_r2=full_blind_r2)
    if fits and full is not None and bool(cfg["rolling.blend_by_blind_score"]):
        last = fits[-1]
        w_last = max(last.blind_r2, 0.0) if np.isfinite(last.blind_r2) else 0.0
        w_full = max(full_blind_r2, 0.0) if np.isfinite(full_blind_r2) else 0.0
        tot = w_last + w_full
        wl, wf = (w_last / tot, w_full / tot) if tot > 0 else (0.5, 0.5)
        blended = full.copy()
        blended.f = wl * last.params.f + wf * full.f
        blended.tau = wl * last.params.tau + wf * full.tau if last.params.tau.shape == full.tau.shape else full.tau
        if blended.J is not None and last.params.J is not None and last.params.J.shape == blended.J.shape:
            blended.J = wl * last.params.J + wf * blended.J
        out.blended = blended
        out.weights = {"latest_window": wl, "full_history": wf}
    return out
