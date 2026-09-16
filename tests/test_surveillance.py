"""Post-M5 — §10 rolling re-fit and CUSUM alerts inside a standard run; blended forecast parameters."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from waterflood_app.api.bundle import build_bundle
from waterflood_app.config import load_config
from waterflood_app.engine import RunResult, run_engine
from waterflood_app.ingest.connectors import LoadedData
from waterflood_app.ingest.units import PVT
from waterflood_app.optimize.run import forecast_models
from waterflood_app.validation import synthetic_suite as suite

CFG = load_config()


def _loaded(name: str) -> LoadedData:
    case = suite.load_case(name)
    return LoadedData(
        rates=case.rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas")),
        coords=case.coords.rename({"well_id": "well"}),
        category=case.category.rename({"well_id": "well"}),
        events=None,
        pressure=None,
    )


@pytest.fixture(scope="module")
def run_default() -> RunResult:
    return run_engine(_loaded("streak_5x4"), CFG, PVT(), seed=0, variants=["crmp"])


def test_rolling_refit_runs_inside_a_standard_run(run_default: RunResult) -> None:
    s = run_default.latest()[0]
    assert s.rolling is not None and len(s.rolling.windows) >= 10  # 120 months, 36/6 windows
    assert set(s.rolling.weights) == {"latest_window", "full_history"}
    assert abs(sum(s.rolling.weights.values()) - 1.0) < 1e-9
    assert s.shifts == []  # stationary fixture: no CUSUM alert
    assert not any(c.code.value == "CUSUM_SHIFT" for c in s.conditions)
    b = build_bundle(run_default, {}, CFG, "p", {"unit_system": "field"})
    sec = b["sectors"][0]
    assert sec["shifts"] == [] and len(sec["rolling"]["windows"]) == len(s.rolling.windows)
    w0 = sec["rolling"]["windows"][0]
    assert {"start", "end", "blind_r2", "f_ij", "tau_days"} <= set(w0)


def test_forecast_uses_blended_parameters(run_default: RunResult) -> None:
    s = run_default.latest()[0]
    models, _weights = forecast_models(s, CFG)
    assert models and models[0].params.extra.get("rolling_blend")
    assert s.rolling is not None and s.tournament.winner is not None
    last = s.rolling.windows[-1].params
    full = s.tournament.winner.fit.params
    wl, wf = s.rolling.weights["latest_window"], s.rolling.weights["full_history"]
    # the winner member is an exact blend of the latest window and the full fit
    assert np.allclose(models[0].params.f, wl * last.f + wf * full.f, atol=1e-9)
    assert np.allclose(models[0].params.f.sum(axis=1), full.f.sum(axis=1), atol=0.05)  # noise-free: near-identical


def test_rolling_mode_switches() -> None:
    off = run_engine(
        _loaded("streak_5x4"), CFG.with_overrides({"rolling": {"mode": "never"}}), PVT(), seed=0, variants=["crmp"]
    )
    assert off.latest()[0].rolling is None
    big = run_engine(
        _loaded("streak_5x4"),
        CFG.with_overrides({"rolling": {"mode": "auto", "max_wells_in_run": 5}}),
        PVT(),
        seed=0,
        variants=["crmp"],
    )
    assert big.latest()[0].rolling is None  # 9 wells > 5 → deferred to the surveillance job
    short = run_engine(
        _loaded("streak_5x4"),
        CFG.with_overrides({"rolling": {"window_months": 200, "min_window_steps": 200}}),
        PVT(),
        seed=0,
        variants=["crmp"],
    )
    assert short.latest()[0].rolling is None  # fewer than two windows


def test_cusum_alert_from_a_real_shift() -> None:
    """A synthetic doubling of one pair's allocation after month 60 raises a CUSUM condition in the run."""
    case = suite.load_case("streak_5x4")
    rates = case.rates.rename({"well_id": "well"})
    # make P-1 respond twice as strongly to I-1 in the second half by scaling P-1's liquid with I-1's rate
    inj = rates.filter(pl.col("well") == "I-1").select(["date", "q_inj"]).rename({"q_inj": "i1"})
    dates = sorted(rates["date"].unique().to_list())
    cut = dates[60]
    r2 = (
        rates.join(inj, on="date", how="left")
        .with_columns(
            [
                pl.when((pl.col("well") == "P-1") & (pl.col("date") >= cut))
                .then(pl.col(c) + 0.6 * pl.col("i1"))
                .otherwise(pl.col(c))
                .alias(c)
                for c in ("q_oil", "q_water")
            ]
        )
        .drop("i1")
    )
    loaded = LoadedData(
        rates=r2.with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas")),
        coords=case.coords.rename({"well_id": "well"}),
        category=case.category.rename({"well_id": "well"}),
        events=None,
        pressure=None,
    )
    cfg = CFG.with_overrides({"rolling": {"window_months": 30, "step_months": 6, "min_window_steps": 24}})
    run = run_engine(loaded, cfg, PVT(), seed=0, variants=["crmp"])
    s = run.latest()[0]
    assert s.rolling is not None
    assert any(x.injector and "I-1" in x.injector and "P-1" in x.producer for x in s.shifts), [
        x.to_dict() for x in s.shifts
    ]
    assert any(c.code.value == "CUSUM_SHIFT" for c in s.conditions)


def _loaded_with_closed_wells(producer: str | None, injector: str | None, months: int = 6) -> LoadedData:
    """streak_5x4 with a producer and/or an injector shut in (rate 0, days_on 0) over the last ``months``."""
    case = suite.load_case("streak_5x4")
    rates = case.rates.rename({"well_id": "well"})
    cut = sorted(rates["date"].unique().to_list())[-months]
    closed = [w for w in (producer, injector) if w]
    late = pl.col("well").is_in(closed) & (pl.col("date") >= cut)
    rates = rates.with_columns(
        [pl.when(late).then(0.0).otherwise(pl.col(c)).alias(c) for c in ("q_oil", "q_water", "q_inj", "days_on")]
    )
    return LoadedData(
        rates=rates.with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas")),
        coords=case.coords.rename({"well_id": "well"}),
        category=case.category.rename({"well_id": "well"}),
        events=None,
        pressure=None,
    )


def test_closed_producer_has_no_forecast_and_idle_injector_is_not_restarted() -> None:
    """Wells closed at the end of history (first field data): a shut-in producer contributes no forecast
    oil, an idle injector is held at zero by the plan instead of being "restarted" as a set-point change."""
    from waterflood_app.models.forecast import active_producers, idle_injectors
    from waterflood_app.optimize.constraints import PlanConstraints
    from waterflood_app.optimize.run import optimize_sector

    cfg = CFG.with_overrides({"rolling": {"mode": "never"}})
    run = run_engine(_loaded_with_closed_wells("P-1", "I-1"), cfg, PVT(), seed=0, variants=["crmp"])
    s = run.latest()[0]
    g = s.grid
    jp, ii = g.producers.index("P-1"), g.injectors.index("I-1")
    assert active_producers(g)[jp] == 0.0 and active_producers(g).sum() == g.n_prod - 1
    assert idle_injectors(g)[ii] and idle_injectors(g).sum() == 1
    rec = optimize_sector(s, cfg, "oil", "balanced", seed=0)
    res = rec.result
    for f in res.forecasts_base + res.forecasts_plan:
        assert np.all(f.oil[:, jp] == 0.0) and np.all(f.liq_res[:, jp] == 0.0)
        assert np.any(f.oil[:, [j for j in range(g.n_prod) if j != jp]] > 0.0)
    assert np.all(res.base.x[..., ii] == 0.0) and np.all(res.plan.x[..., ii] == 0.0)
    assert all(a.well != "I-1" for a in rec.actions)
    assert any("idle at end of history" in n and "I-1" in n for n in res.notes)
    assert any("closed at end of history" in n and "P-1" in n for n in res.notes)
    # the switches restore the previous behaviour
    allow = cfg.with_overrides({"optimize": {"allow_restart_idle_injectors": True}})
    cons = PlanConstraints.for_grid(g, res.base.x, allow)
    assert cons.inj_max[ii] > 0.0 and not cons.notes
    flow = cfg.with_overrides({"optimize": {"forecast_shut_in_producers": True}})
    rec2 = optimize_sector(s, flow, "oil", "balanced", seed=0)
    assert np.any(rec2.result.forecasts_base[0].liq_res[:, jp] > 0.0)


def test_influence_radius_masks_far_pairs_in_every_fit() -> None:
    """solver.distance_cutoff_factor is an influence radius: pairs beyond it are fixed at f_ij = 0 in the
    tournament fits (CRMP and CRMIP), every producer keeps its nearest injector, the aquifer row is free."""
    from waterflood_app.models.base import distance_mask

    cfg = CFG.with_overrides({"rolling": {"mode": "never"}, "solver": {"distance_cutoff_factor": 1.2}})
    loaded = _loaded("streak_5x4")
    run = run_engine(loaded, cfg, PVT(), seed=0, variants=["crmp", "crmip", "aquifer"])
    s = run.latest()[0]
    g = s.grid
    mask = distance_mask(g, cfg)
    assert mask is not None and mask.shape == (g.n_inj, g.n_prod)
    assert 0 < int((~mask).sum()) < mask.size, "the factor must switch off some pairs but not all"
    dist = g.distances()
    assert dist is not None
    assert bool(np.all(mask[np.argmin(dist, axis=0), np.arange(g.n_prod)]))  # nearest injector kept
    for e in s.tournament.entries:
        if e.variant in ("crmp", "crmip", "aquifer"):
            f = np.asarray(e.fit.params.f)[: g.n_inj]
            assert np.all(f[~mask] == 0.0), e.variant
            assert np.any(f[mask] > 0.0), e.variant
    off = run_engine(loaded, CFG.with_overrides({"rolling": {"mode": "never"}}), PVT(), seed=0, variants=["crmp"])
    f_off = np.asarray(off.latest()[0].tournament.winner.fit.params.f)  # type: ignore[union-attr]
    assert np.any(f_off[~mask] > 0.0)  # without the radius, far pairs are free to connect


def test_restart_transient_weights() -> None:
    """solver.restart_transient_months / pre_shutin_months switch the fit weight off around shut-ins."""
    from waterflood_app.models.base import fit_weights

    cfg = CFG.with_overrides({"rolling": {"mode": "never"}})
    run = run_engine(_loaded_with_closed_wells("P-1", None, months=6), cfg, PVT(), seed=0, variants=["crmt"])
    g = run.latest()[0].grid
    jp = g.producers.index("P-1")
    w0 = fit_weights(g, cfg)
    assert np.array_equal(w0, g.prod_mask.astype(float))  # defaults: the mask only
    w1 = fit_weights(g, cfg.with_overrides({"solver": {"pre_shutin_months": 2, "restart_transient_months": 1}}))
    off = int(np.flatnonzero(~g.prod_mask[:, jp])[0])  # first shut-in step of P-1
    assert w1[off - 2 : off, jp].sum() == 0.0 and w1[off - 3, jp] == 1.0
    others = [j for j in range(g.n_prod) if j != jp]
    assert np.array_equal(w1[:, others], w0[:, others])
    # a restart in the middle of the history: the first month back is dropped, the second kept
    m = g.prod_mask.copy()
    m[20:24, jp] = False
    g2 = replace(g, days_on_prod=np.where(m, g.days_on_prod, 0.0))
    w2 = fit_weights(g2, cfg.with_overrides({"solver": {"restart_transient_months": 1}}))
    assert w2[24, jp] == 0.0 and w2[25, jp] == 1.0 and w2[19, jp] == 1.0


def test_late_starter_is_simulated_from_its_first_producing_month() -> None:
    """A producer that comes on stream inside the window has no simulated rate before that month, and
    the bundle's model series (what the history-match plot draws) is zero there too."""
    case = suite.load_case("streak_5x4")
    rates = case.rates.rename({"well_id": "well"})
    cut = sorted(rates["date"].unique().to_list())[18]
    late = (pl.col("well") == "P-2") & (pl.col("date") < cut)
    rates = rates.with_columns(
        [pl.when(late).then(0.0).otherwise(pl.col(c)).alias(c) for c in ("q_oil", "q_water", "days_on")]
    )
    loaded = LoadedData(
        rates=rates.with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas")),
        coords=case.coords.rename({"well_id": "well"}),
        category=case.category.rename({"well_id": "well"}),
        events=None,
        pressure=None,
    )
    cfg = CFG.with_overrides({"rolling": {"mode": "never"}})
    run = run_engine(loaded, cfg, PVT(), seed=0, variants=["crmp"])
    s = run.latest()[0]
    j = s.grid.producers.index("P-2")
    pred = np.asarray(s.tournament.winner.fit.prediction)  # type: ignore[union-attr]
    assert np.all(pred[:18, j] == 0.0) and np.all(pred[18:, j] > 0.0)
    assert np.all(pred[:, [k for k in range(s.grid.n_prod) if k != j]] > 0.0)
    b = build_bundle(run, {}, cfg, "p", {"unit_system": "field"})
    series = next(p for p in b["sectors"][0]["producers"] if p["well"] == "P-2")["model"]
    assert max(series[:18]) == 0.0 and min(series[18:]) > 0.0


def test_field_tank_winner_recommends_no_reallocation() -> None:
    """A CRMT winner cannot distinguish injectors: the plan is hold-current, gain 0, no actions (ALFA finding)."""
    from waterflood_app.optimize.run import optimize_sector

    run = run_engine(
        _loaded("streak_5x4"), CFG.with_overrides({"rolling": {"mode": "never"}}), PVT(), seed=0, variants=["crmt"]
    )
    s = run.latest()[0]
    assert s.tournament.winner is not None and s.tournament.winner.variant == "crmt"
    rec = optimize_sector(s, CFG, "oil", "balanced", seed=0)
    assert rec.result.gain_vs_base_pct == 0.0 and rec.actions == []
    assert np.allclose(rec.result.plan.x, rec.result.base.x)
    assert any("no reallocation" in n for n in rec.notes)
