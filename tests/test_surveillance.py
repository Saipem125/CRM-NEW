"""Post-M5 — §10 rolling re-fit and CUSUM alerts inside a standard run; blended forecast parameters."""

from __future__ import annotations

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
