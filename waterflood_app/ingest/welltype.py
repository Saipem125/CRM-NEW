"""Well type derived from rates, not from a master table — architecture §5.

* per time step: production > 0 → P, injection > 0 → I, both → B (flagged), none → Z;
* well type = PROD or INJ if a single mode ever appears, else MIXED with conversion dates;
* MIXED wells are split into an injector-role and a producer-role *entity* so the CRM sees a
  converted well as two entities with a date boundary; conversions also go to the events table;
* commingled wells are split by the allocation factor in the category table (equal split with a
  warning when absent);
* IDs are normalised (case, whitespace, alias table) so the datasets join reliably.

Long-format canonical rates table columns: well, date, q_oil, q_water, q_gas, q_inj, days_on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from waterflood_app.messaging.conditions import ConditionCode, ConditionLog

PROD, INJ, BOTH, IDLE = "P", "I", "B", "Z"
ROLE_SUFFIX = {"P": "@P", "I": "@I"}


# --------------------------------------------------------------------------------------
# ID normalisation
# --------------------------------------------------------------------------------------
def normalise_id(raw: str, aliases: dict[str, str] | None = None) -> str:
    """Upper-case, trim, collapse inner whitespace, unify separators, then apply the alias table."""
    s = re.sub(r"\s+", " ", str(raw).strip()).upper()
    s = re.sub(r"\s*([-_/])\s*", r"\1", s)
    if aliases:
        norm_aliases = {normalise_id(k): v for k, v in aliases.items()}
        s = norm_aliases.get(s, s)
    return s


def normalise_ids(frame: pl.DataFrame, aliases: dict[str, str] | None = None, col: str = "well") -> pl.DataFrame:
    if col not in frame.columns:
        return frame
    return frame.with_columns(
        pl.col(col).map_elements(lambda v: normalise_id(v, aliases), return_dtype=pl.Utf8).alias(col)
    )


# --------------------------------------------------------------------------------------
# Per-step classification and well types
# --------------------------------------------------------------------------------------
@dataclass
class Conversion:
    date: date
    from_role: str  # "P" | "I"
    to_role: str


@dataclass
class WellType:
    well: str
    type: str  # PROD | INJ | MIXED | NONE
    conversions: list[Conversion] = field(default_factory=list)
    first: date | None = None
    last: date | None = None
    n_active: int = 0
    n_simultaneous: int = 0
    modes: list[tuple[date, str]] = field(default_factory=list)

    @property
    def roles(self) -> list[str]:
        return {"PROD": ["P"], "INJ": ["I"], "MIXED": ["P", "I"], "NONE": []}[self.type]


def classify_steps(rates: pl.DataFrame) -> pl.DataFrame:
    """Add a ``mode`` column (P/I/B/Z) per row from the rate columns."""
    prod = pl.col("q_oil").fill_null(0.0) + pl.col("q_water").fill_null(0.0)
    inj = pl.col("q_inj").fill_null(0.0)
    mode = (
        pl.when((prod > 0) & (inj > 0))
        .then(pl.lit(BOTH))
        .when(prod > 0)
        .then(pl.lit(PROD))
        .when(inj > 0)
        .then(pl.lit(INJ))
        .otherwise(pl.lit(IDLE))
    )
    return rates.with_columns(mode.alias("mode"))


def derive_well_types(rates: pl.DataFrame, log: ConditionLog | None = None) -> dict[str, WellType]:
    """PROD / INJ / MIXED (with conversion dates) / NONE per well, from the per-step modes (§5)."""
    steps = classify_steps(rates).sort(["well", "date"])
    out: dict[str, WellType] = {}
    for (well,), sub in steps.group_by("well", maintain_order=True):
        w = str(well)
        modes = list(zip(sub.get_column("date").to_list(), sub.get_column("mode").to_list(), strict=True))
        active = [(d, m) for d, m in modes if m != IDLE]
        seen = {m for _, m in active}
        has_p = PROD in seen or BOTH in seen
        has_i = INJ in seen or BOTH in seen
        wtype = "MIXED" if (has_p and has_i) else "PROD" if has_p else "INJ" if has_i else "NONE"
        conversions: list[Conversion] = []
        last_role: str | None = None
        for k, (d, m) in enumerate(active):
            if m == BOTH:
                # a simultaneous P+I step is the conversion month when the role changes after it
                nxt = next((mm for _, mm in active[k + 1 :] if mm in (PROD, INJ)), None)
                role = (
                    nxt
                    if (nxt is not None and last_role is not None and nxt != last_role)
                    else (last_role or nxt or PROD)
                )
            else:
                role = m
            if last_role is not None and role != last_role:
                conversions.append(Conversion(date=d, from_role=last_role, to_role=role))
            last_role = role
        n_b = sum(1 for _, m in active if m == BOTH)
        wt = WellType(
            well=w,
            type=wtype,
            conversions=conversions,
            first=active[0][0] if active else None,
            last=active[-1][0] if active else None,
            n_active=len(active),
            n_simultaneous=n_b,
            modes=modes,
        )
        out[w] = wt
        if log is not None:
            if n_b:
                log.emit(ConditionCode.SIMULTANEOUS_PI, scope=f"well:{w}", well=w, steps=n_b)
            for c in conversions:
                log.emit(
                    ConditionCode.CONVERSION_DETECTED,
                    scope=f"well:{w}",
                    well=w,
                    from_role="producer" if c.from_role == PROD else "injector",
                    to_role="producer" if c.to_role == PROD else "injector",
                    when=c.date.strftime("%Y-%m"),
                )
    return out


# --------------------------------------------------------------------------------------
# Role splitting
# --------------------------------------------------------------------------------------
def split_roles(rates: pl.DataFrame, types: dict[str, WellType]) -> pl.DataFrame:
    """Return a rates table with an ``entity`` and ``role`` column.

    PROD/INJ wells keep their id as entity. A MIXED well becomes ``<id>@P`` (rows with
    production) and ``<id>@I`` (rows with injection); a B step contributes to both entities.
    """
    steps = classify_steps(rates)
    parts: list[pl.DataFrame] = []
    zero_inj = {"q_inj": pl.lit(0.0)}
    zero_prod = {"q_oil": pl.lit(0.0), "q_water": pl.lit(0.0), "q_gas": pl.lit(0.0)}
    for w, wt in types.items():
        sub = steps.filter(pl.col("well") == w)
        if wt.type == "MIXED":
            prod_rows = sub.filter(pl.col("mode").is_in([PROD, BOTH])).with_columns(
                pl.lit(f"{w}{ROLE_SUFFIX['P']}").alias("entity"),
                pl.lit("P").alias("role"),
                **zero_inj,
            )
            inj_rows = sub.filter(pl.col("mode").is_in([INJ, BOTH])).with_columns(
                pl.lit(f"{w}{ROLE_SUFFIX['I']}").alias("entity"),
                pl.lit("I").alias("role"),
                **zero_prod,
            )
            parts.extend([prod_rows, inj_rows])
        elif wt.type in ("PROD", "INJ"):
            role = "P" if wt.type == "PROD" else "I"
            parts.append(sub.with_columns(pl.lit(w).alias("entity"), pl.lit(role).alias("role")))
    if not parts:
        return steps.head(0).with_columns(pl.lit("").alias("entity"), pl.lit("").alias("role"))
    cols = [c for c in steps.columns if c != "mode"] + ["entity", "role"]
    return pl.concat([p.select(cols) for p in parts]).sort(["entity", "date"])


def _role_name(role: str) -> str:
    return "producer" if role == PROD else "injector"


def conversion_events(types: dict[str, WellType]) -> pl.DataFrame:
    """Events rows (well, date, type, note) for every detected conversion (§5)."""
    rows = [
        {
            "well": w,
            "date": c.date,
            "type": "CONVERSION",
            "note": f"{_role_name(c.from_role)} to {_role_name(c.to_role)}",
        }
        for w, wt in types.items()
        for c in wt.conversions
    ]
    schema = {"well": pl.Utf8, "date": pl.Date, "type": pl.Utf8, "note": pl.Utf8}
    return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)


# --------------------------------------------------------------------------------------
# Commingled wells
# --------------------------------------------------------------------------------------
def split_commingled(
    rates: pl.DataFrame, category: pl.DataFrame | None, log: ConditionLog | None = None
) -> tuple[pl.DataFrame, dict[str, dict[str, float]]]:
    """Split rates of wells listed under several reservoirs by ``alloc_factor`` (equal split + warning if absent).

    Returns the split table (entity ids ``<well>#<reservoir>``) and the factors used per well.
    Wells in a single reservoir are returned unchanged.
    """
    if category is None or "reservoir" not in category.columns:
        return rates, {}
    multi = (
        category.filter(pl.col("reservoir").is_not_null())
        .group_by("well")
        .agg(pl.col("reservoir").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
        .get_column("well")
        .to_list()
    )
    if not multi:
        return rates, {}
    factors: dict[str, dict[str, float]] = {}
    parts = [rates.filter(~pl.col("well").is_in(multi))]
    rate_cols = [c for c in ("q_oil", "q_water", "q_gas", "q_inj") if c in rates.columns]
    for w in multi:
        rows = category.filter(pl.col("well") == w)
        reservoirs = rows.get_column("reservoir").to_list()
        if "alloc_factor" in rows.columns and rows.get_column("alloc_factor").null_count() == 0:
            raw = [float(v) for v in rows.get_column("alloc_factor").to_list()]
            tot = sum(raw) or 1.0
            fac = {r: v / tot for r, v in zip(reservoirs, raw, strict=True)}
        else:
            fac = dict.fromkeys(reservoirs, 1.0 / len(reservoirs))
            if log is not None:
                log.emit(
                    ConditionCode.COMMINGLED_EQUAL_SPLIT,
                    scope=f"well:{w}",
                    well=w,
                    count=len(reservoirs),
                )
        factors[w] = fac
        base = rates.filter(pl.col("well") == w)
        for r, f in fac.items():
            parts.append(
                base.with_columns([(pl.col(c) * f).alias(c) for c in rate_cols] + [pl.lit(f"{w}#{r}").alias("well")])
            )
    return pl.concat(parts).sort(["well", "date"]), factors
