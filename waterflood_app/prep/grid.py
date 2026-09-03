"""Dense monthly grid — the data structure every L2/L3 step consumes (§7, §8).

Injection and production are always on the same grid, aligned by days-on (§8). Rates are
*calendar-day reservoir-volume* rates (m³/d internally): volume over the step ÷ Δt, so a
shut-in month has rate 0 **and** days_on 0, which the fit uses as a mask (§6 time base).

Time convention (DECISIONS.md, M0): Δt_n = t_n − t_{n−1}; i(n) is the injection over the
interval ending at t_n; q(n) the rate at t_n; Δt_0 := Δt_1 and is never used by the recursion.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl

from waterflood_app.ingest.units import PVT, reservoir_injection, reservoir_production
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog

FArray = npt.NDArray[np.float64]
BArray = npt.NDArray[np.bool_]


@dataclass
class Grid:
    dates: list[date]
    time_days: FArray  # (M,)
    dt_days: FArray  # (M,)
    injectors: list[str]  # entity ids
    producers: list[str]
    inj: FArray  # (M, Ni) reservoir calendar-day rate
    liq: FArray  # (M, Np) reservoir liquid calendar-day rate
    oil: FArray  # (M, Np) surface oil calendar-day rate
    water: FArray  # (M, Np) surface water calendar-day rate
    days_on_prod: FArray  # (M, Np)
    days_on_inj: FArray  # (M, Ni)
    bhp: FArray | None = None  # (M, Np) flowing pressure on the grid, None → constant-BHP mode
    xy_inj: FArray | None = None  # (Ni, 2)
    xy_prod: FArray | None = None  # (Np, 2)
    raw: dict[str, FArray] = field(default_factory=dict)  # pre-cleaning copies (§7)
    well_of_entity: dict[str, str] = field(default_factory=dict)

    # ---- shape helpers ---------------------------------------------------------------
    @property
    def n_steps(self) -> int:
        return len(self.dates)

    @property
    def n_inj(self) -> int:
        return len(self.injectors)

    @property
    def n_prod(self) -> int:
        return len(self.producers)

    @property
    def prod_mask(self) -> BArray:
        return np.asarray(self.days_on_prod > 0, dtype=bool)

    @property
    def water_cut(self) -> FArray:
        tot = self.oil + self.water
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(tot > 0, self.water / np.where(tot > 0, tot, 1.0), 0.0)

    def dt_mean(self) -> float:
        return float(np.mean(self.dt_days[1:])) if self.n_steps > 1 else float(self.dt_days[0])

    # ---- slicing ---------------------------------------------------------------------
    def subset(self, injectors: list[str] | None = None, producers: list[str] | None = None) -> Grid:
        ii = [self.injectors.index(w) for w in (injectors or self.injectors)]
        jj = [self.producers.index(w) for w in (producers or self.producers)]
        return replace(
            self,
            injectors=[self.injectors[k] for k in ii],
            producers=[self.producers[k] for k in jj],
            inj=self.inj[:, ii],
            liq=self.liq[:, jj],
            oil=self.oil[:, jj],
            water=self.water[:, jj],
            days_on_prod=self.days_on_prod[:, jj],
            days_on_inj=self.days_on_inj[:, ii],
            bhp=None if self.bhp is None else self.bhp[:, jj],
            xy_inj=None if self.xy_inj is None else self.xy_inj[ii],
            xy_prod=None if self.xy_prod is None else self.xy_prod[jj],
            raw={
                k: (v[:, ii] if v.shape[1] == self.n_inj and k.startswith("inj") else v[:, jj])
                for k, v in self.raw.items()
            },
        )

    def window(self, start: int, end: int) -> Grid:
        """Steps [start, end) with the time axis re-based to the window's first point."""
        sl = slice(start, end)
        t = self.time_days[sl] - self.time_days[start]
        dt = self.dt_days[sl].copy()
        if len(dt) > 1:
            dt[0] = dt[1]
        return replace(
            self,
            dates=self.dates[sl],
            time_days=t,
            dt_days=dt,
            inj=self.inj[sl],
            liq=self.liq[sl],
            oil=self.oil[sl],
            water=self.water[sl],
            days_on_prod=self.days_on_prod[sl],
            days_on_inj=self.days_on_inj[sl],
            bhp=None if self.bhp is None else self.bhp[sl],
            raw={k: v[sl] for k, v in self.raw.items()},
        )

    def active_entities(self, min_steps: int = 1) -> Grid:
        """Drop injectors / producers with fewer than ``min_steps`` active steps in this window."""
        inj_keep = [
            w
            for k, w in enumerate(self.injectors)
            if int((self.days_on_inj[:, k] > 0).sum()) >= min_steps and self.inj[:, k].sum() > 0
        ]
        prod_keep = [
            w
            for k, w in enumerate(self.producers)
            if int((self.days_on_prod[:, k] > 0).sum()) >= min_steps and self.liq[:, k].sum() > 0
        ]
        return self.subset(inj_keep, prod_keep)

    def distances(self) -> FArray | None:
        if self.xy_inj is None or self.xy_prod is None:
            return None
        return np.asarray(np.linalg.norm(self.xy_inj[:, None, :] - self.xy_prod[None, :, :], axis=2), dtype=np.float64)


# --------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------
def _time_axis(dates: list[date]) -> tuple[FArray, FArray]:
    n = len(dates)
    if n == 1:
        return np.array([0.0]), np.array([30.0])
    gaps = np.array([(dates[k] - dates[k - 1]).days for k in range(1, n)], dtype=np.float64)
    return np.concatenate([[0.0], np.cumsum(gaps)]), np.concatenate([[gaps[0]], gaps])


def build_grid(
    entity_rates: pl.DataFrame,
    pvt: PVT,
    coords: pl.DataFrame | None = None,
    log: ConditionLog | None = None,
    well_of_entity: dict[str, str] | None = None,
) -> Grid:
    """Pivot the role-split long table (entity, role, date, q_*, days_on) to dense matrices.

    Missing entity-months → 0 with days_on 0. Rates are converted to reservoir volumes here
    (§6): production q_o·B_o + q_w·B_w (+ free gas), injection i_w·B_w.
    """
    log = log if log is not None else ConditionLog()
    dates = sorted(entity_rates.get_column("date").unique().to_list())
    time_days, dt_days = _time_axis(dates)
    idx = {d: k for k, d in enumerate(dates)}
    injectors = sorted(entity_rates.filter(pl.col("role") == "I").get_column("entity").unique().to_list())
    producers = sorted(entity_rates.filter(pl.col("role") == "P").get_column("entity").unique().to_list())
    n = len(dates)

    def pivot(col: str, entities: list[str], role: str, fill_days: bool = False) -> FArray:
        out = np.zeros((n, len(entities)))
        widx = {w: k for k, w in enumerate(entities)}
        part = entity_rates.filter(pl.col("role") == role).select(["entity", "date", col])
        for w, d, v in part.iter_rows():
            if v is None:
                continue
            out[idx[d], widx[w]] = float(v)
        return out

    inj_s = pivot("q_inj", injectors, "I")
    oil = pivot("q_oil", producers, "P")
    water = pivot("q_water", producers, "P")
    gas = pivot("q_gas", producers, "P") if "q_gas" in entity_rates.columns else None
    days_p = pivot("days_on", producers, "P")
    days_i = pivot("days_on", injectors, "I")
    # days_on missing (null) → assume full step where there is rate
    dt_col = dt_days[:, None]
    days_p = np.where((days_p <= 0) & ((oil + water) > 0), np.broadcast_to(dt_col, oil.shape), days_p)
    days_i = np.where((days_i <= 0) & (inj_s > 0), np.broadcast_to(dt_col, inj_s.shape), days_i)

    liq, _free = reservoir_production(oil, water, gas if gas is not None and gas.any() else None, pvt, log=log)
    inj = reservoir_injection(inj_s, pvt)

    xy_i = xy_p = None
    woe = dict(well_of_entity or {})
    for e in injectors + producers:
        woe.setdefault(e, e.split("@")[0].split("#")[0])
    if coords is not None and {"well", "x", "y"} <= set(coords.columns):
        table = {
            str(w): (float(x), float(y))
            for w, x, y in coords.select(["well", "x", "y"]).iter_rows()
            if x is not None and y is not None
        }
        missing = [e for e in injectors + producers if woe[e] not in table]
        if missing:
            log.emit(
                ConditionCode.MISSING_COORDINATES,
                count=len(missing),
                examples=", ".join(sorted({woe[e] for e in missing})[:5]),
            )
        if not missing:
            xy_i = np.array([table[woe[e]] for e in injectors], dtype=np.float64).reshape(-1, 2)
            xy_p = np.array([table[woe[e]] for e in producers], dtype=np.float64).reshape(-1, 2)
    return Grid(
        dates=dates,
        time_days=time_days,
        dt_days=dt_days,
        injectors=injectors,
        producers=producers,
        inj=inj,
        liq=liq,
        oil=oil,
        water=water,
        days_on_prod=days_p,
        days_on_inj=days_i,
        bhp=None,
        xy_inj=xy_i,
        xy_prod=xy_p,
        raw={"inj": inj.copy(), "liq": liq.copy(), "oil": oil.copy(), "water": water.copy()},
        well_of_entity=woe,
    )
