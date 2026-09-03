"""Economics — architecture §12 (NPV objective, price deck, costs, discount rate, price tornado)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from waterflood_app.config import Config
from waterflood_app.models.forecast import Forecast


@dataclass(frozen=True)
class Economics:
    oil_price: float  # currency per surface barrel
    water_handling_cost: float  # per barrel of produced water
    injection_cost: float  # per barrel injected
    discount_rate: float  # per year
    price_deck: tuple[float, ...] = ()  # optional per-month prices overriding oil_price

    @classmethod
    def from_config(cls, cfg: Config, overrides: dict[str, Any] | None = None) -> Economics:
        e = cfg.section("economics")
        o = overrides or {}
        return cls(
            oil_price=float(o.get("oil_price_usd_per_bbl", e["oil_price_usd_per_bbl"])),
            water_handling_cost=float(o.get("water_handling_usd_per_bbl", e["water_handling_usd_per_bbl"])),
            injection_cost=float(o.get("injection_usd_per_bbl", e["injection_usd_per_bbl"])),
            discount_rate=float(o.get("discount_rate_per_year", e["discount_rate_per_year"])),
            price_deck=tuple(float(p) for p in o.get("price_deck", ())),
        )

    def prices(self, n: int) -> np.ndarray:
        if self.price_deck:
            deck = np.asarray(self.price_deck, dtype=np.float64)
            return np.concatenate([deck[:n], np.full(max(0, n - len(deck)), deck[-1])])
        return np.full(n, self.oil_price)

    def npv(self, f: Forecast, bbl_per_unit: float = 1.0) -> float:
        """NPV = Σ_t (price·q_o − water cost·q_w − injection cost·i)·Δt / (1 + r)^{t/365} (§12)."""
        t_years = np.cumsum(f.dt_days) / 365.25
        disc = (1.0 + self.discount_rate) ** (-t_years)
        oil = f.oil.sum(axis=1) * f.dt_days * bbl_per_unit
        water = f.water.sum(axis=1) * f.dt_days * bbl_per_unit
        inj = f.inj.sum(axis=1) * f.dt_days * bbl_per_unit
        cash = self.prices(len(f.dt_days)) * oil - self.water_handling_cost * water - self.injection_cost * inj
        return float((cash * disc).sum())

    def with_price(self, price: float) -> Economics:
        return replace(self, oil_price=price, price_deck=())


def price_tornado(
    econ: Economics,
    forecast_plan: Forecast,
    forecast_base: Forecast,
    factors: tuple[float, ...] = (0.7, 0.85, 1.0, 1.15, 1.3),
    bbl_per_unit: float = 1.0,
) -> list[dict[str, float]]:
    """ΔNPV (plan − base) at several oil-price multiples — the §12 price sensitivity tornado."""
    out = []
    for k in factors:
        e = econ.with_price(econ.oil_price * k)
        out.append(
            {
                "price_factor": k,
                "price": e.oil_price,
                "delta_npv": e.npv(forecast_plan, bbl_per_unit) - e.npv(forecast_base, bbl_per_unit),
            }
        )
    return out
