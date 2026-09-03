"""§6 units service — property-based tests."""

from __future__ import annotations

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from waterflood_app.ingest import units
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog

_pos = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


@given(v=_pos, tag=st.sampled_from(["bbl/d", "m3/d", "stb/d"]))
def test_rate_round_trip(v: float, tag: str) -> None:
    internal = units.to_internal(v, tag)
    back = units.convert(internal, "m3/d", tag)
    assert np.isclose(back, v, rtol=1e-12, atol=1e-9)


@given(
    v=_pos,
    a=st.sampled_from(["psi", "kPa", "bar", "MPa"]),
    b=st.sampled_from(["psi", "kPa", "bar", "MPa"]),
)
def test_pressure_conversion_is_consistent(v: float, a: str, b: str) -> None:
    ab = units.convert(v, a, b)
    assert np.isclose(units.convert(ab, b, a), v, rtol=1e-12, atol=1e-9)


def test_known_factors() -> None:
    assert np.isclose(units.convert(1.0, "bbl/d", "m3/d"), 0.158987, rtol=1e-5)
    assert np.isclose(units.convert(1.0, "psi", "kPa"), 6.894757, rtol=1e-6)
    assert np.isclose(units.convert(1.0, "ft", "m"), 0.3048, rtol=1e-12)
    assert np.isclose(units.convert(1.0, "Mscf/d", "m3/d"), 28.3168, rtol=1e-4)


def test_different_kinds_refuse() -> None:
    import pytest

    with pytest.raises(ValueError):
        units.convert(1.0, "psi", "m3/d")


@given(qo=st.lists(_pos, min_size=1, max_size=20), bo=st.floats(1.0, 2.0), bw=st.floats(1.0, 1.1))
def test_reservoir_volume_identity_and_monotonicity(qo: list[float], bo: float, bw: float) -> None:
    q = np.asarray(qo)
    qw = q * 0.5
    ident, free = units.reservoir_production(q, qw, None, units.PVT(bo=1.0, bw=1.0))
    assert np.allclose(ident, q + qw)
    assert not free.any()
    scaled, _ = units.reservoir_production(q, qw, None, units.PVT(bo=bo, bw=bw))
    assert np.all(scaled >= ident - 1e-9)
    assert np.allclose(scaled, q * bo + qw * bw)


def test_free_gas_detection_and_condition() -> None:
    qo = np.array([100.0, 100.0, 100.0])
    qg = np.array([50_000.0, 50_000.0, 120_000.0])  # scf/d; R_s = 500 scf/stb → GOR 500, 500, 1200
    pvt = units.PVT(bo=1.2, bw=1.0, rs=500.0, bg=0.005)
    log = ConditionLog()
    q_res, free = units.reservoir_production(qo, qo, qg, pvt, log=log)
    assert free.tolist() == [False, False, True]
    assert log.has(ConditionCode.GOR_ABOVE_RS) and log.has(ConditionCode.PVT_CONSTANTS_ASSUMED)
    assert q_res[2] > q_res[0]  # the free-gas term adds reservoir volume only where GOR > R_s


def test_pvt_table_interpolation() -> None:
    pvt = units.PVT(
        bw=1.02,
        table_p=(1000.0, 3000.0),
        table_bo=(1.1, 1.3),
        table_rs=(200.0, 600.0),
        table_bg=(0.01, 0.003),
    )
    bo, rs, bg = pvt.at(2000.0)
    assert np.isclose(bo[0], 1.2) and np.isclose(rs[0], 400.0) and np.isclose(bg[0], 0.0065)


@given(p=_pos, depth=st.floats(0, 5000), datum=st.floats(0, 5000))
def test_datum_shift_is_linear_and_reversible(p: float, depth: float, datum: float) -> None:
    g = 0.433
    shifted = units.datum_shift(p, depth, datum, g)
    assert np.isclose(units.datum_shift(shifted, datum, depth, g), p, atol=1e-6)


def test_time_base_rates() -> None:
    vol = np.array([3000.0, 0.0, 1500.0])
    dt = np.array([30.0, 31.0, 30.0])
    don = np.array([30.0, 0.0, 15.0])
    assert np.allclose(units.calendar_day_rate(vol, dt), [100.0, 0.0, 50.0])
    assert np.allclose(units.producing_day_rate(vol, don), [100.0, 0.0, 100.0])
