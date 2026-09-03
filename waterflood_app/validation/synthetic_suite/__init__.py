"""Synthetic ground-truth suite loader (§16 Tier 1).

``load_case("streak_5x4")`` returns the frozen Parquet tables and ``truth.json`` for one case;
``to_matrices`` turns the long rates table into the (time, injection, production) arrays that
pywaterflood and the in-house solvers consume. Role splitting for MIXED wells is the job of
``ingest.welltype`` (Milestone 1); here MIXED wells are handled only through the explicit
``injectors`` / ``producers`` lists in ``truth.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl

SUITE_DIR = Path(__file__).resolve().parent
CASES = (
    "streak_5x4",
    "streak_5x4_noise5",
    "aquifer_6x9",
    "converted_wells",
    "allocated_noisy",
    "sectored_60",
)

FArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Matrices:
    """Dense rate matrices on a common monthly grid."""

    time_days: FArray  # (M,) cumulative days from the first grid point
    dt_days: FArray  # (M,) Δt_n = t_n − t_{n−1} (Δt_0 = Δt_1)
    injection: FArray  # (M, Ni) calendar-day bbl/d
    production: FArray  # (M, Np) liquid, calendar-day bbl/d
    oil: FArray  # (M, Np)
    injectors: list[str]
    producers: list[str]


@dataclass(frozen=True)
class Case:
    name: str
    rates: pl.DataFrame
    coords: pl.DataFrame
    category: pl.DataFrame
    events: pl.DataFrame
    truth: dict[str, Any]
    pressure: pl.DataFrame | None
    extra: dict[str, pl.DataFrame]

    @property
    def injectors(self) -> list[str]:
        return list(self.truth["injectors"])

    @property
    def producers(self) -> list[str]:
        return list(self.truth["producers"])

    def f_matrix(self, injectors: list[str] | None = None, producers: list[str] | None = None) -> FArray:
        """True f_ij as an (Ni, Np) array in the given (default: truth) well order."""
        inj = injectors or self.injectors
        prod = producers or self.producers
        f = self.truth["f_ij"]
        return np.array([[f[i][p] for p in prod] for i in inj], dtype=np.float64)

    def tau_vector(self, producers: list[str] | None = None) -> FArray:
        prod = producers or self.producers
        return np.array([self.truth["tau_days"][p] for p in prod], dtype=np.float64)


def case_dir(name: str) -> Path:
    return SUITE_DIR / name


def load_case(name: str) -> Case:
    if name not in CASES:
        raise KeyError(f"unknown synthetic case {name!r}; known: {CASES}")
    d = case_dir(name)
    truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
    pressure = pl.read_parquet(d / "pressure.parquet") if (d / "pressure.parquet").exists() else None
    extra = {
        p.stem: pl.read_parquet(p)
        for p in d.glob("*.parquet")
        if p.stem not in {"rates", "coords", "category", "events", "pressure"}
    }
    return Case(
        name=name,
        rates=pl.read_parquet(d / "rates.parquet"),
        coords=pl.read_parquet(d / "coords.parquet"),
        category=pl.read_parquet(d / "category.parquet"),
        events=pl.read_parquet(d / "events.parquet"),
        truth=truth,
        pressure=pressure,
        extra=extra,
    )


def to_matrices(
    rates: pl.DataFrame,
    injectors: list[str],
    producers: list[str],
    steps: tuple[int, int] | None = None,
) -> Matrices:
    """Pivot the long rates table to dense matrices (missing well-months → 0)."""
    dates = sorted(rates.get_column("date").unique().to_list())
    if steps is not None:
        dates = dates[steps[0] : steps[1] + 1]
    n = len(dates)
    idx = {d: k for k, d in enumerate(dates)}
    sub = rates.filter(pl.col("date").is_in(dates))

    def pivot(col: str, wells: list[str]) -> FArray:
        out = np.zeros((n, len(wells)))
        widx = {w: k for k, w in enumerate(wells)}
        part = sub.filter(pl.col("well_id").is_in(wells)).select(["well_id", "date", col])
        for w, d, v in part.iter_rows():
            out[idx[d], widx[w]] = float(v)
        return out

    inj = pivot("q_inj", injectors)
    oil = pivot("q_oil", producers)
    water = pivot("q_water", producers)
    # Δt_n = t_n − t_{n−1}; Δt_0 copies Δt_1 (never used by the recursion)
    gaps = np.array([(dates[k] - dates[k - 1]).days for k in range(1, n)], dtype=np.float64)
    dt = np.concatenate([[gaps[0]], gaps]) if n > 1 else np.array([30.0])
    time_days = np.concatenate([[0.0], np.cumsum(gaps)])
    return Matrices(
        time_days=time_days,
        dt_days=dt,
        injection=inj,
        production=oil + water,
        oil=oil,
        injectors=list(injectors),
        producers=list(producers),
    )
