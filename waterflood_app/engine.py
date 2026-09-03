"""Engine driver — L1 → L2 → L3 for one project (architecture §4, §5–§9).

``run_engine`` takes loaded canonical tables and returns everything the Details panel, the
optimizer (M2) and the tests need: grid, well types, windows, sectors, gates, tournament per
(window, sector), oil-cut fits, and the run's data / config hashes (§1 reproducibility).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl

from waterflood_app.config import Config, load_config, seed_from_hash
from waterflood_app.ingest.connectors import LoadedData
from waterflood_app.ingest.units import PVT
from waterflood_app.ingest.welltype import (
    WellType,
    conversion_events,
    derive_well_types,
    split_commingled,
    split_roles,
)
from waterflood_app.messaging.conditions import ConditionLog
from waterflood_app.models.aquifer import static_pressure_on_grid
from waterflood_app.models.base import FitData
from waterflood_app.models.crmt import CRMT
from waterflood_app.models.fractional_flow import PowerLawOilCut, cumulative_basis, fit_power_law
from waterflood_app.models.solver import SolverSettings, fit_field
from waterflood_app.models.tournament import TournamentResult, run_tournament
from waterflood_app.models.twophase import RelPerm
from waterflood_app.prep.clean import clean_grid
from waterflood_app.prep.events import (
    build_windows,
    events_per_well,
    lift_events_per_producer,
    long_shutins,
)
from waterflood_app.prep.gates import DataProfile, profile, run_gates
from waterflood_app.prep.grid import Grid, build_grid
from waterflood_app.prep.pressure_prep import PressurePrep, prepare_pressure
from waterflood_app.prep.sectors import Sector, blocks_from_category, sectorize
from waterflood_app.prep.split import train_blind_split


@dataclass
class SectorRun:
    window_index: int
    sector: Sector
    grid: Grid
    profile: DataProfile
    gates: dict[str, bool]
    tournament: TournamentResult
    oil_cut: dict[str, PowerLawOilCut]
    oil_prediction: np.ndarray | None
    conditions: ConditionLog

    @property
    def key(self) -> str:
        return f"w{self.window_index}:{self.sector.id}"


@dataclass
class RunResult:
    grid: Grid
    well_types: dict[str, WellType]
    windows: list[Any]
    sectors: list[SectorRun]
    pressure: PressurePrep
    conditions: ConditionLog
    data_hash: str
    config_hash: str
    seed: int
    runtime_s: float
    events: pl.DataFrame | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def latest(self) -> list[SectorRun]:
        """Sector runs of the last window (the parameters the forecast/optimizer use, §10)."""
        last = max(s.window_index for s in self.sectors) if self.sectors else 0
        return [s for s in self.sectors if s.window_index == last]

    @property
    def confidence(self) -> str:
        order = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
        badges = [s.tournament.confidence for s in self.latest()]
        return min(badges, key=lambda b: order[b]) if badges else "LOW"

    def summary(self) -> dict[str, Any]:
        return {
            "data_hash": self.data_hash,
            "config_hash": self.config_hash,
            "seed": self.seed,
            "runtime_s": round(self.runtime_s, 2),
            "n_windows": len(self.windows),
            "confidence": self.confidence,
            "sectors": [
                {
                    "key": s.key,
                    "injectors": s.sector.injectors,
                    "producers": s.sector.producers,
                    "winner": s.tournament.winner.variant if s.tournament.winner else None,
                    "confidence": s.tournament.confidence,
                    "blind_r2": s.tournament.winner.report.blind_r2_field if s.tournament.winner else None,
                    "leaderboard": s.tournament.leaderboard,
                }
                for s in self.sectors
            ],
            "conditions": self.conditions.to_list(),
        }


def data_hash(loaded: LoadedData) -> str:
    h = hashlib.sha256()
    for name in ("rates", "pressure", "coords", "category", "events"):
        f = loaded.frame(name)
        if f is not None:
            h.update(name.encode())
            h.update(f.sort(f.columns).write_csv().encode("utf-8"))
    return h.hexdigest()


def _sum_f_apparent(grid: Grid, n_train: int) -> float:
    """Apparent support ratio ΣQ/ΣI over the training window (DECISIONS.md M0: the aquifer signature)."""
    q = float((grid.liq[:n_train] * grid.dt_days[:n_train, None]).sum())
    i = float((grid.inj[:n_train] * grid.dt_days[:n_train, None]).sum())
    return q / i if i > 0 else float("inf")


def run_engine(
    loaded: LoadedData,
    cfg: Config | None = None,
    pvt: PVT | None = None,
    seed: int | None = None,
    engine: str = "inhouse",
    variants: list[str] | None = None,
    datum: float | None = None,
    relperm: RelPerm | None = None,
) -> RunResult:
    t0 = time.perf_counter()
    cfg = cfg or load_config()
    pvt = pvt or PVT()
    log = ConditionLog()
    log.extend(loaded.conditions)
    dh = data_hash(loaded)
    ch = cfg.hash
    seed = seed_from_hash(ch + dh) if seed is None else seed

    # ---- L1: well types, roles, commingled split
    rates, _factors = split_commingled(loaded.rates, loaded.category, log)
    types = derive_well_types(rates, log)
    entity_rates = split_roles(rates, types)
    events = loaded.events
    conv = conversion_events(types)
    if not conv.is_empty():
        events = (
            pl.concat([events.select(conv.columns), conv])
            if events is not None and set(conv.columns) <= set(events.columns)
            else conv
            if events is None
            else events
        )
    grid = build_grid(entity_rates, pvt, loaded.coords, log)
    # ---- L2: clean, pressure, windows, sectors
    grid = clean_grid(grid, cfg, log)
    pprep = prepare_pressure(loaded.pressure, grid, cfg, log, loaded.coords, datum)
    grid = pprep.apply(grid)
    long_shutins(grid, cfg, log)
    windows = build_windows(grid, events, cfg, log)
    blocks = blocks_from_category(loaded.category)
    n_wells_total = grid.n_inj + grid.n_prod
    lift_counts = lift_events_per_producer(events, grid.producers, grid.well_of_entity)
    runs: list[SectorRun] = []
    for wi, win in enumerate(windows):
        wgrid = grid.window(win.start, win.end).active_entities(min_steps=3)
        if wgrid.n_inj == 0 or wgrid.n_prod == 0:
            continue
        for sector in sectorize(wgrid, cfg, blocks):
            sgrid = wgrid.subset(sector.injectors, sector.producers)
            slog = ConditionLog()
            split = train_blind_split(sgrid.n_steps, cfg)
            data = FitData(sgrid, split)
            # quick CRMT for the τ estimate (§8 gate) — cheap, 2 starts
            tau_est: float | None = None
            try:
                crmt = CRMT(cfg)
                crmt.fit(data, seed=seed, n_starts=2)
                tau_est = crmt.tau_field
            except Exception:
                tau_est = None
            sums_p, sums_i = _quick_sum_f(data, cfg, seed)
            ev_per_well = events_per_well(events, n_wells_total) if events is not None else 0.0
            prof = profile(
                sgrid,
                cfg,
                has_relperm=relperm is not None,
                events_per_well=max(ev_per_well, float(lift_counts.mean()) if len(lift_counts) else 0.0),
                tau_estimate_days=tau_est,
                sum_f_apparent=_sum_f_apparent(sgrid, split.n_train),
                pressure_source=pprep.field_source,
                sum_f_per_producer=sums_p,
                sum_f_per_injector=sums_i,
            )
            gates = run_gates(prof, cfg, slog, scope=f"sector:{sector.id}")
            if not gates["history_min"]:
                log.extend(slog)
                continue
            sp = static_pressure_on_grid(sgrid, pprep.static_surveys)
            tres = run_tournament(
                data,
                prof,
                gates,
                cfg,
                slog,
                seed=seed,
                engine=engine,
                variants=variants,
                static_pressure=sp,
                relperm=relperm,
            )
            oil_cut, oil_pred = _fit_oil_cut(sgrid, tres, split.n_train)
            log.extend(slog)
            runs.append(SectorRun(wi, sector, sgrid, prof, gates, tres, oil_cut, oil_pred, slog))
    return RunResult(
        grid,
        types,
        windows,
        runs,
        pprep,
        log,
        dh,
        ch,
        seed,
        time.perf_counter() - t0,
        events,
        {"engine": engine},
    )


def _quick_sum_f(data: FitData, cfg: Config, seed: int) -> tuple[dict[str, float], dict[str, float]]:
    """Apparent Σ_i f_ij per producer and unconstrained Σ_j f_ij per injector from a quick CRMP.

    Two starts, tied primary, no per-injector coupling and a distance mask (pairs farther than
    ``solver.quick_fit_distance_factor`` × the median nearest I–P distance are off), so external
    support has to show up as allocation — the §9 "Σf_ij > 1.15" signature.
    """
    grid = data.grid
    try:
        s = SolverSettings.from_config(cfg, float(np.min(grid.dt_days[1:])) if grid.n_steps > 1 else 30.0)
        s.n_starts = 2
        s.free_primary = False
        s.joint_sum_bound = 1e9
        allowed = None
        d = grid.distances()
        factor = cfg.get("solver.quick_fit_distance_factor")
        if d is not None and factor:
            nearest = np.concatenate([d.min(axis=1), d.min(axis=0)])
            allowed = d <= float(factor) * float(np.median(nearest))
            allowed |= d <= d.min(axis=0, keepdims=True)  # every producer keeps its nearest injector
        res = fit_field(data, "crmp", s, seed, allowed=allowed)
        prod = dict(zip(grid.producers, res.params.sum_f_per_producer.tolist(), strict=True))
        inj = dict(zip(grid.injectors, res.params.sum_f_per_injector.tolist(), strict=True))
        return prod, inj
    except Exception:
        return {}, {}


def _fit_oil_cut(
    grid: Grid, tres: TournamentResult, n_train: int
) -> tuple[dict[str, PowerLawOilCut], np.ndarray | None]:
    """Power-law WOR–CWI per producer on the winner's allocated-water basis (Atlas CRMP §2)."""
    if tres.winner is None or tres.prediction is None:
        return {}, None
    p = tres.winner.fit.params
    support = grid.inj @ p.f  # (M, Np) allocated water
    fits: dict[str, PowerLawOilCut] = {}
    oil_pred = np.zeros_like(tres.prediction)
    mask = grid.prod_mask
    train = np.zeros(grid.n_steps, dtype=bool)
    train[:n_train] = True
    for j, w in enumerate(grid.producers):
        cwi = cumulative_basis(tres.prediction[:, j], support[:, j], grid.dt_days, "allocated_water")
        fit = fit_power_law(
            cwi,
            grid.oil[:, j],
            grid.oil[:, j] + grid.water[:, j],
            mask[:, j] & train,
            "allocated_water",
        )
        fits[w] = fit
        # oil = liquid × f_o, with liquid converted back from reservoir to surface via the observed ratio
        with np.errstate(divide="ignore", invalid="ignore"):
            surf_ratio = np.where(
                grid.liq[:, j] > 0,
                (grid.oil[:, j] + grid.water[:, j]) / np.where(grid.liq[:, j] > 0, grid.liq[:, j], 1.0),
                np.nan,
            )
        ratio = float(np.nanmedian(surf_ratio[mask[:, j]])) if np.isfinite(surf_ratio[mask[:, j]]).any() else 1.0
        oil_pred[:, j] = tres.prediction[:, j] * ratio * fit.oil_cut(cwi)
    return fits, oil_pred
