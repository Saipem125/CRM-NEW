"""Plan optimisation — architecture §12 (SLSQP for smooth short horizons), §13 (ensemble postures).

Decision variables: injection rate per injector per period over the horizon (default one
period = constant reallocation). The objective is evaluated on every ensemble member and
collapsed with the posture statistic; the robust posture adds one "no loss versus hold-current"
inequality per member. Gradients are finite differences (few dozen variables at most per
sector); multi-start from hold-current, equal split and Dirichlet draws seeded from the run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

from waterflood_app.config import Config
from waterflood_app.models.forecast import Forecast, ForecastModel, month_steps
from waterflood_app.optimize.constraints import PlanConstraints
from waterflood_app.optimize.objectives import Objective
from waterflood_app.optimize.posture import aggregate, rejects_loss

FArray = npt.NDArray[np.float64]


@dataclass
class Plan:
    x: FArray  # (P, Ni) rates per period
    inj: FArray  # (H, Ni) expanded to steps
    dt_days: FArray  # (H,)

    @property
    def n_periods(self) -> int:
        return int(self.x.shape[0])


def expand_plan(x: FArray, horizon: int) -> FArray:
    """Piecewise-constant expansion of (P, Ni) to (H, Ni)."""
    p, ni = x.shape
    edges = np.linspace(0, horizon, p + 1).astype(int)
    out = np.zeros((horizon, ni))
    for k in range(p):
        out[edges[k] : edges[k + 1]] = x[k]
    return out


@dataclass
class OptimizationResult:
    plan: Plan
    base: Plan
    objective: str
    posture: str
    value_plan: float  # posture statistic for the plan
    value_base: float  # posture statistic for hold-current
    member_values_plan: FArray
    member_values_base: FArray
    equal_split_value: float
    gain_vs_base_pct: float
    gain_vs_equal_split_pct: float
    forecasts_plan: list[Forecast]
    forecasts_base: list[Forecast]
    converged: bool
    n_evals: int
    runtime_s: float
    notes: list[str] = field(default_factory=list)
    marginal_value: FArray = field(default_factory=lambda: np.zeros(0))  # ∂(posture objective)/∂x_i at the plan

    def to_dict(self, injectors: list[str]) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "posture": self.posture,
            "value_plan": self.value_plan,
            "value_base": self.value_base,
            "gain_vs_base_pct": self.gain_vs_base_pct,
            "gain_vs_equal_split_pct": self.gain_vs_equal_split_pct,
            "rates": {w: [float(v) for v in self.plan.x[:, k]] for k, w in enumerate(injectors)},
            "base_rates": {w: float(self.base.x[0, k]) for k, w in enumerate(injectors)},
            "marginal_value": {w: float(self.marginal_value[k]) for k, w in enumerate(injectors)}
            if len(self.marginal_value)
            else {},
            "converged": self.converged,
            "n_evals": self.n_evals,
            "runtime_s": self.runtime_s,
            "notes": self.notes,
        }


class _Evaluator:
    """Caches forecasts per decision vector and counts evaluations."""

    def __init__(
        self,
        models: list[ForecastModel],
        weights: FArray,
        objective: Objective,
        cons: PlanConstraints,
        dt: FArray,
        horizon: int,
        ni: int,
        n_periods: int,
    ) -> None:
        self.models, self.w, self.obj, self.cons, self.dt, self.h, self.ni, self.p = (
            models,
            weights,
            objective,
            cons,
            dt,
            horizon,
            ni,
            n_periods,
        )
        self.n_evals = 0
        self._cache: dict[bytes, tuple[FArray, list[Forecast]]] = {}

    def forecasts(self, x: FArray) -> tuple[FArray, list[Forecast]]:
        key = np.round(x, 9).tobytes()
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        inj = expand_plan(np.asarray(x, dtype=np.float64).reshape(self.p, self.ni), self.h)
        fcs = [m.simulate(inj, self.dt) for m in self.models]
        vals = np.array([self.obj.value(f) for f in fcs])
        self.n_evals += 1
        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[key] = (vals, fcs)
        return vals, fcs


def optimize_plan(
    models: list[ForecastModel],
    objective: Objective,
    constraints: PlanConstraints,
    posture: str,
    cfg: Config,
    weights: list[float] | None = None,
    horizon_months: int | None = None,
    n_periods: int | None = None,
    seed: int = 0,
    current: FArray | None = None,
) -> OptimizationResult:
    t0 = time.perf_counter()
    o = cfg.section("optimize")
    h = int(horizon_months or o["horizon_months"])
    p = int(n_periods or o["n_periods"])
    ref = models[0]
    ni = ref.grid.n_inj
    _, dt = month_steps(ref.grid.dates[-1], h)
    w = (
        np.ones(len(models)) / len(models)
        if weights is None
        else np.asarray(weights, dtype=np.float64) / float(np.sum(weights))
    )
    cur = (
        ref.current_injection(int(o.get("current_months", 1)))
        if current is None
        else np.asarray(current, dtype=np.float64)
    )
    ev = _Evaluator(models, w, objective, constraints, dt, h, ni, p)
    base_x = np.tile(cur, (p, 1))
    base_vals, base_fcs = ev.forecasts(base_x.ravel())
    equal_x = np.tile(np.full(ni, cur.sum() / ni), (p, 1))
    equal_vals, _ = ev.forecasts(equal_x.ravel())
    reject_loss = rejects_loss(posture, cfg)

    def stat(x: FArray) -> float:
        vals, _ = ev.forecasts(x)
        return aggregate(vals, posture, cfg)

    scale = max(abs(aggregate(base_vals, posture, cfg)), 1e-9)

    def neg(x: FArray) -> float:
        return -stat(x) / scale

    cons_list: list[dict[str, Any]] = []
    eq, _ineq = constraints.water_constraints(base_x.ravel(), p)
    if len(eq):
        cons_list.append(
            {
                "type": "eq",
                "fun": lambda x: constraints.water_constraints(x, p)[0] / max(constraints.total_water or 1.0, 1e-9),
            }
        )
    if constraints.total_water is not None and not constraints.same_water:
        cons_list.append(
            {
                "type": "ineq",
                "fun": lambda x: constraints.water_constraints(x, p)[1] / max(constraints.total_water or 1.0, 1e-9),
            }
        )

    k_ref = int(np.argmax(w))
    base_g = constraints.forecast_constraints(base_fcs[k_ref])
    relax = np.minimum(base_g, 0.0) if bool(o.get("relax_violated_at_base", True)) else np.zeros_like(base_g)
    notes0: list[str] = []
    if (relax < 0).any():
        notes0.append(
            f"{int((relax < 0).sum())} forecast constraint(s) already violated at hold-current "
            "were relaxed to that level"
        )

    def fc_cons(x: FArray) -> FArray:
        vals, fcs = ev.forecasts(x)
        # constraints on the reference (highest-weight) member forecast
        g = constraints.forecast_constraints(fcs[k_ref]) - relax
        extra = objective.extra_constraints(fcs[k_ref])
        parts = [g, extra]
        if reject_loss:
            parts.append((vals - base_vals) / scale)
        return np.concatenate(parts) if parts else np.zeros(0)

    probe = fc_cons(base_x.ravel())
    if len(probe):
        cons_list.append({"type": "ineq", "fun": fc_cons})
    bounds = constraints.bounds(p)
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    rng = np.random.default_rng(seed)
    starts = [base_x.ravel(), equal_x.ravel()]
    total = cur.sum()
    # greedy vertex start: fill injectors in order of marginal value at hold-current up to their bounds
    marg0 = np.zeros(ni)
    h0 = max(1e-3 * max(total / ni, 1.0), 1e-6)
    v0 = stat(base_x.ravel())
    for k in range(ni):
        xp = base_x.ravel().copy()
        xp[k] += h0
        marg0[k] = (stat(xp) - v0) / h0
    greedy = lo[:ni].copy()
    remaining = total - greedy.sum()
    for k in np.argsort(-marg0):
        add = min(hi[k] - greedy[k], max(remaining, 0.0))
        greedy[k] += add
        remaining -= add
    starts.append(np.tile(greedy, p))
    for _ in range(int(o["multistart"]) - 2):
        d = rng.dirichlet(np.ones(ni)) * total
        starts.append(np.tile(np.clip(d, lo[:ni], hi[:ni]), p))
    best_x, best_val, converged, notes = base_x.ravel(), stat(base_x.ravel()), False, list(notes0)
    for x0 in starts:
        x0 = np.clip(x0, lo, hi)
        try:
            res = minimize(
                neg,
                x0,
                method="SLSQP",
                bounds=bounds,
                constraints=cons_list,
                options={"maxiter": int(o["maxiter"]), "ftol": 1e-10},
            )
        except (ValueError, FloatingPointError) as exc:  # a start that breaks the forecast is simply skipped
            notes.append(f"start skipped: {exc}")
            continue
        x = np.clip(res.x, lo, hi)
        feasible = all(np.all(np.asarray(c["fun"](x)) >= -1e-6) for c in cons_list if c["type"] == "ineq") and all(
            np.all(np.abs(np.asarray(c["fun"](x))) <= 1e-6) for c in cons_list if c["type"] == "eq"
        )
        val = stat(x)
        if feasible and val > best_val + 1e-12:
            best_x, best_val, converged = x, val, bool(res.success)
    # polish the best point with a tighter tolerance
    try:
        res = minimize(
            neg,
            best_x,
            method="SLSQP",
            bounds=bounds,
            constraints=cons_list,
            options={"maxiter": 2 * int(o["maxiter"]), "ftol": 1e-13},
        )
        xr = np.clip(res.x, lo, hi)
        ok = all(np.all(np.asarray(c["fun"](xr)) >= -1e-6) for c in cons_list if c["type"] == "ineq") and all(
            np.all(np.abs(np.asarray(c["fun"](xr))) <= 1e-6) for c in cons_list if c["type"] == "eq"
        )
        if ok and stat(xr) > best_val:
            best_x, best_val, converged = xr, stat(xr), bool(res.success)
    except (ValueError, FloatingPointError):
        pass
    plan_vals, plan_fcs = ev.forecasts(best_x)
    if reject_loss and (plan_vals < base_vals - 1e-9).any():
        notes.append("robust posture: candidate lost oil in a realization — hold-current kept")
        best_x, plan_vals, plan_fcs, best_val = base_x.ravel(), base_vals, base_fcs, aggregate(base_vals, posture, cfg)
    # marginal value of water per injector at the plan (posture objective per unit rate)
    marg = np.zeros(ni)
    hstep = max(1e-3 * max(total / ni, 1.0), 1e-6)
    for k in range(ni):
        xp = best_x.copy()
        xp[k] += hstep
        marg[k] = (stat(xp) - best_val) / hstep
    vb = aggregate(base_vals, posture, cfg)
    ve = aggregate(equal_vals, posture, cfg)
    plan = Plan(best_x.reshape(p, ni), expand_plan(best_x.reshape(p, ni), h), dt)
    base = Plan(base_x, expand_plan(base_x, h), dt)
    return OptimizationResult(
        plan=plan,
        base=base,
        objective=objective.name,
        posture=posture,
        value_plan=best_val,
        value_base=vb,
        member_values_plan=plan_vals,
        member_values_base=base_vals,
        equal_split_value=ve,
        gain_vs_base_pct=100.0 * (best_val - vb) / abs(vb) if vb else 0.0,
        gain_vs_equal_split_pct=100.0 * (best_val - ve) / abs(ve) if ve else 0.0,
        forecasts_plan=plan_fcs,
        forecasts_base=base_fcs,
        converged=converged,
        n_evals=ev.n_evals,
        runtime_s=time.perf_counter() - t0,
        notes=notes,
        marginal_value=marg,
    )
