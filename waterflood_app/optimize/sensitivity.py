"""Economic sensitivity (tornado) of a plan — architecture §12 (price tornado) and prompt §4.2.

Each item perturbs one economic input and re-evaluates the posture-aggregated NPV gain of the
plan over hold-current on the *same* forecast ensemble (no re-optimisation); a final item shows
the forecast (P10 / P90 member) range. Values are in currency for the NPV objective; for the oil
objective the tornado is expressed as the incremental cumulative oil, which does not depend on
economics, so only the forecast-range item is produced.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from waterflood_app.config import Config
from waterflood_app.ingest.units import BBL_PER_M3
from waterflood_app.models.forecast import Forecast
from waterflood_app.optimize.economics import Economics
from waterflood_app.optimize.posture import aggregate

PERTURBATIONS: tuple[tuple[str, str, float, float], ...] = (
    ("oil_price", "Oil price −30 % / +30 %", 0.7, 1.3),
    ("water_handling_cost", "Water handling cost +50 % / −50 %", 1.5, 0.5),
    ("injection_cost", "Injection cost +50 % / −50 %", 1.5, 0.5),
    ("discount_rate", "Discount rate +5 pts / −5 pts", 1.0, 1.0),  # additive, handled below
)


def _gain(
    econ: Economics, plan: list[Forecast], base: list[Forecast], weights: list[float] | None, posture: str, cfg: Config
) -> float:
    vp = np.array([econ.npv(f, BBL_PER_M3) for f in plan])
    vb = np.array([econ.npv(f, BBL_PER_M3) for f in base])
    return float(aggregate(vp, posture, cfg) - aggregate(vb, posture, cfg))


def economic_tornado(
    econ: Economics,
    forecasts_plan: list[Forecast],
    forecasts_base: list[Forecast],
    posture: str,
    cfg: Config,
    weights: list[float] | None = None,
) -> dict[str, Any]:
    """Tornado items for ΔNPV (plan − hold-current) under one-at-a-time perturbations."""
    if not forecasts_plan or not forecasts_base:
        return {"unit": "currency", "base": 0.0, "items": []}
    base_gain = _gain(econ, forecasts_plan, forecasts_base, weights, posture, cfg)
    items: list[dict[str, Any]] = []
    for attr, label, lo_f, hi_f in PERTURBATIONS:
        if attr == "discount_rate":
            lo_e = replace(econ, discount_rate=econ.discount_rate + 0.05)
            hi_e = replace(econ, discount_rate=max(0.0, econ.discount_rate - 0.05))
        elif attr == "oil_price":
            lo_e, hi_e = econ.with_price(econ.oil_price * lo_f), econ.with_price(econ.oil_price * hi_f)
        else:
            lo_e = replace(econ, **{attr: getattr(econ, attr) * lo_f})
            hi_e = replace(econ, **{attr: getattr(econ, attr) * hi_f})
        items.append(
            {
                "label": label,
                "low": _gain(lo_e, forecasts_plan, forecasts_base, weights, posture, cfg),
                "high": _gain(hi_e, forecasts_plan, forecasts_base, weights, posture, cfg),
            }
        )
    items.append(forecast_range_item(econ, forecasts_plan, forecasts_base))
    return {"unit": "currency", "base": base_gain, "items": items}


def forecast_range_item(econ: Economics | None, plan: list[Forecast], base: list[Forecast]) -> dict[str, Any]:
    """P10 / P90 of the per-member gain — the model-uncertainty bar of the tornado."""
    if econ is None:
        gains = np.array([p.cum_oil - b.cum_oil for p, b in zip(plan, base, strict=False)])
    else:
        gains = np.array([econ.npv(p, BBL_PER_M3) - econ.npv(b, BBL_PER_M3) for p, b in zip(plan, base, strict=False)])
    if gains.size == 0:
        return {"label": "Forecast range (P10 / P90)", "low": 0.0, "high": 0.0}
    return {
        "label": "Forecast range (P10 / P90)",
        "low": float(np.percentile(gains, 10)),
        "high": float(np.percentile(gains, 90)),
    }


def oil_tornado(
    forecasts_plan: list[Forecast], forecasts_base: list[Forecast], posture: str, cfg: Config
) -> dict[str, Any]:
    """For the cumulative-oil objective: only the forecast-range item (no economics involved)."""
    if not forecasts_plan or not forecasts_base:
        return {"unit": "volume", "base": 0.0, "items": []}
    vp = np.array([f.cum_oil for f in forecasts_plan])
    vb = np.array([f.cum_oil for f in forecasts_base])
    return {
        "unit": "volume",
        "base": float(aggregate(vp, posture, cfg) - aggregate(vb, posture, cfg)),
        "items": [forecast_range_item(None, forecasts_plan, forecasts_base)],
    }
