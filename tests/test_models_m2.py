"""Milestone 2 models: CRMPA on the aquifer fixture, MPI prior, rolling/CUSUM, two-phase and crossflow."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from waterflood_app.config import load_config
from waterflood_app.engine import run_engine
from waterflood_app.ingest.connectors import LoadedData
from waterflood_app.ingest.units import PVT
from waterflood_app.ingest.welltype import derive_well_types, split_roles
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.models.aquifer import CRMPA, influx_series, static_pressure_on_grid
from waterflood_app.models.base import FitData
from waterflood_app.models.change_detect import cusum, detect_shifts
from waterflood_app.models.crossflow import CrossflowCRM, CrossflowParams, crossflow_forward
from waterflood_app.models.mpi import infill_split, mpi_matrix, new_injector_prior, prior_allocation
from waterflood_app.models.rolling import RollingResult, WindowFit, fit_rolling, window_bounds
from waterflood_app.models.twophase import RelPerm, TwoPhaseCRM, twophase_forward
from waterflood_app.models.verify import r2
from waterflood_app.prep.grid import Grid, build_grid
from waterflood_app.prep.split import Split, train_blind_split
from waterflood_app.validation import synthetic_suite as suite

CFG = load_config()


def _grid(name: str) -> tuple[suite.Case, Grid]:
    case = suite.load_case(name)
    rates = case.rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas"))
    return case, build_grid(
        split_roles(rates, derive_well_types(rates)), PVT(), case.coords.rename({"well_id": "well"})
    )


# ---------------------------------------------------------------------------------------- aquifer
def test_influx_series_is_exact_exponential_without_forcing() -> None:
    dt = np.full(50, 30.0)
    we = influx_series(1000.0, 1e-3, 0.0, np.zeros(50), dt)
    assert np.allclose(we, 1000.0 * np.exp(-1e-3 * np.cumsum(dt) + 1e-3 * dt[0]))


def test_crmpa_recovers_m0_aquifer_truth() -> None:
    case, g = _grid("aquifer_6x9")
    t = case.truth["aquifer"]
    assert case.pressure is not None
    sp = static_pressure_on_grid(g, case.pressure.rename({"well_id": "well", "p_static_psi": "bhp"}))
    data = FitData(g, train_blind_split(g.n_steps, CFG))
    m = CRMPA(CFG, sp)
    res = m.fit(data, seed=0)
    assert m.terms is not None
    assert abs(m.terms.influx[0] - t["influx_bbl_d_first"]) / t["influx_bbl_d_first"] < 0.3
    assert abs(m.terms.influx[-1] - t["influx_bbl_d_last"]) / t["influx_bbl_d_last"] < 0.3
    alloc = dict(zip(g.producers, m.terms.allocation, strict=True))
    for w, v in t["f_aq"].items():
        assert abs(alloc[w] - v) < 0.05, (w, alloc[w], v)
    f_true = case.f_matrix(g.injectors, g.producers)
    big = f_true >= 0.05
    assert (np.abs(res.params.f - f_true)[big] / f_true[big]).max() < 0.15
    blind = np.zeros(g.n_steps, dtype=bool)
    blind[data.split.blind] = True
    assert r2(g.liq, res.prediction, g.prod_mask & blind[:, None]) > 0.95
    assert m.terms.pv_resolvable and m.terms.ct_v_r is not None and 0.4 < m.terms.ct_v_r / t["ct_V_r_bbl_psi"] < 2.5


def test_tournament_selects_aquifer_variant_on_aquifer_fixture() -> None:
    case = suite.load_case("aquifer_6x9")
    rates = case.rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas"))
    assert case.pressure is not None
    pressure = case.pressure.rename({"well_id": "well", "p_static_psi": "bhp"}).with_columns(
        pl.lit("static").alias("src")
    )
    loaded = LoadedData(
        rates=rates,
        coords=case.coords.rename({"well_id": "well"}),
        category=case.category.rename({"well_id": "well"}),
        pressure=pressure,
    )
    run = run_engine(loaded, CFG, PVT(), seed=0)
    s = run.latest()[0]
    assert s.tournament.winner is not None
    assert s.tournament.winner.variant == "aquifer", s.tournament.leaderboard
    assert s.conditions.has(ConditionCode.SUM_F_HIGH)
    assert not any(
        c.code == ConditionCode.VARIANT_NOT_AVAILABLE and "Aquifer" in c.context.get("variant", "")
        for c in s.conditions
    )
    assert s.tournament.confidence in ("HIGH", "MEDIUM")


# ------------------------------------------------------------------------------------------- MPI
def test_mpi_matrix_is_symmetric_and_distance_decaying() -> None:
    xy = np.array([[0.0, 0.0], [1000.0, 0.0], [3000.0, 0.0], [0.0, 2000.0]])
    res = mpi_matrix(xy, ["a", "b", "c", "d"])
    assert np.allclose(res.influence, res.influence.T, rtol=1e-6)
    assert res.influence[0, 1] > res.influence[0, 2]  # closer well → larger influence
    assert np.all(np.diag(res.influence) > 0)
    f = prior_allocation(xy[:2], xy[2:])
    assert f.shape == (2, 2) and np.allclose(f.sum(axis=1), 1.0)


def test_new_injector_and_infill_priors_preserve_rows() -> None:
    case, g = _grid("streak_5x4")
    f = case.f_matrix(g.injectors, g.producers)
    assert g.xy_inj is not None and g.xy_prod is not None
    row, d = new_injector_prior(np.array([3000.0, 1000.0]), g.xy_inj, g.xy_prod, f)
    assert row.shape == (4,) and abs(row.sum() - np.median(f.sum(axis=1))) < 1e-9 and d > 0
    new_col, adjusted = infill_split(np.array([2000.0, 2000.0]), g.xy_inj, g.xy_prod, f)
    assert np.allclose(adjusted.sum(axis=1) + new_col, f.sum(axis=1))
    assert (new_col > 0).all()


# --------------------------------------------------------------------------------- rolling / CUSUM
def test_window_bounds_respect_breaks() -> None:
    wb = window_bounds(120, CFG)
    assert wb[0] == (0, 36) and wb[1] == (6, 42) and wb[-1][1] == 120
    wb2 = window_bounds(96, CFG, breaks=[48])
    assert all(not (a < 48 < b) for a, b in wb2)


def test_rolling_fit_and_blend_on_streak() -> None:
    _, g = _grid("streak_5x4")
    full = suite.load_case("streak_5x4")
    from waterflood_app.models.crmp import CRMP

    data = FitData(g, train_blind_split(g.n_steps, CFG))
    res = CRMP(CFG).fit(data, seed=0, n_starts=2)
    roll = fit_rolling(
        g,
        CFG.with_overrides({"rolling": {"window_months": 60, "step_months": 30}}),
        seed=0,
        full=res.params,
        full_blind_r2=0.99,
    )
    assert len(roll.windows) >= 2 and roll.blended is not None
    f_true = full.f_matrix(g.injectors, g.producers)
    assert abs(roll.blended.f[2, 2] - f_true[2, 2]) < 0.1  # streak pair stable through the windows
    assert roll.weights["latest_window"] + roll.weights["full_history"] == pytest.approx(1.0)
    log = ConditionLog()
    assert detect_shifts(roll, CFG, log) == [] and not log.has(ConditionCode.CUSUM_SHIFT)


def test_cusum_detects_a_persistent_shift() -> None:
    up, dn = cusum(np.array([1.0, 1.0, 1.0, 1.6, 1.7, 1.8]), scale=0.1)
    assert up[-1] > 2.0 and dn[-1] == 0.0
    # a synthetic rolling result with f doubling on one pair after window 3
    wins = []
    for k in range(6):
        f = np.array([[0.5, 0.2], [0.3, 0.4]])
        if k >= 3:
            f[0, 0] = 0.9
        from waterflood_app.models.base import ModelParams

        p = ModelParams(f=f, tau=np.array([100.0, 120.0]), J=None, gain_p=np.ones(2), tau_p=np.array([100.0, 120.0]))
        wins.append(WindowFit(k * 6, k * 6 + 36, date(2015 + k, 1, 1), date(2018 + k, 1, 1), p, 0.9, 0.05, 0.05))
    roll = RollingResult(wins, ["I-1", "I-2"], ["P-1", "P-2"])
    log = ConditionLog()
    shifts = detect_shifts(roll, CFG, log)
    assert any(s.injector == "I-1" and s.producer == "P-1" and s.direction == "strengthened" for s in shifts)
    assert log.has(ConditionCode.CUSUM_SHIFT)
    msg = next(c for c in log if c.code == ConditionCode.CUSUM_SHIFT).message
    assert "I-1" in msg and "P-1" in msg and "strengthened" in msg


# ------------------------------------------------------------------------------- two-phase / crossflow
def test_twophase_forward_and_fit_recover_synthetic() -> None:
    rp = RelPerm()
    rng = np.random.default_rng(0)
    m, ni = 96, 2
    dt = np.full(m, 30.0)
    inj = np.column_stack([800 + 200 * np.sign(np.sin(np.arange(m) / 6 + k)) for k in range(ni)]) + rng.normal(
        0, 3, (m, ni)
    )
    f = np.array([0.6, 0.35])
    q, fw, sw = twophase_forward(f, 120.0, 4.0e5, 0.3, inj, dt, 700.0, rp)
    assert sw[m // 2] > sw[0] and fw[-1] > fw[0]  # saturation and water cut rise as the flood matures
    grid = Grid(
        dates=[date(2010, 1, 1)] * m,
        time_days=np.concatenate([[0.0], np.cumsum(dt[1:])]),
        dt_days=dt,
        injectors=["A", "B"],
        producers=["P"],
        inj=inj,
        liq=q[:, None],
        oil=(q * (1 - fw))[:, None],
        water=(q * fw)[:, None],
        days_on_prod=np.full((m, 1), 30.0),
        days_on_inj=np.full((m, ni), 30.0),
    )
    model = TwoPhaseCRM(CFG, rp)
    res = model.fit(FitData(grid, Split(80, m)), seed=0, n_starts=2)
    assert np.allclose(res.params.f[:, 0], f, atol=0.05)
    assert abs(res.params.extra["sw0"][0] - 0.3) < 0.05
    pred_fw = res.params.extra["water_cut_pred"][:, 0]
    assert np.abs(pred_fw - fw).max() < 0.05
    assert res.prediction.shape == (m, 1) and r2(q, res.prediction[:, 0], np.ones(m, dtype=bool)) > 0.95


def test_crossflow_forward_conserves_and_fit_runs() -> None:
    m, npd = 60, 2
    dt = np.full(m, 30.0)
    inj = np.full((m, 1), 1000.0)
    bhp = np.column_stack([np.full(m, 1500.0), np.full(m, 1500.0)])
    bhp[30:, 1] -= 200.0  # a choke change on the second producer
    cp = CrossflowParams(
        ct_v=np.array([2.0e4, 2.0e4]),
        J=np.array([1.0, 1.0]),
        p0=np.array([2000.0, 2000.0]),
        X=np.array([[0.0, 0.5], [0.5, 0.0]]),
        f=np.array([[0.5, 0.5]]),
    )
    q, _p = crossflow_forward(cp, inj, bhp, dt)
    assert q.shape == (m, npd) and (q >= 0).all()
    assert q[-1, 1] > q[29, 1]  # the lower BHP producer draws more
    assert q[-1, 0] < q[29, 0]  # ... and its neighbour loses some through crossflow
    grid = Grid(
        dates=[date(2010, 1, 1)] * m,
        time_days=np.concatenate([[0.0], np.cumsum(dt[1:])]),
        dt_days=dt,
        injectors=["I"],
        producers=["P1", "P2"],
        inj=inj,
        liq=q,
        oil=q * 0.3,
        water=q * 0.7,
        days_on_prod=np.full((m, npd), 30.0),
        days_on_inj=np.full((m, 1), 30.0),
        bhp=bhp,
        xy_inj=np.array([[0.0, 0.0]]),
        xy_prod=np.array([[500.0, 0.0], [-500.0, 0.0]]),
    )
    model = CrossflowCRM(CFG)
    res = model.fit(FitData(grid, Split(48, m)), seed=0)
    assert r2(q, res.prediction, np.ones((m, npd), dtype=bool)) > 0.9
    assert res.params.extra["X"][0, 1] > 0
