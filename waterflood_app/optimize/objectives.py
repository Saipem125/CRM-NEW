"""Objectives — architecture §12: max cumulative oil · max NPV · min water for a target oil."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from waterflood_app.ingest.units import BBL_PER_M3
from waterflood_app.models.forecast import Forecast
from waterflood_app.optimize.economics import Economics


@dataclass(frozen=True)
class Objective:
    """Higher is better. ``extra_constraints`` are g(forecast) ≥ 0 terms the objective needs."""

    name: str

    def value(self, f: Forecast) -> float:
        raise NotImplementedError

    def extra_constraints(self, f: Forecast) -> np.ndarray:
        return np.zeros(0)

    @property
    def unit(self) -> str:
        return ""


@dataclass(frozen=True)
class CumulativeOil(Objective):
    name: str = "max_cumulative_oil"

    def value(self, f: Forecast) -> float:
        return f.cum_oil

    @property
    def unit(self) -> str:
        return "volume"


@dataclass(frozen=True)
class NetPresentValue(Objective):
    economics: Economics = field(default_factory=lambda: Economics(70.0, 1.5, 0.8, 0.1))
    bbl_per_unit: float = 1.0
    name: str = "max_npv"

    def value(self, f: Forecast) -> float:
        return self.economics.npv(f, self.bbl_per_unit)

    @property
    def unit(self) -> str:
        return "currency"


@dataclass(frozen=True)
class MinWaterForTargetOil(Objective):
    target_oil: float = 0.0  # cumulative surface oil over the horizon (internal units × days)
    name: str = "min_water_for_target_oil"

    def value(self, f: Forecast) -> float:
        return -f.cum_inj

    def extra_constraints(self, f: Forecast) -> np.ndarray:
        return np.array([f.cum_oil - self.target_oil])

    @property
    def unit(self) -> str:
        return "volume"


def make_objective(name: str, economics: Economics | None = None, target_oil: float | None = None) -> Objective:
    if name in ("oil", "max_cumulative_oil", "cumulative_oil"):
        return CumulativeOil()
    if name in ("npv", "max_npv"):
        return NetPresentValue(economics=economics or Economics(70.0, 1.5, 0.8, 0.1), bbl_per_unit=BBL_PER_M3)
    if name in ("min_water", "min_water_for_target_oil"):
        return MinWaterForTargetOil(target_oil=float(target_oil or 0.0))
    raise ValueError(f"unknown objective {name!r}")
