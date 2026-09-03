"""Scenario manager — architecture §12.

Base (hold current) · Reallocation (same water) · Water-budget sweep (marginal value of water)
· Shut-in / convert / new well (§11 priors) · Pattern balancing (VRR targets). Every scenario
stores inputs, model version, objective, posture, results and delta-vs-base; scenarios are
compared on the same UQ ensemble so differences are not noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config
from waterflood_app.models.forecast import ForecastModel, fan, month_steps
from waterflood_app.models.mpi import conversion_prior, new_injector_prior
from waterflood_app.optimize.constraints import PlanConstraints
from waterflood_app.optimize.objectives import Objective
from waterflood_app.optimize.solvers import OptimizationResult, expand_plan, optimize_plan

FArray = npt.NDArray[np.float64]


@dataclass
class Scenario:
    name: str
    kind: str
    inputs: dict[str, Any]
    objective: str
    posture: str
    value: float  # posture statistic of the objective
    delta_vs_base: float
    delta_pct_vs_base: float
    result: OptimizationResult | None = None
    fan: dict[str, Any] = field(default_factory=dict)
    prior_based: bool = False  # §11: wells without history are labelled on every chart
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "inputs": self.inputs,
            "objective": self.objective,
            "posture": self.posture,
            "value": self.value,
            "delta_vs_base": self.delta_vs_base,
            "delta_pct_vs_base": self.delta_pct_vs_base,
            "prior_based": self.prior_based,
            "notes": self.notes,
            "fan": {k: v for k, v in self.fan.items() if not isinstance(v, np.ndarray)},
        }


class ScenarioManager:
    def __init__(
        self,
        models: list[ForecastModel],
        weights: list[float],
        objective: Objective,
        posture: str,
        cfg: Config,
        seed: int = 0,
        horizon_months: int | None = None,
    ) -> None:
        self.models, self.weights, self.objective, self.posture, self.cfg, self.seed = (
            models,
            weights,
            objective,
            posture,
            cfg,
            seed,
        )
        self.h = int(horizon_months or cfg["optimize.horizon_months"])
        self.current = models[0].current_injection(int(cfg.get("optimize.current_months", 1)))
        self.scenarios: list[Scenario] = []
        self._base_value: float | None = None

    # ---- helpers ------------------------------------------------------------------------
    def _evaluate(self, models: list[ForecastModel], x: FArray) -> tuple[float, dict[str, Any]]:
        from waterflood_app.optimize.posture import aggregate

        _, dt = month_steps(models[0].grid.dates[-1], self.h)
        inj = expand_plan(np.tile(x, (1, 1)), self.h)
        fcs = [m.simulate(inj, dt) for m in models]
        vals = np.array([self.objective.value(f) for f in fcs])
        return aggregate(vals, self.posture, self.cfg), fan(fcs)

    def _record(
        self,
        name: str,
        kind: str,
        inputs: dict[str, Any],
        value: float,
        fan_d: dict[str, Any],
        result: OptimizationResult | None = None,
        prior_based: bool = False,
        notes: list[str] | None = None,
    ) -> Scenario:
        base = self.base().value if kind != "base" else value
        sc = Scenario(
            name,
            kind,
            inputs,
            self.objective.name,
            self.posture,
            value,
            value - base,
            100.0 * (value - base) / abs(base) if base else 0.0,
            result,
            fan_d,
            prior_based,
            notes or [],
        )
        self.scenarios.append(sc)
        return sc

    # ---- scenarios ----------------------------------------------------------------------
    def base(self) -> Scenario:
        for s in self.scenarios:
            if s.kind == "base":
                return s
        v, fd = self._evaluate(self.models, self.current)
        return self._record("Base (hold current)", "base", {"rates": self.current.tolist()}, v, fd)

    def reallocation(self, constraints: PlanConstraints | None = None) -> Scenario:
        self.base()
        cons = constraints or PlanConstraints.from_config(self.current, self.cfg)
        res = optimize_plan(
            self.models,
            self.objective,
            cons,
            self.posture,
            self.cfg,
            self.weights,
            self.h,
            seed=self.seed,
            current=self.current,
        )
        return self._record(
            "Reallocation (same water)",
            "reallocation",
            {"total_water": cons.total_water},
            res.value_plan,
            fan(res.forecasts_plan),
            res,
        )

    def water_budget_sweep(self, fractions: tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2)) -> list[Scenario]:
        """Optimal plan at several total-water budgets → marginal value of water (§12)."""
        self.base()
        out: list[Scenario] = []
        total = float(self.current.sum())
        for fr in fractions:
            cons = PlanConstraints.from_config(self.current, self.cfg, total_water=total * fr)
            res = optimize_plan(
                self.models,
                self.objective,
                cons,
                self.posture,
                self.cfg,
                self.weights,
                self.h,
                seed=self.seed,
                current=self.current,
            )
            out.append(
                self._record(
                    f"Water budget {fr:.0%}",
                    "water_budget",
                    {"total_water": total * fr, "fraction": fr},
                    res.value_plan,
                    fan(res.forecasts_plan),
                    res,
                )
            )
        vals = np.array([s.value for s in out])
        waters = np.array([s.inputs["total_water"] for s in out])
        if len(out) > 1:
            marg = np.gradient(vals, waters)
            for s, m in zip(out, marg, strict=True):
                s.notes.append(f"marginal value of water ≈ {m:.4g} per unit rate")
        return out

    def shut_in_injector(self, injector: str) -> Scenario:
        """Direct: remove its f_ij column, keep the water elsewhere fixed (HIGH confidence, §11)."""
        self.base()
        k = self.models[0].injectors.index(injector)
        x = self.current.copy()
        x[k] = 0.0
        v, fd = self._evaluate(self.models, x)
        return self._record(
            f"Shut in {injector}",
            "shut_in",
            {"injector": injector, "rates": x.tolist()},
            v,
            fd,
            notes=["crossflow variant recommended if BHP exists: neighbours' pressure redistributes (§11)"],
        )

    def new_injector(self, name: str, xy: FArray, rate: float) -> Scenario:
        """MPI-scaled prior row for a new injector (MEDIUM at best); labelled prior-based (§11)."""
        self.base()
        base_models = self.models
        models: list[ForecastModel] = []
        for m in base_models:
            g = m.grid
            if g.xy_inj is None or g.xy_prod is None:
                raise ValueError("coordinates are required for a new-well scenario")
            row, _ = new_injector_prior(np.asarray(xy, dtype=np.float64), g.xy_inj, g.xy_prod, m.params.f)
            p = m.params.copy()
            p.f = np.vstack([p.f, row[None, :]])
            g2 = replace(
                g,
                injectors=[*g.injectors, name],
                inj=np.column_stack([g.inj, np.zeros(g.n_steps)]),
                days_on_inj=np.column_stack([g.days_on_inj, np.zeros(g.n_steps)]),
                xy_inj=np.vstack([g.xy_inj, np.asarray(xy)[None, :]]),
                raw={},
            )
            models.append(ForecastModel(m.variant, p, m.oilcut, g2, m.surface_ratio, label=m.label))
        x = np.concatenate([self.current, [rate]])
        v, fd = self._evaluate(models, x)
        return self._record(
            f"New injector {name}",
            "new_well",
            {"well": name, "xy": list(map(float, xy)), "rate": rate},
            v,
            fd,
            prior_based=True,
            notes=["prior-based: re-evaluated automatically once 12 months of data exist (§11)"],
        )

    def convert_producer(self, producer: str, rate: float) -> Scenario:
        """Producer → injector: prior f row to neighbours from the MPI-scaled prior (MEDIUM, §11)."""
        self.base()
        models: list[ForecastModel] = []
        for m in self.models:
            g = m.grid
            if g.xy_inj is None or g.xy_prod is None:
                raise ValueError("coordinates are required for a conversion scenario")
            j = g.producers.index(producer)
            row = conversion_prior(j, g.xy_inj, g.xy_prod, m.params.f)
            keep = [k for k in range(g.n_prod) if k != j]
            p = m.params.copy()
            p.f = np.vstack([p.f[:, keep], row[None, :]])
            p.tau = p.tau[keep] if p.tau.ndim == 1 else p.tau[:, keep]
            p.gain_p, p.tau_p = p.gain_p[keep], p.tau_p[keep]
            if p.J is not None:
                p.J = p.J[keep] if p.J.ndim == 1 else p.J[:, keep]
            g2 = g.subset(producers=[g.producers[k] for k in keep])
            g2 = replace(
                g2,
                injectors=[*g2.injectors, f"{producer}@I"],
                inj=np.column_stack([g2.inj, np.zeros(g2.n_steps)]),
                days_on_inj=np.column_stack([g2.days_on_inj, np.zeros(g2.n_steps)]),
                xy_inj=np.vstack([g2.xy_inj, g.xy_prod[j][None, :]]) if g2.xy_inj is not None else None,
                raw={},
            )
            oc = {k: v for k, v in m.oilcut.items() if k != producer}
            models.append(ForecastModel(m.variant, p, oc, g2, m.surface_ratio[keep], label=m.label))
        x = np.concatenate([self.current, [rate]])
        v, fd = self._evaluate(models, x)
        return self._record(
            f"Convert {producer} to injector",
            "conversion",
            {"producer": producer, "rate": rate},
            v,
            fd,
            prior_based=True,
            notes=["uses the well's producer-role drainage volume as the starting tank (§11)"],
        )

    def pattern_balancing(self, vrr_target: float, patterns: dict[str, list[str]] | None = None) -> Scenario:
        """Scale injection so that each pattern's (or the sector's) VRR hits the target (§12)."""
        self.base()
        m = self.models[0]
        liq = m.grid.liq[-3:].mean(axis=0)
        x = self.current.copy()
        if patterns:
            for _name, wells in patterns.items():
                ii = [m.injectors.index(w) for w in wells if w in m.injectors]
                jj = [m.producers.index(w) for w in wells if w in m.producers]
                if ii and jj:
                    target_inj = vrr_target * float(liq[jj].sum())
                    cur = float(x[ii].sum())
                    if cur > 0:
                        x[ii] *= target_inj / cur
        else:
            target_inj = vrr_target * float(liq.sum())
            x *= target_inj / max(float(x.sum()), 1e-9)
        v, fd = self._evaluate(self.models, x)
        return self._record(
            f"Pattern balancing VRR {vrr_target:.2f}",
            "pattern_balancing",
            {"vrr_target": vrr_target, "rates": x.tolist()},
            v,
            fd,
        )

    def compare(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.scenarios]
