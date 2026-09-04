"""Result bundle — everything the Result and Details screens draw, as plain JSON (§2, §4, §9, §14).

Built once when a run finishes and stored with the run, so the UI never needs the in-memory
model objects: per-producer history / cleaned / model / blind window, forecast fans, well map
(coordinates, f_ij, τ, pair confidence), leaderboard and exclusions, gates and profile, Δt/τ
numbers, pressure handling, conditions, the change-this-week list and the ramp, change alerts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import numpy as np

from waterflood_app.config import Config
from waterflood_app.engine import RunResult, SectorRun
from waterflood_app.ingest.schema import DEFAULT_UNITS, INTERNAL_UNITS
from waterflood_app.ingest.units import convert
from waterflood_app.models.uq import select_members
from waterflood_app.optimize.run import SectorRecommendation


class Display:
    """Conversion factors from internal units to the project's unit system (§6 "Display converts back")."""

    def __init__(self, unit_system: str = "field") -> None:
        self.units = dict(DEFAULT_UNITS.get(unit_system, DEFAULT_UNITS["field"]))
        self.units["volume"] = "bbl" if unit_system == "field" else "m3"
        self.rate = float(convert(1.0, INTERNAL_UNITS["rate"], self.units["rate"]))
        self.volume = float(convert(1.0, "m3", self.units["volume"]))
        self.pressure = float(convert(1.0, INTERNAL_UNITS["pressure"], self.units["pressure"]))

    def r(self, x: Any) -> Any:  # rates
        return _f(np.asarray(x, dtype=np.float64) * self.rate) if x is not None else None

    def v(self, x: Any) -> Any:  # volumes
        return _f(np.asarray(x, dtype=np.float64) * self.volume) if x is not None else None

    def p(self, x: Any) -> Any:  # pressures
        return _f(np.asarray(x, dtype=np.float64) * self.pressure) if x is not None else None


def _f(x: Any) -> Any:
    """numpy → JSON-safe (NaN → None)."""
    if isinstance(x, np.ndarray):
        return [_f(v) for v in x.tolist()]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, dict):
        return {str(k): _f(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_f(v) for v in x]
    return x


def _pair_confidence(s: SectorRun, cfg: Config) -> list[list[float]]:
    """1 − relative spread of f_ij across ensemble members (1 = all members agree)."""
    w = s.tournament.winner
    if w is None:
        return []
    members = select_members(w.fit, cfg)
    F = np.stack([m.f for m in members])
    mean = F.mean(axis=0)
    std = F.std(axis=0) if len(members) > 1 else np.zeros_like(mean)
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(mean > 1e-3, std / np.maximum(mean, 1e-9), 0.0)
    return _f(np.clip(1.0 - rel, 0.0, 1.0))  # type: ignore[no-any-return]


def sector_bundle(
    s: SectorRun,
    rec: SectorRecommendation | None,
    cfg: Config,
    well_types: dict[str, str],
    pressure_sources: Mapping[str, str] | None = None,
    d: Display | None = None,
) -> dict[str, Any]:
    pressure_sources = pressure_sources or {}
    d = d or Display()
    g = s.grid
    w = s.tournament.winner
    n_train = int(w.report.n_train_points / max(g.n_prod, 1)) if w is not None and g.n_prod else int(0.8 * g.n_steps)
    split_idx = round(g.n_steps * (1 - float(cfg["split.blind_fraction"])))
    dates = [d.isoformat() for d in g.dates]
    producers: list[dict[str, Any]] = []
    pred = w.fit.prediction if w is not None else np.zeros_like(g.liq)
    per = {m.producer: m for m in w.report.per_producer} if w is not None else {}
    for j, p in enumerate(g.producers):
        m = per.get(p)
        producers.append(
            {
                "id": p,
                "well": g.well_of_entity.get(p, p),
                "raw_liquid": d.r(g.raw["liq"][:, j]) if "liq" in g.raw else d.r(g.liq[:, j]),
                "liquid": d.r(g.liq[:, j]),
                "oil": d.r(g.oil[:, j]),
                "water": d.r(g.water[:, j]),
                "model": d.r(pred[:, j]),
                "oil_model": d.r(s.oil_prediction[:, j]) if s.oil_prediction is not None else None,
                "days_on": _f(g.days_on_prod[:, j]),
                "bhp": d.p(g.bhp[:, j]) if g.bhp is not None else None,
                "pressure_source": pressure_sources.get(p, "none"),
                "blind_r2": _f(m.blind_r2) if m else None,
                "blind_mape": _f(m.blind_mape) if m else None,
                "train_r2": _f(m.train_r2) if m else None,
                "autocorr": _f(m.autocorr_lag1) if m else None,
                "xy": _f(g.xy_prod[j]) if g.xy_prod is not None else None,
            }
        )
    injectors = [
        {
            "id": i,
            "well": g.well_of_entity.get(i, i),
            "rate": d.r(g.inj[:, k]),
            "days_on": _f(g.days_on_inj[:, k]),
            "xy": _f(g.xy_inj[k]) if g.xy_inj is not None else None,
        }
        for k, i in enumerate(g.injectors)
    ]
    params = w.fit.params if w is not None else None
    model = None
    if params is not None:
        tau_prod = params.tau_per_producer()
        model = {
            "variant": w.variant,  # type: ignore[union-attr]
            "label": w.label,  # type: ignore[union-attr]
            "f_ij": _f(params.f),
            "tau_days": _f(tau_prod),
            "tau_ij_days": _f(params.tau) if params.tau.ndim == 2 else None,
            "J": _f(params.J) if params.J is not None else None,
            "pair_confidence": _pair_confidence(s, cfg),
            "sum_f_per_injector": _f(params.sum_f_per_injector),
            "sum_f_per_producer": _f(params.sum_f_per_producer),
            "extra": _f({k: v for k, v in params.extra.items() if k not in ("influx",)}),
            "n_params": w.fit.n_params,  # type: ignore[union-attr]
            "blind_r2": _f(w.report.blind_r2_field),  # type: ignore[union-attr]
            "blind_mape": _f(w.report.blind_mape_median),  # type: ignore[union-attr]
            "aicc": _f(w.report.aicc),  # type: ignore[union-attr]
            "spread": _f(w.spread.f_spread),  # type: ignore[union-attr]
            "plausible": w.report.plausible,  # type: ignore[union-attr]
            "plausibility_notes": w.report.plausibility_notes,  # type: ignore[union-attr]
        }
    prof = s.profile
    dt_tau = {
        "dt_days": _f(prof.dt_days),
        "tau_estimate_days": _f(prof.tau_estimate_days),
        "tau_over_dt": _f(prof.tau_over_dt),
        "tau_fitted_days": _f(params.tau_per_producer()) if params is not None else None,
        "monthly_allocated": prof.monthly_allocated,
        "od": _f(prof.od),
        "history_needed_for_od": int(np.ceil(6 * (prof.n_inj + 1))),
    }
    fc: dict[str, Any] | None = None
    if rec is not None:
        r = rec.result
        from waterflood_app.models.forecast import month_steps

        fdates, _ = month_steps(g.dates[-1], len(r.plan.dt_days))
        dates_f = [d.isoformat() for d in fdates]
        plan_oil = np.stack([f.oil for f in r.forecasts_plan])
        base_oil = np.stack([f.oil for f in r.forecasts_base])
        fc = {
            "dates": dates_f,
            "plan": {
                "p10": d.r(np.percentile(plan_oil, 10, axis=0)),
                "p50": d.r(np.percentile(plan_oil, 50, axis=0)),
                "p90": d.r(np.percentile(plan_oil, 90, axis=0)),
            },
            "base": {
                "p10": d.r(np.percentile(base_oil, 10, axis=0)),
                "p50": d.r(np.percentile(base_oil, 50, axis=0)),
                "p90": d.r(np.percentile(base_oil, 90, axis=0)),
            },
            "field_plan": {
                "p10": d.r(np.percentile(plan_oil.sum(axis=2), 10, axis=0)),
                "p50": d.r(np.percentile(plan_oil.sum(axis=2), 50, axis=0)),
                "p90": d.r(np.percentile(plan_oil.sum(axis=2), 90, axis=0)),
            },
            "field_base": {
                "p10": d.r(np.percentile(base_oil.sum(axis=2), 10, axis=0)),
                "p50": d.r(np.percentile(base_oil.sum(axis=2), 50, axis=0)),
                "p90": d.r(np.percentile(base_oil.sum(axis=2), 90, axis=0)),
            },
            "plan_rates": d.r(r.plan.x[0]),
            "base_rates": d.r(r.base.x[0]),
            "n_members": len(r.forecasts_plan),
            "ramp_weekly": d.r(np.stack(rec.ramp.weekly)) if rec.ramp.weekly else [],
            "injector_efficiency": _injector_efficiency(g, params, r, d) if params is not None else None,
        }
    return {
        "id": s.sector.id,
        "key": s.key,
        "window_index": s.window_index,
        "dates": dates,
        "blind_start_index": min(split_idx, n_train) if n_train else split_idx,
        "injectors": injectors,
        "producers": producers,
        "well_types": {e: well_types.get(g.well_of_entity.get(e, e), "") for e in g.injectors + g.producers},
        "model": model,
        "leaderboard": _f(s.tournament.leaderboard),
        "eligibility": [
            {
                "variant": e.variant,
                "eligible": e.eligible,
                "available": e.available,
                "reason": e.reason,
                "suitability": e.suitability,
            }
            for e in s.tournament.eligibility
        ],
        "confidence": s.tournament.confidence,
        "confidence_reasons": s.tournament.confidence_reasons,
        "gates": s.gates,
        "profile": _f(prof.to_dict()),
        "dt_tau": dt_tau,
        "conditions": s.conditions.to_list(),
        "recommendation": display_recommendation(rec.to_dict(), d) if rec is not None else None,
        "forecast": fc,
        "distances": _f(g.distances()) if g.distances() is not None else None,
    }


def _injector_efficiency(g: Any, params: Any, r: Any, d: Display) -> dict[str, Any]:
    """Oil per unit injected (§14) at current vs optimised rates: Σ_j f_ij·f_o,j per injector."""
    fo_last = []
    for j in range(g.n_prod):
        tot = g.oil[-3:, j].sum() + g.water[-3:, j].sum()
        fo_last.append(float(g.oil[-3:, j].sum() / tot) if tot > 0 else 0.0)
    eff = params.f @ np.asarray(fo_last)
    return {
        "injectors": list(g.injectors),
        "oil_per_bbl": _f(eff),
        "current": d.r(r.base.x[0]),
        "optimized": d.r(r.plan.x[0]),
        "current_oil": d.r(eff * r.base.x[0]),
        "optimized_oil": d.r(eff * r.plan.x[0]),
    }


def build_bundle(
    run: RunResult,
    recs: Mapping[str, SectorRecommendation | None],
    cfg: Config,
    project_id: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    types = {w: t.type for w, t in run.well_types.items()}
    dq = _dq_report(run)
    d = Display(str(request.get("unit_system", "field")))
    return {
        "project_id": project_id,
        "units": d.units,
        "request": {k: v for k, v in request.items() if k != "economics"},
        "data_hash": run.data_hash,
        "config_hash": run.config_hash,
        "seed": run.seed,
        "runtime_s": run.runtime_s,
        "confidence": run.confidence,
        "windows": [
            {
                "start": run.grid.dates[w.start].isoformat(),
                "end": run.grid.dates[w.end - 1].isoformat(),
                "reason": w.reason,
            }
            for w in run.windows
        ],
        "pressure": {"field_source": run.pressure.field_source, "per_producer": run.pressure.source_per_producer},
        "well_types": {
            w: {
                "type": t.type,
                "conversions": [
                    {"date": c.date.isoformat(), "from": c.from_role, "to": c.to_role} for c in t.conversions
                ],
            }
            for w, t in run.well_types.items()
        },
        "conditions": run.conditions.to_list(),
        "data_quality": dq,
        "sectors": [
            sector_bundle(
                s,
                recs.get(s.sector.id) if s.window_index == max(x.window_index for x in run.sectors) else None,
                cfg,
                types,
                run.pressure.source_per_producer,
                d,
            )
            for s in run.sectors
        ],
        "latest_window_index": max((s.window_index for s in run.sectors), default=0),
    }


def _dq_report(run: RunResult) -> dict[str, Any]:
    """Data-quality report (§7): coverage per well, outliers, simultaneous P+I, unmatched IDs, traffic light."""
    codes = [c.code.value for c in run.conditions]
    outliers = sum(int(c.context.get("count", 0)) for c in run.conditions if c.code.value == "OUTLIERS_REMOVED")
    simult = sum(int(c.context.get("steps", 0)) for c in run.conditions if c.code.value == "SIMULTANEOUS_PI")
    unmatched = sum(int(c.context.get("count", 0)) for c in run.conditions if c.code.value == "UNMATCHED_WELL_IDS")
    g = run.grid
    coverage = {p: float((g.days_on_prod[:, j] > 0).mean()) for j, p in enumerate(g.producers)}
    coverage.update({i: float((g.days_on_inj[:, k] > 0).mean()) for k, i in enumerate(g.injectors)})
    warnings = len(run.conditions.warnings())
    errors = sum(1 for c in run.conditions if c.level.value == "error")
    light = "red" if errors or "HISTORY_TOO_SHORT" in codes else "amber" if warnings else "green"
    return {
        "traffic_light": light,
        "coverage": coverage,
        "outliers_removed": outliers,
        "simultaneous_pi_steps": simult,
        "unmatched_ids": unmatched,
        "n_warnings": warnings,
        "n_errors": errors,
        "n_steps": g.n_steps,
        "pressure_source": run.pressure.field_source,
    }


def display_recommendation(rec: dict[str, Any], d: Display) -> dict[str, Any]:
    """Recommendation payload with rates/volumes in display units (the stored payload stays internal)."""
    out = dict(rec)
    opt = dict(out.get("optimization", {}))
    for k in ("value_plan", "value_base"):
        if k in opt and opt[k] is not None:
            opt[k] = float(opt[k]) * d.volume
    if "rates" in opt:
        opt["rates"] = {w: [float(v) * d.rate for v in vals] for w, vals in opt["rates"].items()}
    if "base_rates" in opt:
        opt["base_rates"] = {w: float(v) * d.rate for w, v in opt["base_rates"].items()}
    out["optimization"] = opt
    tor = dict(out.get("tornado") or {})
    if tor.get("unit") == "volume":
        tor["base"] = float(tor.get("base", 0.0)) * d.volume
        tor["items"] = [
            {**i, "low": float(i["low"]) * d.volume, "high": float(i["high"]) * d.volume} for i in tor.get("items", [])
        ]
        tor["unit"] = d.units["volume"]
    elif tor:
        tor["unit"] = "USD"
    out["tornado"] = tor
    out["actions"] = [
        {
            **a,
            "rate_from": a["rate_from"] * d.rate,
            "rate_to": a["rate_to"] * d.rate,
            "step_this_week": a["step_this_week"] * d.rate,
            "expected_oil_gain": a["expected_oil_gain"] * d.volume,
            "setting_hint": re.sub(
                r"target [\d.]+", f"target {a['rate_to'] * d.rate:.0f}", str(a.get("setting_hint", ""))
            ),
        }
        for a in out.get("actions", [])
    ]
    for k in ("cum_oil_plan_p10_p50_p90", "cum_oil_base_p10_p50_p90"):
        if k in out:
            out[k] = [float(v) * d.volume for v in out[k]]
    out["units"] = d.units
    return out
