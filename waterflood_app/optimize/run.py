"""Sector-level optimisation entry point: SectorRun → ensemble forecast models → plan → action list."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np

from waterflood_app.config import Config
from waterflood_app.engine import SectorRun
from waterflood_app.models.forecast import ForecastModel, fan, surface_ratio
from waterflood_app.models.uq import select_members
from waterflood_app.optimize.action_list import ActionItem, build_action_list
from waterflood_app.optimize.constraints import PlanConstraints
from waterflood_app.optimize.economics import Economics
from waterflood_app.optimize.objectives import Objective, make_objective
from waterflood_app.optimize.posture import effective_posture
from waterflood_app.optimize.ramping import RampPlan
from waterflood_app.optimize.sensitivity import economic_tornado, oil_tornado
from waterflood_app.optimize.solvers import OptimizationResult, optimize_plan


def forecast_models(run: SectorRun, cfg: Config, max_members: int = 12) -> tuple[list[ForecastModel], list[float]]:
    """Ensemble of forecast models from the winner (or the score-weighted top models) of a sector run."""
    models: list[ForecastModel] = []
    weights: list[float] = []
    ratio = surface_ratio(run.grid)
    for entry, w_entry in run.tournament.ensemble:
        members = select_members(entry.fit, cfg)[:max_members]
        for k, p in enumerate(members):
            models.append(ForecastModel(entry.variant, p, run.oil_cut, run.grid, ratio, label=f"{entry.variant}#{k}"))
            weights.append(w_entry / len(members))
    if not models and run.tournament.winner is not None:
        e = run.tournament.winner
        models.append(ForecastModel(e.variant, e.fit.params, run.oil_cut, run.grid, ratio))
        weights.append(1.0)
    # §10: the forecast uses the latest window blended with the full-history fit by blind score
    roll = run.rolling
    if roll is not None and roll.blended is not None and roll.windows and run.tournament.winner is not None:
        last = roll.windows[-1].params
        wl = float(roll.weights.get("latest_window", 0.0))
        wf = float(roll.weights.get("full_history", 1.0))
        win_variant = run.tournament.winner.variant
        for m in models:
            if m.variant != win_variant or m.params.f.shape != last.f.shape:
                continue
            p = m.params.copy()
            p.f = wl * last.f + wf * p.f
            if p.tau.shape == last.tau.shape:
                p.tau = wl * last.tau + wf * p.tau
            p.extra["rolling_blend"] = {"latest_window": wl, "full_history": wf}
            m.params = p
    return models, weights


@dataclass
class SectorRecommendation:
    sector_id: str
    injectors: list[str]
    producers: list[str]
    result: OptimizationResult
    actions: list[ActionItem]
    ramp: RampPlan
    posture: str
    posture_note: str | None
    confidence: str
    fan_plan: dict[str, Any]
    fan_base: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    tornado: dict[str, Any] = field(default_factory=dict)  # §12 economic sensitivity (report tornado)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sector": self.sector_id,
            "confidence": self.confidence,
            "posture": self.posture,
            "posture_note": self.posture_note,
            "optimization": self.result.to_dict(self.injectors),
            "actions": [a.to_dict() for a in self.actions],
            "cum_oil_plan_p10_p50_p90": [
                self.fan_plan["cum_oil_p10"],
                self.fan_plan["cum_oil_p50"],
                self.fan_plan["cum_oil_p90"],
            ],
            "cum_oil_base_p10_p50_p90": [
                self.fan_base["cum_oil_p10"],
                self.fan_base["cum_oil_p50"],
                self.fan_base["cum_oil_p90"],
            ],
            "notes": self.notes,
            "tornado": self.tornado,
        }


def optimize_sector(
    run: SectorRun,
    cfg: Config,
    objective: str | Objective = "oil",
    posture: str | None = None,
    horizon_months: int | None = None,
    constraints: PlanConstraints | None = None,
    economics: Economics | None = None,
    target_oil: float | None = None,
    seed: int = 0,
    start: date | None = None,
    facilities: dict[str, str] | None = None,
) -> SectorRecommendation:
    models, weights = forecast_models(run, cfg)
    if not models:
        raise RuntimeError("no fitted model in this sector")
    conf = run.tournament.confidence
    post, note = effective_posture(posture, conf, cfg)
    econ = economics or Economics.from_config(cfg)
    obj = objective if isinstance(objective, Objective) else make_objective(objective, econ, target_oil)
    current = models[0].current_injection(int(cfg.get("optimize.current_months", 1)))
    cons = constraints or PlanConstraints.from_config(current, cfg)
    res = optimize_plan(
        models, obj, cons, post, cfg, weights=weights, horizon_months=horizon_months, seed=seed, current=current
    )
    if run.tournament.winner is not None and run.tournament.winner.variant == "crmt":
        # A field tank cannot tell injectors apart: any split of the same water gives the same forecast
        # apart from oil-cut nonlinearity, so a "reallocation gain" would be an artefact (first field data).
        res = replace(
            res,
            plan=res.base,
            value_plan=res.value_base,
            member_values_plan=res.member_values_base,
            forecasts_plan=res.forecasts_base,
            gain_vs_base_pct=0.0,
            gain_vs_equal_split_pct=0.0,
            marginal_value=np.zeros_like(res.marginal_value),
            notes=[
                *res.notes,
                "field-tank model (CRMT) won: injectors are indistinguishable, no reallocation is recommended — "
                "hold current rates; get pressure data or fit a smaller group for a per-injector model",
            ],
        )
    wells = [run.grid.well_of_entity.get(e, e) for e in run.grid.injectors]
    wc_now = {
        run.grid.well_of_entity.get(p, p): float(v)
        for p, v in zip(run.grid.producers, run.grid.water_cut[-3:].mean(axis=0), strict=True)
    }
    actions, rp = build_action_list(res, run.grid.injectors, conf, cfg, start or run.grid.dates[-1], facilities, wc_now)
    notes = list(res.notes)
    tornado = (
        economic_tornado(econ, res.forecasts_plan, res.forecasts_base, post, cfg, weights)
        if obj.unit == "currency"
        else oil_tornado(res.forecasts_plan, res.forecasts_base, post, cfg)
    )
    if conf == "LOW":
        notes.append("screening only — LOW confidence, cannot be approved without reviewer override")
    return SectorRecommendation(
        sector_id=run.sector.id,
        injectors=list(run.grid.injectors),
        producers=list(run.grid.producers),
        result=res,
        actions=actions,
        ramp=rp,
        posture=post,
        posture_note=note,
        confidence=conf,
        fan_plan=fan(res.forecasts_plan),
        fan_base=fan(res.forecasts_base),
        notes=notes + ([f"wells: {', '.join(wells)}"] if wells != list(run.grid.injectors) else []),
        tornado=tornado,
    )


def optimize_sectors(
    runs: list[SectorRun],
    cfg: Config,
    objective: str = "oil",
    posture: str | None = None,
    horizon_months: int | None = None,
    economics: Economics | None = None,
    target_oil: float | None = None,
    seed: int = 0,
) -> tuple[dict[str, SectorRecommendation], dict[str, str]]:
    """Optimize every sector, in parallel processes when there are several (large fields).

    Returns the recommendations by sector id and, separately, the plain error text of sectors whose
    optimisation failed (a failed sector never blocks the others).
    """
    import os

    from joblib import Parallel, delayed

    def one(run: SectorRun) -> tuple[str, SectorRecommendation | None, str | None]:
        try:
            return (
                run.sector.id,
                optimize_sector(
                    run, cfg, objective, posture, horizon_months, economics=economics, target_oil=target_oil, seed=seed
                ),
                None,
            )
        except Exception as exc:
            return run.sector.id, None, f"{type(exc).__name__}: {exc}"

    # Sequential by default: with the forecast continuing from the cached history state a sector
    # optimises in seconds, and a loky pool per sector was measured slower (pickling + cold caches).
    if bool(cfg.get("solver.sector_parallel", False)) and len(runs) > 1:
        n_jobs = int(cfg.get("solver.n_jobs", -1) or -1)
        n_workers = min(len(runs), (os.cpu_count() or 1) if n_jobs < 0 else n_jobs)
        inner = cfg.with_overrides({"solver": {"n_jobs": 1}})
        results = Parallel(n_jobs=n_workers, prefer="processes")(
            delayed(_optimize_one)(r, inner, objective, posture, horizon_months, economics, target_oil, seed)
            for r in runs
        )
    else:
        results = [_optimize_one(r, cfg, objective, posture, horizon_months, economics, target_oil, seed) for r in runs]
    recs: dict[str, SectorRecommendation] = {}
    errors: dict[str, str] = {}
    for sid, rec, err in results:
        if rec is not None:
            recs[sid] = rec
        if err is not None:
            errors[sid] = err
    return recs, errors


def _optimize_one(
    run: SectorRun,
    cfg: Config,
    objective: str,
    posture: str | None,
    horizon_months: int | None,
    economics: Economics | None,
    target_oil: float | None,
    seed: int,
) -> tuple[str, SectorRecommendation | None, str | None]:
    try:
        return (
            run.sector.id,
            optimize_sector(
                run, cfg, objective, posture, horizon_months, economics=economics, target_oil=target_oil, seed=seed
            ),
            None,
        )
    except Exception as exc:
        return run.sector.id, None, f"{type(exc).__name__}: {exc}"
