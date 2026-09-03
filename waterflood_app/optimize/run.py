"""Sector-level optimisation entry point: SectorRun → ensemble forecast models → plan → action list."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

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
    obj = (
        objective
        if isinstance(objective, Objective)
        else make_objective(objective, economics or Economics.from_config(cfg), target_oil)
    )
    current = models[0].current_injection(int(cfg.get("optimize.current_months", 1)))
    cons = constraints or PlanConstraints.from_config(current, cfg)
    res = optimize_plan(
        models, obj, cons, post, cfg, weights=weights, horizon_months=horizon_months, seed=seed, current=current
    )
    wells = [run.grid.well_of_entity.get(e, e) for e in run.grid.injectors]
    wc_now = {
        run.grid.well_of_entity.get(p, p): float(v)
        for p, v in zip(run.grid.producers, run.grid.water_cut[-3:].mean(axis=0), strict=True)
    }
    actions, rp = build_action_list(res, run.grid.injectors, conf, cfg, start or run.grid.dates[-1], facilities, wc_now)
    notes = list(res.notes)
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
    )
