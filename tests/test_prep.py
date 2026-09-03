"""§7 / §8 processing: cleaning, gates, sectors, windows, split, pressure prep."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from waterflood_app.config import load_config
from waterflood_app.ingest.units import PVT
from waterflood_app.ingest.welltype import derive_well_types, split_roles
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.prep.clean import clean_grid, hampel
from waterflood_app.prep.events import build_windows
from waterflood_app.prep.gates import injection_cv, profile, run_gates
from waterflood_app.prep.grid import Grid, build_grid
from waterflood_app.prep.pressure_prep import classify_source, prepare_pressure
from waterflood_app.prep.sectors import blocks_from_category, sectorize
from waterflood_app.prep.split import train_blind_split
from waterflood_app.validation import synthetic_suite as suite

CFG = load_config()


def _grid(name: str) -> tuple[suite.Case, Grid]:
    case = suite.load_case(name)
    rates = case.rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas"))
    types = derive_well_types(rates)
    coords = case.coords.rename({"well_id": "well"})
    return case, build_grid(split_roles(rates, types), PVT(), coords)


def test_grid_time_convention_and_reservoir_volumes() -> None:
    case, g = _grid("streak_5x4")
    assert g.n_steps == 120 and g.n_inj == 5 and g.n_prod == 4
    assert g.dt_days[0] == g.dt_days[1] and g.time_days[0] == 0.0
    assert np.allclose(np.diff(g.time_days), g.dt_days[1:])
    m = suite.to_matrices(case.rates, case.injectors, case.producers)
    assert np.allclose(g.inj, m.injection) and np.allclose(g.liq, m.production)  # PVT() = identity


def test_hampel_replaces_only_spikes() -> None:
    x = np.sin(np.arange(60) / 5.0) * 10 + 100
    x[30] += 80
    y, n = hampel(x, 7, 3.0)
    assert n == 1 and abs(y[30] - 100) < 15 and np.allclose(np.delete(y, 30), np.delete(x, 30))
    _, n0 = hampel(x[:2], 7, 3.0)
    assert n0 == 0


def test_clean_grid_keeps_raw_and_flags() -> None:
    _, g = _grid("streak_5x4")
    g.liq[40, 2] *= 3.0
    g.oil[40, 2] *= 3.0
    log = ConditionLog()
    cg = clean_grid(g, CFG, log)
    assert log.has(ConditionCode.OUTLIERS_REMOVED)
    assert cg.liq[40, 2] < g.liq[40, 2] and "liq" in cg.raw


def test_gates_streak_pass_and_low_od_flags() -> None:
    _, g = _grid("streak_5x4")
    p = profile(g, CFG, tau_estimate_days=120.0, sum_f_apparent=1.0)
    log = ConditionLog()
    gates = run_gates(p, CFG, log)
    assert gates["od"] and gates["inj_cv"] and gates["tau_dt"] and not gates["influx"]
    assert not log.has(ConditionCode.OD_LOW)
    short = g.window(0, 30)
    p2 = profile(short, CFG, tau_estimate_days=50.0)
    log2 = ConditionLog()
    g2 = run_gates(p2, CFG, log2)
    assert (
        not g2["od"]
        and log2.has(ConditionCode.OD_LOW)
        and log2.has(ConditionCode.HISTORY_SHORT)
        and log2.has(ConditionCode.TAU_DT_LOW)
    )
    assert np.all(injection_cv(g.inj, g.days_on_inj) >= 0.15)


def test_influx_gate_and_message() -> None:
    _, g = _grid("aquifer_6x9")
    p = profile(g, CFG, tau_estimate_days=200.0, sum_f_apparent=1.43)
    log = ConditionLog()
    gates = run_gates(p, CFG, log)
    assert gates["influx"]
    msg = next(c for c in log if c.code == ConditionCode.SUM_F_HIGH).message
    assert "aquifer" in msg.lower()


def test_sectorization_splits_sealed_blocks() -> None:
    case, g = _grid("sectored_60")
    blocks = blocks_from_category(case.category.rename({"well_id": "well"}))
    sectors = sectorize(g, CFG, blocks)
    assert len(sectors) == 2
    for s in sectors:
        bl = {blocks[w] for w in s.injectors + s.producers}  # type: ignore[index]
        assert len(bl) == 1
    assert sum(s.n_wells for s in sectors) == 60
    # without blocks the two sectors are only 600 m apart → one sector (distance graph alone cannot split)
    assert len(sectorize(g, CFG, None)) == 1
    _, small = _grid("streak_5x4")
    assert len(sectorize(small, CFG, None)) == 1


def test_windows_break_at_conversion_and_merge_short() -> None:
    _, g = _grid("converted_wells")
    ev = pl.DataFrame(
        {
            "well": ["P-5", "P-6"],
            "date": [date(2014, 1, 1)] * 2,
            "type": ["CONVERSION"] * 2,
            "note": ["", ""],
        }
    )
    log = ConditionLog()
    w = build_windows(g, ev, CFG, log)
    assert [(x.start, x.end) for x in w] == [(0, 48), (48, 96)]
    ev2 = pl.DataFrame({"well": ["P-1"], "date": [date(2017, 10, 1)], "type": ["WORKOVER"], "note": [""]})
    w2 = build_windows(g, ev2, CFG, log)
    assert len(w2) == 1 and w2[0].end == 96  # 3-step tail merged back


def test_split_rule() -> None:
    s = train_blind_split(120, CFG)
    assert s.n_blind == 24 and s.n_train == 96
    assert train_blind_split(20, CFG).n_blind == 6


def test_pressure_sources() -> None:
    assert classify_source("ESP", True, True) == "esp"
    assert classify_source("gauge", True, False) == "gauge"
    assert classify_source(None, False, True) == "whp"
    assert classify_source("static survey", True, False) == "static"
    _, g = _grid("streak_5x4")
    log = ConditionLog()
    prep = prepare_pressure(None, g, CFG, log)
    assert prep.bhp is None and log.has(ConditionCode.NO_PRESSURE)
    rows = [
        {"well": w, "date": d, "bhp": 1500.0 - 2 * k, "whp": 200.0, "src": "ESP"}
        for w in g.producers
        for k, d in enumerate(g.dates)
    ]
    pf = pl.DataFrame(rows)
    prep2 = prepare_pressure(pf, g, CFG, ConditionLog())
    assert prep2.bhp is not None and prep2.bhp.shape == (g.n_steps, g.n_prod) and prep2.field_source == "esp"
    assert np.allclose(prep2.bhp[:, 0], 1500.0 - 2 * np.arange(g.n_steps))
