"""Units service — architecture §6 (units, conventions, reservoir-volume conversion).

* every column carries a unit tag; ``convert`` moves a value between tags of the same kind;
  the loader converts once to the internal (SI-based) tags in ``schema.INTERNAL_UNITS``;
* reservoir volumes (Atlas §0 material balance is in reservoir barrels)::

      q_res = q_o·B_o + q_w·B_w + (q_g − q_o·R_s)·B_g        i_res = i_w·B_w

  with B_o, R_s, B_g from a PVT table at average reservoir pressure or constants (warning);
* free gas: producing GOR > R_s → the free-gas term is included and the run is flagged
  ``GOR_ABOVE_RS`` (reduced confidence; gas injection is out of scope, §20);
* datum shift: p_datum = p + γ·(D_datum − D_measured) with a fluid gradient γ;
* time base: volumes ÷ days_on give producing-day rates; calendar-day rates = volume ÷ Δt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from waterflood_app.messaging.conditions import ConditionCode, ConditionLog

FArray = npt.NDArray[np.float64]

BBL_PER_M3 = 6.289810770432105
FT_PER_M = 3.280839895013123
PSI_PER_KPA = 0.14503773773020923
SCF_PER_M3 = 35.31466672148859

# factor[tag] converts *from* tag *to* the canonical tag of its kind
_TO_CANONICAL: dict[str, dict[str, float]] = {
    "rate": {
        "m3/d": 1.0,
        "bbl/d": 1.0 / BBL_PER_M3,
        "stb/d": 1.0 / BBL_PER_M3,
        "m3/month": 1.0 / 30.436875,
        "bbl/month": 1.0 / (BBL_PER_M3 * 30.436875),
    },
    "volume": {
        "m3": 1.0,
        "bbl": 1.0 / BBL_PER_M3,
        "stb": 1.0 / BBL_PER_M3,
        "Mbbl": 1000.0 / BBL_PER_M3,
    },
    "gas_rate": {
        "m3/d": 1.0,
        "scf/d": 1.0 / SCF_PER_M3,
        "Mscf/d": 1000.0 / SCF_PER_M3,
        "MMscf/d": 1.0e6 / SCF_PER_M3,
        "sm3/d": 1.0,
    },
    "pressure": {
        "kPa": 1.0,
        "psi": 1.0 / PSI_PER_KPA,
        "psia": 1.0 / PSI_PER_KPA,
        "bar": 100.0,
        "MPa": 1000.0,
    },
    "length": {"m": 1.0, "ft": 1.0 / FT_PER_M},
    "gor": {"m3/m3": 1.0, "scf/stb": 1.0 / (SCF_PER_M3 / BBL_PER_M3)},
}

CANONICAL: dict[str, str] = {
    "rate": "m3/d",
    "volume": "m3",
    "gas_rate": "m3/d",
    "pressure": "kPa",
    "length": "m",
    "gor": "m3/m3",
}


def unit_kind(tag: str) -> str:
    for kind, table in _TO_CANONICAL.items():
        if tag in table:
            return kind
    raise ValueError(f"unknown unit tag {tag!r}")


def convert(value: FArray | float, from_tag: str, to_tag: str) -> FArray | float:
    """Convert between two unit tags of the same kind (exact, dimensionally checked)."""
    if from_tag == to_tag:
        return value
    kind = unit_kind(from_tag)
    if to_tag not in _TO_CANONICAL[kind]:
        raise ValueError(f"cannot convert {from_tag!r} to {to_tag!r} (different kinds)")
    factor = _TO_CANONICAL[kind][from_tag] / _TO_CANONICAL[kind][to_tag]
    return value * factor


def to_internal(value: FArray | float, from_tag: str) -> FArray | float:
    return convert(value, from_tag, CANONICAL[unit_kind(from_tag)])


# --------------------------------------------------------------------------------------
# PVT
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PVT:
    """Black-oil PVT. Constants, or a table (pressure-sorted) interpolated at p̄.

    Units are dimensionless (B_o, B_w, B_g in res-vol / surface-vol) and R_s in surface-gas /
    surface-oil volume — the caller keeps q_gas and R_s in the same gas/oil unit pair.
    """

    bo: float = 1.0
    bw: float = 1.0
    rs: float = 0.0
    bg: float = 0.0
    table_p: tuple[float, ...] = field(default_factory=tuple)
    table_bo: tuple[float, ...] = field(default_factory=tuple)
    table_rs: tuple[float, ...] = field(default_factory=tuple)
    table_bg: tuple[float, ...] = field(default_factory=tuple)
    p_bubble: float | None = None

    @property
    def is_table(self) -> bool:
        return len(self.table_p) > 1

    def at(self, p_bar: FArray | float) -> tuple[FArray, FArray, FArray]:
        """(B_o, R_s, B_g) at reservoir pressure(s) — interpolated if a table is present."""
        p = np.atleast_1d(np.asarray(p_bar, dtype=np.float64))
        if not self.is_table:
            return (np.full_like(p, self.bo), np.full_like(p, self.rs), np.full_like(p, self.bg))
        xp = np.asarray(self.table_p, dtype=np.float64)
        bo = np.interp(p, xp, np.asarray(self.table_bo, dtype=np.float64))
        rs = (
            np.interp(p, xp, np.asarray(self.table_rs, dtype=np.float64)) if self.table_rs else np.full_like(p, self.rs)
        )
        bg = (
            np.interp(p, xp, np.asarray(self.table_bg, dtype=np.float64)) if self.table_bg else np.full_like(p, self.bg)
        )
        return bo, rs, bg


def detect_free_gas(
    q_oil: FArray, q_gas: FArray | None, rs: FArray | float, rel_tol: float = 0.05
) -> npt.NDArray[np.bool_]:
    """Producing GOR > R_s (with tolerance) marks free-gas steps (§6). No gas column → never."""
    if q_gas is None:
        return np.zeros(np.shape(q_oil), dtype=bool)
    qo = np.asarray(q_oil, dtype=np.float64)
    qg = np.asarray(q_gas, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        gor = np.where(qo > 0, qg / np.where(qo > 0, qo, 1.0), 0.0)
    return np.asarray(gor > np.asarray(rs, dtype=np.float64) * (1.0 + rel_tol), dtype=bool)


def reservoir_production(
    q_oil: FArray,
    q_water: FArray,
    q_gas: FArray | None,
    pvt: PVT,
    p_bar: FArray | float | None = None,
    log: ConditionLog | None = None,
    scope: str = "field",
) -> tuple[FArray, npt.NDArray[np.bool_]]:
    """Total reservoir-volume production rate and the free-gas mask (§6).

    q_res = q_o·B_o + q_w·B_w + max(q_g − q_o·R_s, 0)·B_g. The free-gas term is only added
    where GOR exceeds R_s; below bubble point it is zero by construction.
    """
    qo = np.asarray(q_oil, dtype=np.float64)
    qw = np.asarray(q_water, dtype=np.float64)
    p = np.zeros_like(qo) if p_bar is None else np.broadcast_to(np.asarray(p_bar, dtype=np.float64), qo.shape)
    bo, rs, bg = pvt.at(p.ravel())
    bo, rs, bg = bo.reshape(qo.shape), rs.reshape(qo.shape), bg.reshape(qo.shape)
    if log is not None and not pvt.is_table:
        log.emit(ConditionCode.PVT_CONSTANTS_ASSUMED, scope=scope, bo=pvt.bo, bw=pvt.bw)
    gas_pvt_known = pvt.is_table or pvt.rs > 0 or pvt.bg > 0
    free = detect_free_gas(qo, q_gas, rs) if gas_pvt_known else np.zeros(qo.shape, dtype=bool)
    q_res = qo * bo + qw * pvt.bw
    if q_gas is not None and free.any():
        qg = np.asarray(q_gas, dtype=np.float64)
        q_res = q_res + np.where(free, np.maximum(qg - qo * rs, 0.0) * bg, 0.0)
        if log is not None:
            log.emit(ConditionCode.GOR_ABOVE_RS, scope=scope, steps=int(free.sum()))
    return q_res, free


def reservoir_injection(q_inj: FArray, pvt: PVT) -> FArray:
    """i_res = i_w·B_w (§6)."""
    return np.asarray(q_inj, dtype=np.float64) * pvt.bw


# --------------------------------------------------------------------------------------
# Datum and time base
# --------------------------------------------------------------------------------------
def datum_shift(p: FArray | float, depth: FArray | float, datum: float, gradient: float) -> FArray | float:
    """p at datum = p + γ·(D_datum − D_measured); depths positive downwards, γ in p-unit per length-unit."""
    return p + gradient * (datum - depth)


def mixture_gradient(water_cut: FArray | float, grad_water: float, grad_oil: float) -> FArray | float:
    """Water-cut-weighted static gradient used to shift ESP intake pressure to mid-perf (§8)."""
    wc = np.clip(np.asarray(water_cut, dtype=np.float64), 0.0, 1.0)
    return wc * grad_water + (1.0 - wc) * grad_oil


def calendar_day_rate(volume: FArray, dt_days: FArray) -> FArray:
    """volume per step ÷ step length. Volumes preserved; shut-in steps stay 0 with days_on = 0."""
    dt = np.asarray(dt_days, dtype=np.float64)
    return np.asarray(volume, dtype=np.float64) / np.where(dt > 0, dt, 1.0)


def producing_day_rate(volume: FArray, days_on: FArray) -> FArray:
    """volume ÷ days_on (0 where days_on = 0)."""
    d = np.asarray(days_on, dtype=np.float64)
    return np.where(d > 0, np.asarray(volume, dtype=np.float64) / np.where(d > 0, d, 1.0), 0.0)
