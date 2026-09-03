"""Canonical datasets and column-name conventions — architecture §5, Data Loader reference.

Four canonical datasets (rates required; pressure, coords, category optional) plus the events
table. Field names, required flags and the auto-mapping hints are copied from the Data Loader
reference (``DATASETS`` in ``docs/spec/CRM_Data_Loader.html``) so the loader behaves the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    required: bool
    hints: tuple[str, ...]
    dtype: type[pl.DataType]
    unit_kind: str | None = None  # "rate" | "gas_rate" | "pressure" | "length" | None


@dataclass(frozen=True)
class Dataset:
    name: str
    label: str
    fields: tuple[Field, ...]
    table_hints: tuple[str, ...]
    required: bool = False
    extra_rule: str = ""

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    @property
    def required_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.required]

    @property
    def schema(self) -> dict[str, type[pl.DataType]]:
        return {f.name: f.dtype for f in self.fields}


_WELL_HINTS = ("well_name", "well", "uwi", "well_id", "wellname", "wellbore", "well_no")

RATES = Dataset(
    name="rates",
    label="Rates (production / injection)",
    required=True,
    table_hints=("prod", "inj", "rate", "monthly", "daily"),
    extra_rule="at least one of oil/water or injection must be mapped",
    fields=(
        Field("well", "well id", True, _WELL_HINTS, pl.Utf8),
        Field("date", "date", True, ("prod_date", "date", "month", "period", "time"), pl.Date),
        Field(
            "q_oil",
            "oil rate / volume",
            False,
            ("oil_vol", "oil", "qo", "oil_rate", "q_oil"),
            pl.Float64,
            "rate",
        ),
        Field(
            "q_water",
            "water rate / volume",
            False,
            ("wat_vol", "water", "qw", "water_rate", "q_water", "wtr"),
            pl.Float64,
            "rate",
        ),
        Field(
            "q_gas",
            "gas",
            False,
            ("gas_vol", "gas", "qg", "gas_rate", "q_gas"),
            pl.Float64,
            "gas_rate",
        ),
        Field(
            "q_inj",
            "water injection",
            False,
            ("winj_vol", "inj", "injection", "q_inj", "water_inj", "winj"),
            pl.Float64,
            "rate",
        ),
        Field(
            "days_on",
            "days on",
            False,
            ("days_on", "days", "prod_days", "dayson", "on_days"),
            pl.Float64,
        ),
        Field("type", "well type (optional)", False, ("type", "well_type", "role"), pl.Utf8),
    ),
)

PRESSURE = Dataset(
    name="pressure",
    label="Pressure",
    table_hints=("press", "bhp", "gauge", "pip"),
    fields=(
        Field("well", "well id", True, _WELL_HINTS, pl.Utf8),
        Field("date", "date", True, ("press_date", "date", "time", "month"), pl.Date),
        Field(
            "bhp",
            "flowing BHP",
            False,
            ("fbhp", "bhp", "pwf", "pip", "bottom"),
            pl.Float64,
            "pressure",
        ),
        Field("whp", "WHP", False, ("whp", "thp", "wellhead"), pl.Float64, "pressure"),
        Field("src", "source / sensor", False, ("src", "source", "sensor"), pl.Utf8),
        Field(
            "depth",
            "gauge / intake depth",
            False,
            ("gauge_depth", "intake_depth", "sensor_depth", "depth"),
            pl.Float64,
            "length",
        ),
    ),
)

COORDS = Dataset(
    name="coords",
    label="Well coordinates",
    table_hints=("header", "coord", "master", "loc"),
    fields=(
        Field("well", "well id", True, _WELL_HINTS, pl.Utf8),
        Field(
            "x",
            "X / easting",
            True,
            ("x_coord", "x", "east", "easting", "surf_x"),
            pl.Float64,
            "length",
        ),
        Field(
            "y",
            "Y / northing",
            True,
            ("y_coord", "y", "north", "northing", "surf_y"),
            pl.Float64,
            "length",
        ),
        Field(
            "tvd",
            "TVD perforation",
            False,
            ("tvd", "tvd_perf", "depth", "perf_depth"),
            pl.Float64,
            "length",
        ),
    ),
)

CATEGORY = Dataset(
    name="category",
    label="Category (field / reservoir / block)",
    table_hints=("cat", "field", "class", "hier"),
    fields=(
        Field("well", "well id", True, _WELL_HINTS, pl.Utf8),
        Field("field", "field", True, ("field_name", "field", "asset"), pl.Utf8),
        Field("reservoir", "reservoir", False, ("reservoir", "res", "zone", "formation"), pl.Utf8),
        Field("block", "block / pattern", False, ("block", "pattern", "sector", "area"), pl.Utf8),
        Field(
            "alloc_factor",
            "allocation factor (commingled)",
            False,
            ("alloc", "alloc_factor", "allocation", "split"),
            pl.Float64,
        ),
    ),
)

EVENTS = Dataset(
    name="events",
    label="Events",
    table_hints=("event", "workover", "intervention"),
    fields=(
        Field("well", "well id", True, _WELL_HINTS, pl.Utf8),
        Field("date", "date", True, ("date", "event_date", "start"), pl.Date),
        Field("type", "event type", True, ("type", "event", "event_type", "kind"), pl.Utf8),
        Field("note", "note", False, ("note", "comment", "remarks", "description"), pl.Utf8),
    ),
)

DATASETS: dict[str, Dataset] = {d.name: d for d in (RATES, PRESSURE, COORDS, CATEGORY, EVENTS)}

# Canonical unit tags per unit kind and unit system (§6)
DEFAULT_UNITS: dict[str, dict[str, str]] = {
    "field": {"rate": "bbl/d", "gas_rate": "Mscf/d", "pressure": "psi", "length": "ft"},
    "metric": {"rate": "m3/d", "gas_rate": "m3/d", "pressure": "kPa", "length": "m"},
}

# Internal storage units (SI-based; §6 "stores SI internally")
INTERNAL_UNITS: dict[str, str] = {
    "rate": "m3/d",
    "gas_rate": "m3/d",
    "pressure": "kPa",
    "length": "m",
}


@dataclass
class ColumnMapping:
    """Chosen source table and source→canonical column names for one dataset."""

    table: str | None = None
    columns: dict[str, str] = field(default_factory=dict)  # canonical field → source column
    units: dict[str, str] = field(default_factory=dict)  # canonical field → unit tag
    date_format: str | None = None
