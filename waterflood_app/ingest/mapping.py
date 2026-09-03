"""Column mapping — architecture §5 (auto-suggested from naming conventions, confirmed by the user).

``suggest_mapping`` reproduces the Data Loader reference: exact hint match first, then substring
match, per canonical field. ``apply_mapping`` renames, parses dates, converts units to the
internal tags and reports every problem as a Condition (no silent drops, §5 engineering rules).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import polars as pl

from waterflood_app.ingest import schema
from waterflood_app.ingest.units import CANONICAL, convert, unit_kind
from waterflood_app.ingest.welltype import normalise_ids
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%b-%y",
    "%Y%m",
    "%Y-%m",
    "%d.%m.%Y",
)


def suggest_table(dataset: schema.Dataset, tables: list[str]) -> str | None:
    """Pick the source table whose name contains one of the dataset's hints (Loader reference)."""
    low = {t: t.lower() for t in tables}
    for hint in dataset.table_hints:
        for t, tl in low.items():
            if hint in tl:
                return t
    return None


def suggest_column(fld: schema.Field, columns: list[str]) -> str | None:
    low = {c: c.lower().strip() for c in columns}
    for hint in fld.hints:
        for c, cl in low.items():
            if cl == hint:
                return c
    for hint in fld.hints:
        for c, cl in low.items():
            if hint in cl:
                return c
    return None


def suggest_mapping(dataset: schema.Dataset, columns: list[str], unit_system: str = "field") -> schema.ColumnMapping:
    m = schema.ColumnMapping()
    used: set[str] = set()
    for fld in dataset.fields:
        col = suggest_column(fld, [c for c in columns if c not in used])
        if col is not None:
            m.columns[fld.name] = col
            used.add(col)
            if fld.unit_kind:
                m.units[fld.name] = schema.DEFAULT_UNITS[unit_system][fld.unit_kind]
    return m


def validate_mapping(dataset: schema.Dataset, mapping: schema.ColumnMapping, log: ConditionLog) -> bool:
    ok = True
    for fld in dataset.fields:
        if fld.required and fld.name not in mapping.columns:
            log.emit(
                ConditionCode.UNMAPPED_REQUIRED_COLUMN,
                scope=f"dataset:{dataset.name}",
                dataset=dataset.name,
                field=fld.name,
            )
            ok = False
    if dataset.name == "rates" and not any(k in mapping.columns for k in ("q_oil", "q_water", "q_inj")):
        log.emit(
            ConditionCode.UNMAPPED_REQUIRED_COLUMN,
            scope="dataset:rates",
            dataset="rates",
            field="q_oil / q_water / q_inj",
        )
        ok = False
    return ok


# --------------------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------------------
def _parse_date(v: Any, fmt: str | None) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    fmts = (fmt,) if fmt else _DATE_FORMATS
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


@dataclass
class AppliedDataset:
    name: str
    frame: pl.DataFrame
    units: dict[str, str] = field(default_factory=dict)


def apply_mapping(
    dataset: schema.Dataset,
    source: pl.DataFrame,
    mapping: schema.ColumnMapping,
    log: ConditionLog,
    aliases: dict[str, str] | None = None,
) -> AppliedDataset:
    """Rename to canonical names, parse dates, normalise IDs, convert to internal units."""
    validate_mapping(dataset, mapping, log)
    cols: dict[str, pl.Expr] = {}
    for fld in dataset.fields:
        src = mapping.columns.get(fld.name)
        if src is None or src not in source.columns:
            continue
        cols[fld.name] = pl.col(src).alias(fld.name)
    frame = source.select(list(cols.values()))
    # dates
    if "date" in frame.columns:
        parsed = [_parse_date(v, mapping.date_format) for v in frame.get_column("date").to_list()]
        bad = [
            str(v)
            for v, p in zip(frame.get_column("date").to_list(), parsed, strict=True)
            if p is None and v is not None
        ]
        if bad:
            log.emit(
                ConditionCode.UNPARSEABLE_DATES,
                scope=f"dataset:{dataset.name}",
                dataset=dataset.name,
                count=len(bad),
                examples=", ".join(sorted(set(bad))[:3]),
            )
        frame = frame.with_columns(pl.Series("date", parsed, dtype=pl.Date)).filter(pl.col("date").is_not_null())
    # numeric columns and units
    units_out: dict[str, str] = {}
    for fld in dataset.fields:
        if fld.name not in frame.columns or fld.dtype not in (pl.Float64,):
            continue
        frame = frame.with_columns(pl.col(fld.name).cast(pl.Float64, strict=False))
        tag = mapping.units.get(fld.name)
        if fld.unit_kind and tag:
            if tag.endswith("/month"):
                # monthly volume → calendar-day rate with the actual month length, then unit convert
                days = frame.get_column("date").map_elements(
                    lambda d: float(calendar.monthrange(d.year, d.month)[1]),
                    return_dtype=pl.Float64,
                )
                frame = frame.with_columns((pl.col(fld.name) / days).alias(fld.name))
                tag = tag.replace("/month", "/d")
            target = CANONICAL[unit_kind(tag)]
            factor = float(convert(1.0, tag, target))
            frame = frame.with_columns((pl.col(fld.name) * factor).alias(fld.name))
            units_out[fld.name] = target
    # ids
    if "well" in frame.columns:
        frame = frame.with_columns(pl.col("well").cast(pl.Utf8))
        frame = normalise_ids(frame, aliases)
    # rates: fill missing numeric rate columns with nulls → zeros later, ensure days_on exists
    if dataset.name == "rates":
        for c in ("q_oil", "q_water", "q_gas", "q_inj"):
            if c not in frame.columns:
                frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias(c))
        if "days_on" not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("days_on"))
    return AppliedDataset(name=dataset.name, frame=frame, units=units_out)


def report_unmatched_ids(rates: pl.DataFrame, other: pl.DataFrame, name: str, log: ConditionLog) -> list[str]:
    """IDs present in ``other`` but not in the rates table (§6 naming) — reported, never dropped silently."""
    if "well" not in other.columns:
        return []
    known = set(rates.get_column("well").unique().to_list())
    missing = sorted(set(other.get_column("well").unique().to_list()) - known)
    if missing:
        log.emit(
            ConditionCode.UNMATCHED_WELL_IDS,
            scope=f"dataset:{name}",
            dataset=name,
            count=len(missing),
            examples=", ".join(missing[:5]),
        )
    return missing
