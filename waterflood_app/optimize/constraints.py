"""Plan constraints — architecture §12.

Total water available · per-injector min/max (injectivity, frac gradient) · producer liquid /
ESP limits · max water cut · VRR band per pattern · reservoir-pressure window (needs a pressure
model: applied when the aquifer/pressure variant provides p̄_r, otherwise skipped and noted).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config
from waterflood_app.models.forecast import Forecast

FArray = npt.NDArray[np.float64]


@dataclass
class PlanConstraints:
    total_water: float | None  # Σ_i x_i per period (equality when `same_water`)
    same_water: bool = True
    inj_min: FArray = field(default_factory=lambda: np.zeros(0))
    inj_max: FArray = field(default_factory=lambda: np.zeros(0))
    producer_liquid_max: FArray | None = None  # (Np,) surface liquid limit
    max_water_cut: float | None = None
    vrr_band: tuple[float, float] | None = None
    pressure_window: tuple[float, float] | None = None  # applied only when p̄_r is forecast
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_config(
        cls,
        current: FArray,
        cfg: Config,
        inj_min: FArray | None = None,
        inj_max: FArray | None = None,
        producer_liquid_max: FArray | None = None,
        same_water: bool = True,
        total_water: float | None = None,
    ) -> PlanConstraints:
        o = cfg.section("optimize")
        n = len(current)
        mean = float(current.mean()) if n else 0.0
        lo = np.zeros(n) if inj_min is None else np.asarray(inj_min, dtype=np.float64)
        hi = (
            np.full(n, float(o["injector_max_multiple_of_mean"]) * mean)
            if inj_max is None
            else np.asarray(inj_max, dtype=np.float64)
        )
        return cls(
            total_water=float(current.sum()) if total_water is None else float(total_water),
            same_water=same_water,
            inj_min=lo,
            inj_max=hi,
            producer_liquid_max=producer_liquid_max,
            max_water_cut=None if o.get("max_water_cut") is None else float(o["max_water_cut"]),
            vrr_band=None if o.get("vrr_band") is None else (float(o["vrr_band"][0]), float(o["vrr_band"][1])),
        )

    def bounds(self, n_periods: int) -> list[tuple[float, float]]:
        return [
            (float(lo), float(hi)) for _ in range(n_periods) for lo, hi in zip(self.inj_min, self.inj_max, strict=True)
        ]

    def water_constraints(self, x: FArray, n_periods: int) -> tuple[FArray, FArray]:
        """(equalities, inequalities ≥ 0) on the decision vector, per period."""
        xs = x.reshape(n_periods, -1)
        if self.total_water is None:
            return np.zeros(0), np.zeros(0)
        sums = xs.sum(axis=1)
        if self.same_water:
            return sums - self.total_water, np.zeros(0)
        return np.zeros(0), self.total_water - sums

    def forecast_constraints(self, f: Forecast) -> FArray:
        """g(forecast) ≥ 0 terms: liquid limits, water cut, VRR band."""
        terms: list[FArray] = []
        if self.producer_liquid_max is not None:
            terms.append((self.producer_liquid_max[None, :] - f.liq_surface).ravel())
        if self.max_water_cut is not None:
            terms.append(np.array([self.max_water_cut - float(np.nanmax(f.water_cut))]))
        if self.vrr_band is not None:
            tot_liq = float((f.liq_res * f.dt_days[:, None]).sum())
            vrr = f.cum_inj / tot_liq if tot_liq > 0 else 1.0
            terms.append(np.array([vrr - self.vrr_band[0], self.vrr_band[1] - vrr]))
        return np.concatenate(terms) if terms else np.zeros(0)
