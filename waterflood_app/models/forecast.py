"""Forecast continuation — the calibrated model driven by a future injection plan (§12, §13).

A :class:`ForecastModel` bundles the fitted CRM parameters, the oil-cut fits and the history
needed to continue the recursion from the last historical state. ``simulate`` returns liquid
(reservoir), oil and water (surface) per producer for a plan; a list of models built from the
multi-start ensemble gives the P10/P50/P90 fan (§13).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

import numpy as np
import numpy.typing as npt

from waterflood_app.models.base import ModelParams
from waterflood_app.models.fractional_flow import PowerLawOilCut, cumulative_basis
from waterflood_app.models.solver import predict_field
from waterflood_app.prep.grid import Grid

FArray = npt.NDArray[np.float64]


@dataclass
class Forecast:
    dt_days: FArray  # (H,)
    inj: FArray  # (H, Ni) reservoir rates
    liq_res: FArray  # (H, Np) reservoir liquid
    oil: FArray  # (H, Np) surface oil
    water: FArray  # (H, Np) surface water
    cwi: FArray  # (H, Np) cumulative basis at step end
    injectors: list[str]
    producers: list[str]

    @property
    def cum_oil(self) -> float:
        return float((self.oil * self.dt_days[:, None]).sum())

    @property
    def cum_water(self) -> float:
        return float((self.water * self.dt_days[:, None]).sum())

    @property
    def cum_inj(self) -> float:
        return float((self.inj * self.dt_days[:, None]).sum())

    @property
    def water_cut(self) -> FArray:
        tot = self.oil + self.water
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(tot > 0, self.water / np.where(tot > 0, tot, 1.0), 0.0)

    @property
    def liq_surface(self) -> FArray:
        return self.oil + self.water


def month_steps(start: date, n: int) -> tuple[list[date], FArray]:
    """Monthly dates after ``start`` and their Δt (days)."""
    dates: list[date] = []
    y, m = start.year, start.month
    for _ in range(n):
        m += 1
        if m > 12:
            m, y = 1, y + 1
        dates.append(date(y, m, 1))
    prev = [start, *dates[:-1]]
    dt = np.array([(d - p).days for d, p in zip(dates, prev, strict=True)], dtype=np.float64)
    return dates, dt


@dataclass
class ForecastModel:
    variant: str
    params: ModelParams
    oilcut: dict[str, PowerLawOilCut]
    grid: Grid  # history (sector grid)
    surface_ratio: FArray  # (Np,) surface liquid / reservoir liquid
    label: str = "p50"

    @property
    def producers(self) -> list[str]:
        return self.grid.producers

    @property
    def injectors(self) -> list[str]:
        return self.grid.injectors

    def current_injection(self, months: int = 1) -> FArray:
        """Mean injection over the last ``months`` steps (the "hold current" base)."""
        return np.asarray(self.grid.inj[-months:].mean(axis=0), dtype=np.float64)

    def historical_cwi_end(self) -> FArray:
        support = self.grid.inj @ self.params.f
        return np.asarray(np.cumsum(support * self.grid.dt_days[:, None], axis=0)[-1], dtype=np.float64)

    def simulate(self, inj_plan: FArray, dt_days: FArray, bhp_future: FArray | None = None) -> Forecast:
        """Continue the fitted model over a plan (H, Ni) with step lengths dt_days (H,)."""
        g = self.grid
        h = inj_plan.shape[0]
        t_future = g.time_days[-1] + np.cumsum(dt_days)
        bhp = None
        if g.bhp is not None:
            fut = np.tile(g.bhp[-1], (h, 1)) if bhp_future is None else bhp_future
            bhp = np.vstack([g.bhp, fut])
        ext = replace(
            g,
            dates=g.dates + [g.dates[-1]] * h,
            time_days=np.concatenate([g.time_days, t_future]),
            dt_days=np.concatenate([g.dt_days, dt_days]),
            inj=np.vstack([g.inj, inj_plan]),
            liq=np.vstack([g.liq, np.zeros((h, g.n_prod))]),
            oil=np.vstack([g.oil, np.zeros((h, g.n_prod))]),
            water=np.vstack([g.water, np.zeros((h, g.n_prod))]),
            days_on_prod=np.vstack([g.days_on_prod, np.full((h, g.n_prod), dt_days.mean())]),
            days_on_inj=np.vstack([g.days_on_inj, np.full((h, g.n_inj), dt_days.mean())]),
            bhp=bhp,
            raw={},
        )
        liq = np.maximum(predict_field(ext, self.params, self.variant)[-h:], 0.0)
        support = inj_plan @ self.params.f
        cwi0 = self.historical_cwi_end()
        cwi = np.zeros((h, g.n_prod))
        oil = np.zeros((h, g.n_prod))
        for j, w in enumerate(g.producers):
            fit = self.oilcut.get(w)
            basis = fit.basis if fit is not None else "allocated_water"
            cwi[:, j] = cumulative_basis(liq[:, j], support[:, j], dt_days, basis, offset=float(cwi0[j]))
            fo = fit.oil_cut(cwi[:, j]) if fit is not None else np.full(h, 0.5)
            oil[:, j] = liq[:, j] * self.surface_ratio[j] * fo
        water = liq * self.surface_ratio[None, :] - oil
        return Forecast(dt_days, inj_plan, liq, oil, np.maximum(water, 0.0), cwi, list(g.injectors), list(g.producers))


def surface_ratio(grid: Grid) -> FArray:
    """Median surface-liquid / reservoir-liquid ratio per producer over producing steps."""
    out = np.ones(grid.n_prod)
    mask = grid.prod_mask
    for j in range(grid.n_prod):
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(
                grid.liq[:, j] > 0,
                (grid.oil[:, j] + grid.water[:, j]) / np.where(grid.liq[:, j] > 0, grid.liq[:, j], 1.0),
                np.nan,
            )
        vals = r[mask[:, j]]
        if np.isfinite(vals).any():
            out[j] = float(np.nanmedian(vals))
    return out


def fan(forecasts: list[Forecast], weights: list[float] | None = None) -> dict[str, Any]:
    """P10 / P50 / P90 of oil per producer and of cumulative oil across ensemble forecasts (§13)."""
    oil = np.stack([f.oil for f in forecasts])
    cum = np.array([f.cum_oil for f in forecasts])
    return {
        "oil_p10": np.percentile(oil, 10, axis=0),
        "oil_p50": np.percentile(oil, 50, axis=0),
        "oil_p90": np.percentile(oil, 90, axis=0),
        "cum_oil_p10": float(np.percentile(cum, 10)),
        "cum_oil_p50": float(np.percentile(cum, 50)),
        "cum_oil_p90": float(np.percentile(cum, 90)),
        "n_members": len(forecasts),
    }
