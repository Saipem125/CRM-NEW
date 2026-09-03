"""Milestone 2 acceptance — optimizer (§12–§14).

* on ``streak_5x4`` the optimizer increases cumulative oil vs. the equal (proportional) split by
  the amount recorded in ``truth.json`` ± 5 % (relative);
* the robust posture never selects a plan that loses oil in any ensemble member;
* objectives, constraints, ramping and the action list behave as §12–§14 specify.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from waterflood_app.config import load_config
from waterflood_app.engine import RunResult, run_engine
from waterflood_app.ingest.connectors import LoadedData
from waterflood_app.ingest.units import PVT
from waterflood_app.models.forecast import month_steps
from waterflood_app.optimize.action_list import build_action_list
from waterflood_app.optimize.constraints import PlanConstraints
from waterflood_app.optimize.economics import Economics, price_tornado
from waterflood_app.optimize.objectives import MinWaterForTargetOil, NetPresentValue, make_objective
from waterflood_app.optimize.posture import aggregate, effective_posture
from waterflood_app.optimize.ramping import ramp
from waterflood_app.optimize.run import forecast_models, optimize_sector
from waterflood_app.optimize.scenarios import ScenarioManager
from waterflood_app.optimize.solvers import optimize_plan
from waterflood_app.validation import synthetic_suite as suite

CFG = load_config()


def _loaded(name: str) -> LoadedData:
    case = suite.load_case(name)
    rates = case.rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas"))
    return LoadedData(
        rates=rates, coords=case.coords.rename({"well_id": "well"}), category=case.category.rename({"well_id": "well"})
    )


@pytest.fixture(scope="module")
def streak_run() -> RunResult:
    return run_engine(_loaded("streak_5x4"), CFG, PVT(), seed=0, variants=["crmp"])


@pytest.fixture(scope="module")
def noisy_run() -> RunResult:
    return run_engine(_loaded("streak_5x4_noise5"), CFG, PVT(), seed=0, variants=["crmp"])


def test_optimizer_matches_truth_gain(streak_run: RunResult) -> None:
    t = suite.load_case("streak_5x4").truth["optimizer"]
    rec = optimize_sector(streak_run.latest()[0], CFG, "oil", "balanced", seed=0)
    gain = rec.result.gain_vs_equal_split_pct
    assert abs(gain - t["gain_vs_equal_split_pct"]) / t["gain_vs_equal_split_pct"] < 0.05, (
        gain,
        t["gain_vs_equal_split_pct"],
    )
    assert abs(rec.result.gain_vs_base_pct - t["gain_vs_hold_current_pct"]) / t["gain_vs_hold_current_pct"] < 0.05
    rates = dict(zip(rec.injectors, rec.result.plan.x[0], strict=True))
    truth_rates = dict(zip(suite.load_case("streak_5x4").injectors, t["optimal_rates_bbl_d"], strict=True))
    for w, v in truth_rates.items():
        assert abs(rates[w] - v) < 0.03 * t["total_water_bbl_d"], (w, rates[w], v)
    assert abs(rec.result.plan.x[0].sum() - t["total_water_bbl_d"]) < 1e-3 * t["total_water_bbl_d"]
    assert rec.confidence == "HIGH" and rec.posture == "balanced"


def test_robust_posture_never_loses_oil_in_any_member(noisy_run: RunResult) -> None:
    s = noisy_run.latest()[0]
    models, weights = forecast_models(s, CFG)
    assert len(models) >= 2, "the noisy case must yield a multi-member ensemble"
    cur = models[0].current_injection()
    cons = PlanConstraints.from_config(cur, CFG)
    res = optimize_plan(models, make_objective("oil"), cons, "robust", CFG, weights, seed=0, current=cur)
    assert np.all(res.member_values_plan >= res.member_values_base - 1e-9), (
        res.member_values_plan,
        res.member_values_base,
    )
    assert res.value_plan >= res.value_base - 1e-9
    # the P10 statistic is what the robust posture maximises
    assert res.value_plan == pytest.approx(aggregate(res.member_values_plan, "robust", CFG))


def test_posture_rules_and_low_confidence() -> None:
    assert effective_posture(None, "HIGH", CFG) == ("balanced", None)
    p, note = effective_posture("aggressive", "LOW", CFG)
    assert p == "robust" and note
    v = np.array([10.0, 12.0, 14.0])
    assert aggregate(v, "aggressive", CFG) == pytest.approx(12.0)
    assert aggregate(v, "balanced", CFG) == pytest.approx(12.0 - 0.5 * v.std())
    assert aggregate(v, "robust", CFG) == pytest.approx(np.percentile(v, 10))


def test_objectives_and_constraints(streak_run: RunResult) -> None:
    s = streak_run.latest()[0]
    models, weights = forecast_models(s, CFG)
    m = models[0]
    cur = m.current_injection()
    _, dt = month_steps(m.grid.dates[-1], 12)
    fc = m.simulate(np.tile(cur, (12, 1)), dt)
    econ = Economics.from_config(CFG)
    npv = NetPresentValue(economics=econ).value(fc)
    # a 99 %-water-cut field: costs exceed revenue at $70, so NPV is negative but rises with price
    assert npv < econ.oil_price * fc.cum_oil
    assert econ.with_price(140.0).npv(fc) > npv
    torn = price_tornado(econ, fc, fc)
    assert len(torn) == 5 and all(abs(t["delta_npv"]) < 1e-6 for t in torn)
    # min water for a target oil: hitting 95 % of the hold-current oil needs less water
    target = 0.95 * fc.cum_oil
    cons = PlanConstraints.from_config(cur, CFG, same_water=False, total_water=float(cur.sum()))
    res = optimize_plan(
        models,
        MinWaterForTargetOil(target_oil=target),
        cons,
        "aggressive",
        CFG,
        weights,
        horizon_months=12,
        seed=0,
        current=cur,
    )
    assert res.plan.x[0].sum() < cur.sum() * 0.999
    assert res.forecasts_plan[0].cum_oil >= target * (1 - 1e-6)
    # per-injector bounds are honoured
    cons2 = PlanConstraints.from_config(cur, CFG, inj_max=cur * 1.1)
    res2 = optimize_plan(
        models, make_objective("oil"), cons2, "aggressive", CFG, weights, horizon_months=12, seed=0, current=cur
    )
    assert np.all(res2.plan.x[0] <= cur * 1.1 + 1e-6)


def test_ramping_and_action_list(streak_run: RunResult) -> None:
    r = CFG.section("ramping")
    cur = np.array([1000.0, 1000.0, 1000.0, 1000.0])
    tgt = np.array([1500.0, 700.0, 1030.0, 1003.0])
    rp = ramp(cur, tgt, "HIGH", CFG)
    assert rp.step_frac == r["step_frac_per_week"]["HIGH"]
    assert rp.suppressed.tolist() == [False, False, True, True]  # 3 % and 0.3 % changes suppressed
    assert abs(rp.first_week[0] - 1150.0) < 1e-9 and abs(rp.first_week[1] - 850.0) < 1e-9
    assert np.allclose(rp.weekly[-1][:2], [1500.0, 700.0])
    rp_low = ramp(cur, tgt, "LOW", CFG)
    assert rp_low.weeks_to_target > rp.weeks_to_target
    rec = optimize_sector(
        streak_run.latest()[0],
        CFG,
        "oil",
        "balanced",
        seed=0,
        start=date(2020, 1, 1),
        facilities={"I-1": "A", "I-2": "A", "I-3": "B", "I-4": "B", "I-5": "B"},
    )
    items = rec.actions
    assert items and all(it.revert_if and it.setting_hint and it.target_date > date(2020, 1, 1) for it in items)
    assert sum(it.expected_oil_gain for it in items) == pytest.approx(
        max(rec.result.value_plan - rec.result.value_base, 0.0), rel=1e-6
    )
    facs = [it.facility for it in items]
    assert facs == sorted(facs, key=lambda f: facs.index(f))  # grouped by facility
    d = rec.to_dict()
    assert d["cum_oil_plan_p10_p50_p90"][1] >= d["cum_oil_base_p10_p50_p90"][1]


def test_scenarios_compare_on_the_same_ensemble(streak_run: RunResult) -> None:
    s = streak_run.latest()[0]
    models, weights = forecast_models(s, CFG)
    sm = ScenarioManager(models, weights, make_objective("oil"), "balanced", CFG, seed=0, horizon_months=12)
    base = sm.base()
    re = sm.reallocation()
    assert re.delta_vs_base > 0 and re.delta_pct_vs_base > 0
    sweep = sm.water_budget_sweep((0.9, 1.0, 1.1))
    assert sweep[2].value >= sweep[1].value >= sweep[0].value
    shut = sm.shut_in_injector("I-3")
    assert shut.value < base.value
    new = sm.new_injector("I-NEW", np.array([1500.0, 1500.0]), 800.0)
    assert new.prior_based and new.value > base.value
    conv = sm.convert_producer("P-4", 600.0)
    assert conv.prior_based
    pb = sm.pattern_balancing(1.0)
    assert pb.kind == "pattern_balancing"
    rows = sm.compare()
    assert {r["kind"] for r in rows} >= {
        "base",
        "reallocation",
        "water_budget",
        "shut_in",
        "new_well",
        "conversion",
        "pattern_balancing",
    }


def test_build_action_list_setting_hint_uses_injectivity(streak_run: RunResult) -> None:
    rec = optimize_sector(streak_run.latest()[0], CFG, "oil", "balanced", seed=0)
    items, _ = build_action_list(
        rec.result,
        rec.injectors,
        "HIGH",
        CFG,
        date(2021, 1, 1),
        injectivity=dict.fromkeys(rec.injectors, 5.0),
        p_res=3000.0,
        pressure_limit=4000.0,
    )
    assert all("injection pressure about" in it.setting_hint for it in items)
    assert all("exceeds" in it.revert_if for it in items)
