"""§9 models: analytic step response, gradient correctness, in-house vs pywaterflood (≤ 1 %), oil cut."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from waterflood_app.config import load_config
from waterflood_app.ingest.units import PVT
from waterflood_app.ingest.welltype import derive_well_types, split_roles
from waterflood_app.models.base import FitData
from waterflood_app.models.crmip import CRMIP
from waterflood_app.models.crmp import CRMP
from waterflood_app.models.crmt import CRMT
from waterflood_app.models.fractional_flow import (
    KovalFractionalFlow,
    PowerLawOilCut,
    fit_koval,
    fit_power_law,
)
from waterflood_app.models.solver import (
    ProducerData,
    SolverSettings,
    crmip_forward,
    crmp_forward,
    fit_field,
    make_forward,
    predict_field,
)
from waterflood_app.models.verify import aicc, r2
from waterflood_app.prep.grid import Grid, build_grid
from waterflood_app.prep.split import Split, train_blind_split
from waterflood_app.validation import synthetic_suite as suite

CFG = load_config()
CFG_FREE = CFG.with_overrides({"solver": {"free_primary": True}})  # pywaterflood's model form


def _grid(name: str, with_xy: bool = True) -> tuple[suite.Case, Grid]:
    case = suite.load_case(name)
    rates = case.rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas"))
    types = derive_well_types(rates)
    coords = case.coords.rename({"well_id": "well"}) if with_xy else None
    return case, build_grid(split_roles(rates, types), PVT(), coords)


def test_single_tank_step_response_is_analytic() -> None:
    """Engineering rule: q(t) = q₀e^{-t/τ} + i(1 − e^{-t/τ}) for a step injection at t=0⁺."""
    m, tau, q0, i0 = 60, 90.0, 500.0, 800.0
    dt = np.full(m, 15.0)
    t = np.concatenate([[0.0], np.cumsum(dt[1:])])
    inj = np.full((m, 1), i0)
    d = ProducerData(inj=inj, q=np.zeros(m), w=np.ones(m), t=t, dt=dt, dpdt=None, q0=q0, n_train=m)
    theta = np.array([1.0, tau, 1.0, tau])  # f=1, τ, g_p=1, τ_p=τ
    qhat, _ = crmp_forward(theta, d, need_grad=False)
    expected = q0 * np.exp(-t / tau) + i0 * (1.0 - np.exp(-t / tau))
    assert np.allclose(qhat, expected, rtol=1e-12)
    # CRMIP with one pair is the same model
    qhat2, _ = crmip_forward(np.array([1.0, tau, 1.0, tau]), d, need_grad=False)
    assert np.allclose(qhat2, expected, rtol=1e-12)


@pytest.mark.parametrize("variant", ["crmp", "crmip"])
def test_analytic_gradients_match_finite_differences(variant: str) -> None:
    rng = np.random.default_rng(1)
    m, ni = 40, 3
    dt = rng.uniform(28, 31, m)
    t = np.concatenate([[0.0], np.cumsum(dt[1:])])
    inj = rng.uniform(500, 1500, (m, ni))
    bhp = 1500 - 3 * np.arange(m) + 20 * np.sin(np.arange(m) / 4)
    dpdt = np.concatenate([[0.0], np.diff(bhp) / dt[1:]])
    d = ProducerData(inj=inj, q=np.zeros(m), w=np.ones(m), t=t, dt=dt, dpdt=dpdt, q0=900.0, n_train=m)
    if variant == "crmp":
        theta = np.array([0.3, 0.5, 0.2, 120.0, 0.9, 200.0, 2.0])
    else:
        theta = np.array([0.3, 0.5, 0.2, 90.0, 150.0, 200.0, 0.9, 200.0, 1.0, 2.0, 0.5])
    fwd = make_forward(variant, tied=False)
    _q, jac = fwd(theta, d, True)
    assert jac is not None
    for k in range(len(theta)):
        h = 1e-6 * max(1.0, abs(theta[k]))
        tp, tm = theta.copy(), theta.copy()
        tp[k] += h
        tm[k] -= h
        num = (fwd(tp, d, False)[0] - fwd(tm, d, False)[0]) / (2 * h)
        assert np.allclose(jac[:, k], num, rtol=1e-5, atol=1e-6 * max(1.0, np.abs(num).max())), f"param {k}"


def test_inhouse_crmp_recovers_streak_truth_and_matches_pywaterflood() -> None:
    case, g = _grid("streak_5x4")
    data = FitData(g, train_blind_split(g.n_steps, CFG))
    inhouse = CRMP(CFG_FREE, "inhouse").fit(data, seed=0)
    f_true, tau_true = case.f_matrix(), case.tau_vector()
    big = f_true >= 0.05
    rel = np.abs(inhouse.params.f - f_true)[big] / f_true[big]
    assert rel.max() < 0.02, rel
    assert np.abs(inhouse.params.f - f_true)[~big].max() < 0.01
    assert (np.abs(inhouse.params.tau - tau_true) / tau_true).max() < 0.05
    base = CRMP(CFG, "pywaterflood").fit(data)
    # in-house vs pywaterflood within 1 % on f_ij (large pairs), τ and blind R²
    rel_f = np.abs(inhouse.params.f - base.params.f)[big] / np.maximum(base.params.f[big], 1e-9)
    assert rel_f.max() < 0.01, rel_f
    assert (np.abs(inhouse.params.tau - base.params.tau) / base.params.tau).max() < 0.01
    mask = g.prod_mask
    blind = np.zeros(g.n_steps, dtype=bool)
    blind[data.split.blind] = True
    r_in = r2(g.liq, inhouse.prediction, mask & blind[:, None])
    r_py = r2(g.liq, base.prediction, mask & blind[:, None])
    assert r_in > 0.99 and abs(r_in - r_py) < 0.01


def test_inhouse_vs_pywaterflood_on_noisy_within_one_percent_r2() -> None:
    _, g = _grid("streak_5x4_noise5")
    data = FitData(g, train_blind_split(g.n_steps, CFG))
    inhouse = CRMP(CFG_FREE, "inhouse").fit(data, seed=0)
    base = CRMP(CFG, "pywaterflood").fit(data)
    mask = g.prod_mask
    blind = np.zeros(g.n_steps, dtype=bool)
    blind[data.split.blind] = True
    r_in = r2(g.liq, inhouse.prediction, mask & blind[:, None])
    r_py = r2(g.liq, base.prediction, mask & blind[:, None])
    assert r_in >= r_py - 0.01, (r_in, r_py)
    assert r_in >= 0.75


def test_crmip_reduces_to_crmp_truth_on_streak() -> None:
    case, g = _grid("streak_5x4")
    data = FitData(g, train_blind_split(g.n_steps, CFG))
    res = CRMIP(CFG).fit(data, seed=0)
    f_true = case.f_matrix()
    big = f_true >= 0.05
    assert (np.abs(res.params.f - f_true)[big] / f_true[big]).max() < 0.05
    assert res.params.tau.shape == (5, 4)


def test_crmt_field_tank_and_tau_estimate() -> None:
    _case, g = _grid("streak_5x4")
    data = FitData(g, train_blind_split(g.n_steps, CFG))
    m = CRMT(CFG)
    res = m.fit(data, seed=0, n_starts=3)
    assert 60.0 < m.tau_field < 400.0  # true τ range 95–180 d
    field_pred = res.prediction.sum(axis=1)
    field_obs = g.liq.sum(axis=1)
    assert r2(field_obs, field_pred, np.ones(g.n_steps, dtype=bool)) > 0.95


def test_joint_refinement_enforces_injector_sum() -> None:
    """A producer-only fit can give Σ_j f_ij > 1; the joint stage must bring it to ≤ 1."""
    _, g = _grid("streak_5x4")
    g2 = g.subset(injectors=["I-3"])  # one injector feeding four producers → Σ_j f_3j would exceed 1
    data = FitData(g2, Split(96, 120))
    s = SolverSettings.from_config(CFG, 28.0)
    s.n_starts = 3
    res = fit_field(data, "crmp", s, seed=0)
    assert res.params.sum_f_per_injector.max() <= 1.0 + 1e-6
    assert predict_field(g2, res.params, "crmp").shape == (120, 4)


def test_bhp_term_is_identified() -> None:
    """Synthetic producer with a known J·dp_wf/dt term; the fit must recover f, τ and J."""
    rng = np.random.default_rng(3)
    m, ni = 120, 2
    dt = np.full(m, 30.0)
    t = np.concatenate([[0.0], np.cumsum(dt[1:])])
    inj = np.column_stack([1000 + 300 * np.sign(np.sin(np.arange(m) / 7 + k)) for k in range(ni)]) + rng.normal(
        0, 5, (m, ni)
    )
    bhp = 1500 - 2 * np.arange(m) + 60 * (np.arange(m) > 50) - 40 * (np.arange(m) > 90)
    dpdt = np.concatenate([[0.0], np.diff(bhp) / dt[1:]])
    d0 = ProducerData(inj=inj, q=np.zeros(m), w=np.ones(m), t=t, dt=dt, dpdt=dpdt, q0=0.0, n_train=m)
    true = np.array([0.6, 0.3, 120.0, 1.0, 120.0, 4.0])
    q, _ = crmp_forward(true, d0, False)
    q0 = q[0]
    q = q + 300 * np.exp(-t / 120.0)  # a primary term consistent with g_p·q(0)
    grid = Grid(
        dates=[],
        time_days=t,
        dt_days=dt,
        injectors=["A", "B"],
        producers=["P"],
        inj=inj,
        liq=q[:, None],
        oil=q[:, None],
        water=np.zeros((m, 1)),
        days_on_prod=np.full((m, 1), 30.0),
        days_on_inj=np.full((m, ni), 30.0),
        bhp=bhp[:, None],
    )
    grid.dates = [None] * m  # type: ignore[list-item]
    s = SolverSettings.from_config(CFG, 30.0)
    s.n_starts = 4
    res = fit_field(FitData(grid, Split(100, m)), "crmp", s, seed=0)
    assert np.allclose(res.params.f[:, 0], [0.6, 0.3], atol=0.02)
    assert abs(res.params.tau[0] - 120.0) / 120.0 < 0.1
    assert res.params.J is not None and abs(res.params.J[0] - 4.0) < 0.5
    del q0


def test_power_law_oil_cut_fit_and_koval() -> None:
    cwi = np.linspace(1, 500, 100)
    truth = PowerLawOilCut(alpha=0.02, beta=1.2)
    fo = truth.oil_cut(cwi)
    liq = np.full(100, 1000.0)
    fit = fit_power_law(cwi, liq * fo, liq, np.ones(100, dtype=bool))
    assert abs(fit.alpha - 0.02) / 0.02 < 1e-6 and abs(fit.beta - 1.2) < 1e-6
    kv = KovalFractionalFlow(k_val=3.0, v_pd=5000.0)
    cum = np.linspace(0, 20000, 200)
    fw = kv.water_cut(cum)
    assert fw[0] == 0.0 and fw[-1] == 1.0 and np.all(np.diff(fw) >= -1e-12)
    kfit = fit_koval(cum, fw, np.ones(200, dtype=bool))
    assert abs(kfit.k_val - 3.0) < 0.05 and abs(kfit.v_pd - 5000.0) / 5000.0 < 0.05


def test_aicc_prefers_parsimony_at_equal_fit() -> None:
    assert aicc(100, 5, 10.0) < aicc(100, 20, 10.0)
