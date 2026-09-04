"""Milestone 5 — the forecast continues from the cached history state and must equal a full re-simulation."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from waterflood_app.models.base import ModelParams
from waterflood_app.models.forecast import ForecastModel, month_steps
from waterflood_app.models.solver import predict_field
from waterflood_app.prep.grid import Grid


def _grid(rng: np.random.Generator, with_bhp: bool) -> Grid:
    from datetime import date

    m, ni, npd = 60, 3, 4
    dates, dt = month_steps(date(2019, 12, 1), m)
    dt[0] = 30.0
    inj = rng.uniform(300, 900, size=(m, ni))
    liq = rng.uniform(200, 600, size=(m, npd))
    bhp = rng.uniform(9000, 11000, size=(m, npd)) if with_bhp else None
    return Grid(
        dates=dates,
        time_days=np.cumsum(dt) - dt[0],
        dt_days=dt,
        injectors=[f"I-{k}" for k in range(ni)],
        producers=[f"P-{k}" for k in range(npd)],
        inj=inj,
        liq=liq,
        oil=liq * 0.2,
        water=liq * 0.8,
        days_on_prod=np.tile(dt[:, None], (1, npd)),
        days_on_inj=np.tile(dt[:, None], (1, ni)),
        bhp=bhp,
    )


def _slow(model: ForecastModel, plan: np.ndarray, dt: np.ndarray, bhp_future: np.ndarray | None) -> np.ndarray:
    g = model.grid
    h = plan.shape[0]
    t_future = g.time_days[-1] + np.cumsum(dt)
    bhp = None
    if g.bhp is not None:
        fut = np.tile(g.bhp[-1], (h, 1)) if bhp_future is None else bhp_future
        bhp = np.vstack([g.bhp, fut])
    ext = replace(
        g,
        dates=g.dates + [g.dates[-1]] * h,
        time_days=np.concatenate([g.time_days, t_future]),
        dt_days=np.concatenate([g.dt_days, dt]),
        inj=np.vstack([g.inj, plan]),
        liq=np.vstack([g.liq, np.zeros((h, g.n_prod))]),
        oil=np.vstack([g.oil, np.zeros((h, g.n_prod))]),
        water=np.vstack([g.water, np.zeros((h, g.n_prod))]),
        days_on_prod=np.vstack([g.days_on_prod, np.full((h, g.n_prod), dt.mean())]),
        days_on_inj=np.vstack([g.days_on_inj, np.full((h, g.n_inj), dt.mean())]),
        bhp=bhp,
        raw={},
    )
    return np.asarray(predict_field(ext, model.params, model.variant)[-h:])


@pytest.mark.parametrize("variant", ["crmp", "crmip"])
@pytest.mark.parametrize("with_bhp", [False, True])
@pytest.mark.parametrize("future_bhp", [False, True])
def test_fast_continuation_matches_full_resimulation(variant: str, with_bhp: bool, future_bhp: bool) -> None:
    rng = np.random.default_rng(7)
    g = _grid(rng, with_bhp)
    ni, npd = g.n_inj, g.n_prod
    f = rng.uniform(0.05, 0.4, size=(ni, npd))
    if variant == "crmip":
        tau = rng.uniform(40, 300, size=(ni, npd))
        J = rng.uniform(0.01, 0.05, size=(ni, npd)) if with_bhp else None
    else:
        tau = rng.uniform(40, 300, size=npd)
        J = rng.uniform(0.01, 0.05, size=npd) if with_bhp else None
    params = ModelParams(f, tau, J, rng.uniform(0.5, 1.5, size=npd), rng.uniform(100, 800, size=npd))
    model = ForecastModel(variant, params, {}, g, np.ones(npd))
    h = 18
    _, dt = month_steps(g.dates[-1], h)
    plan = g.inj[-1] * rng.uniform(0.3, 1.7, size=(h, ni))
    bhp_f = (g.bhp[-1] + rng.normal(0, 200, size=(h, npd))) if (g.bhp is not None and future_bhp) else None
    fast = model._continue(plan, dt, bhp_f)
    slow = _slow(model, plan, dt, bhp_f)
    assert np.allclose(fast, slow, rtol=1e-10, atol=1e-8), np.abs(fast - slow).max()
    # the cached state is reused: a second plan is still exact
    plan2 = plan * 0.6
    assert np.allclose(model._continue(plan2, dt, bhp_f), _slow(model, plan2, dt, bhp_f), rtol=1e-10, atol=1e-8)
    fc = model.simulate(plan, dt, bhp_f)
    assert fc.liq_res.shape == (h, npd) and np.all(fc.liq_res >= 0)
