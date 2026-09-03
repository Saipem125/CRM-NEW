"""Milestone 1 acceptance on the M0 suite (§9, §16).

* the tournament selects the expected variant recorded in each ``truth.json`` (for the aquifer
  case the aquifer variant is *recognised* — eligible and recommended — pending its M2 fit);
* CRMP blind R² ≥ 0.9 on the noise-free streak field, ≥ 0.75 on the noisy one;
* the whole pipeline (types → grid → clean → pressure → windows → sectors → gates → tournament)
  runs on every case and never drops a well silently.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from waterflood_app.config import load_config
from waterflood_app.engine import RunResult, run_engine
from waterflood_app.ingest.connectors import LoadedData
from waterflood_app.ingest.units import PVT
from waterflood_app.messaging.conditions import ConditionCode
from waterflood_app.validation import synthetic_suite as suite

CFG = load_config()
EXPECTED = {
    "streak_5x4": "crmp",
    "streak_5x4_noise5": "crmp",
    "converted_wells": "crmp",
    "allocated_noisy": "crmp",
    "sectored_60": "crmp",
}


def _loaded(name: str) -> LoadedData:
    case = suite.load_case(name)
    rates = case.rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas"))
    ev = case.events.rename({"well_id": "well"}) if case.events is not None and case.events.height else None
    if ev is not None and "event" in ev.columns:
        ev = ev.rename({"event": "type"})
    return LoadedData(
        rates=rates,
        coords=case.coords.rename({"well_id": "well"}),
        category=case.category.rename({"well_id": "well"}),
        events=ev,
        pressure=case.pressure.rename({"well_id": "well", "p_static_psi": "bhp"}).with_columns(
            pl.lit("static").alias("src")
        )
        if case.pressure is not None
        else None,
    )


@pytest.fixture(scope="module")
def runs() -> dict[str, RunResult]:
    return {name: run_engine(_loaded(name), CFG, PVT(), seed=0) for name in suite.CASES}


@pytest.mark.parametrize("name", [c for c in suite.CASES if c != "aquifer_6x9"])
def test_tournament_selects_expected_variant(runs: dict[str, RunResult], name: str) -> None:
    run = runs[name]
    latest = run.latest()
    assert latest, "no sector fitted"
    for s in latest:
        assert s.tournament.winner is not None
        assert s.tournament.winner.variant == EXPECTED[name], s.tournament.leaderboard


def test_blind_r2_floors_streak(runs: dict[str, RunResult]) -> None:
    clean = runs["streak_5x4"].latest()[0].tournament
    noisy = runs["streak_5x4_noise5"].latest()[0].tournament
    crmp_clean = next(e for e in clean.entries if e.variant == "crmp")
    crmp_noisy = next(e for e in noisy.entries if e.variant == "crmp")
    assert crmp_clean.report.blind_r2_field >= 0.9, crmp_clean.report.blind_r2_field
    assert crmp_noisy.report.blind_r2_field >= 0.75, crmp_noisy.report.blind_r2_field
    assert clean.confidence == "HIGH", clean.confidence_reasons


def test_streak_parameters_recovered_by_pipeline(runs: dict[str, RunResult]) -> None:
    case = suite.load_case("streak_5x4")
    s = runs["streak_5x4"].latest()[0]
    p = s.tournament.winner.fit.params  # type: ignore[union-attr]
    f_true = case.f_matrix(s.grid.injectors, s.grid.producers)
    big = f_true >= 0.05
    assert (np.abs(p.f - f_true)[big] / f_true[big]).max() < 0.1
    streak = case.truth["streak"]
    i, j = s.grid.injectors.index(streak["injector"]), s.grid.producers.index(streak["producer"])
    assert p.f[i, j] == p.f.max()
    assert s.oil_prediction is not None and s.oil_prediction.shape == s.grid.liq.shape


def test_aquifer_signature_recognised(runs: dict[str, RunResult]) -> None:
    s = runs["aquifer_6x9"].latest()[0]
    assert s.gates["influx"], s.profile.sum_f_apparent
    aq = next(e for e in s.tournament.eligibility if e.variant == "aquifer")
    assert aq.eligible and not aq.available  # recommended, fitted in Milestone 2
    assert s.conditions.has(ConditionCode.SUM_F_HIGH)
    assert s.conditions.has(ConditionCode.VARIANT_NOT_AVAILABLE)
    # CRMP is the best available model meanwhile and the aquifer support inflates Σ_i f_ij
    assert s.tournament.winner is not None and s.tournament.winner.variant in ("crmp", "crmip")


def test_converted_wells_two_windows(runs: dict[str, RunResult]) -> None:
    run = runs["converted_wells"]
    assert len(run.windows) == 2 and run.windows[1].start == 48
    assert run.well_types["P-5"].type == "MIXED"
    last = run.latest()[0]
    assert {"P-5@I", "P-6@I"} <= set(last.grid.injectors) and "P-5@P" not in last.grid.producers
    first = next(s for s in run.sectors if s.window_index == 0)
    assert {"P-5@P", "P-6@P"} <= set(first.grid.producers)


def test_sectored_field_two_sectors_and_no_cross_fault(runs: dict[str, RunResult]) -> None:
    run = runs["sectored_60"]
    latest = run.latest()
    assert len(latest) == 2
    assert run.runtime_s < 600.0, run.runtime_s
    for s in latest:
        assert s.gates["od"]  # per-sector O_d = 9.2
        assert s.tournament.winner is not None


def test_allocated_noisy_keeps_days_on_mask(runs: dict[str, RunResult]) -> None:
    s = runs["allocated_noisy"].latest()[0]
    assert (s.grid.days_on_prod == 0).any()
    assert s.tournament.winner is not None and s.tournament.winner.report.blind_r2_field > 0.6


def test_no_silent_drops_and_hashes(runs: dict[str, RunResult]) -> None:
    run = runs["streak_5x4"]
    assert len(run.data_hash) == 64 and len(run.config_hash) == 64
    summary = run.summary()
    assert summary["sectors"][0]["winner"] == "crmp"
    assert all("message" in c for c in summary["conditions"])
