"""§5 well-type derivation, role splitting, commingled split, ID normalisation (tested on converted_wells)."""

from __future__ import annotations

from datetime import date

import polars as pl

from waterflood_app.ingest.welltype import (
    conversion_events,
    derive_well_types,
    normalise_id,
    split_commingled,
    split_roles,
)
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.validation import synthetic_suite as suite
from waterflood_app.validation.external_fixtures import load_external, truth


def _canon(rates: pl.DataFrame) -> pl.DataFrame:
    return rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas"))


def test_normalise_id() -> None:
    assert normalise_id("  p-2 ") == "P-2"
    assert normalise_id("P - 2") == "P-2"
    assert normalise_id("well 7a") == "WELL 7A"
    assert normalise_id("P_02", {"P_02": "P-2"}) == "P-2"


def test_m0_converted_wells_types_and_conversion_date() -> None:
    case = suite.load_case("converted_wells")
    log = ConditionLog()
    types = derive_well_types(_canon(case.rates), log)
    assert types["P-5"].type == "MIXED" and types["P-6"].type == "MIXED"
    assert types["P-1"].type == "PROD" and types["I-1"].type == "INJ"
    conv = types["P-5"].conversions
    assert len(conv) == 1 and conv[0].from_role == "P" and conv[0].to_role == "I"
    assert conv[0].date == date(2014, 1, 1)
    # the conversion month reports both production and injection → flagged, treated as conversion month
    assert types["P-5"].n_simultaneous == 1
    assert log.has(ConditionCode.SIMULTANEOUS_PI) and log.has(ConditionCode.CONVERSION_DETECTED)


def test_external_converted_wells_types_exact() -> None:
    loaded = load_external("converted_wells")
    types = derive_well_types(loaded.rates)
    expected = truth("converted_wells")["expected_well_types"]
    assert {w: types[w].type for w in expected} == expected
    for w in ("P-2", "P-5"):
        assert types[w].conversions[0].date == date(2020, 1, 1)


def test_split_roles_makes_two_entities_with_a_date_boundary() -> None:
    case = suite.load_case("converted_wells")
    rates = _canon(case.rates)
    types = derive_well_types(rates)
    split = split_roles(rates, types)
    ents = set(split.get_column("entity").unique().to_list())
    assert {"P-5@P", "P-5@I", "P-6@P", "P-6@I", "P-1", "I-1"} <= ents
    p = split.filter(pl.col("entity") == "P-5@P")
    i = split.filter(pl.col("entity") == "P-5@I")
    assert p.get_column("date").max() == date(2014, 1, 1)  # conversion month belongs to both roles
    assert i.get_column("date").min() == date(2014, 1, 1)
    assert (p.get_column("q_inj") == 0).all() and (i.get_column("q_oil") == 0).all()
    ev = conversion_events(types)
    assert ev.height == 2 and set(ev.get_column("type").to_list()) == {"CONVERSION"}


def test_commingled_split_equal_with_warning_and_with_factors() -> None:
    rates = pl.DataFrame(
        {
            "well": ["W1", "W1", "W2"],
            "date": [date(2020, 1, 1), date(2020, 2, 1), date(2020, 1, 1)],
            "q_oil": [100.0, 120.0, 50.0],
            "q_water": [10.0, 12.0, 5.0],
            "q_gas": [None, None, None],
            "q_inj": [0.0, 0.0, 0.0],
            "days_on": [31.0, 29.0, 31.0],
        },
        schema_overrides={"q_gas": pl.Float64},
    )
    cat = pl.DataFrame({"well": ["W1", "W1", "W2"], "field": ["F", "F", "F"], "reservoir": ["A", "B", "A"]})
    log = ConditionLog()
    out, factors = split_commingled(rates, cat, log)
    assert factors == {"W1": {"A": 0.5, "B": 0.5}} and log.has(ConditionCode.COMMINGLED_EQUAL_SPLIT)
    assert set(out.get_column("well").unique().to_list()) == {"W1#A", "W1#B", "W2"}
    assert abs(float(out.filter(pl.col("well").str.starts_with("W1")).get_column("q_oil").sum()) - 220.0) < 1e-9
    cat2 = cat.with_columns(pl.Series("alloc_factor", [0.75, 0.25, 1.0]))
    out2, f2 = split_commingled(rates, cat2, ConditionLog())
    assert f2["W1"] == {"A": 0.75, "B": 0.25}
    assert abs(out2.filter(pl.col("well") == "W1#A").get_column("q_oil")[0] - 75.0) < 1e-9
