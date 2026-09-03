"""Identifiability gates and the data profile — architecture §7, §8, §9 (Tournament Selector).

Gates: O_d = M/(I+1) > 6 · rate points ≥ 4× parameters · injection-signal CV ≥ 0.15 ·
τ/Δt ≥ 3. Every failure emits a Condition (§17) and changes which variants are eligible (§9).
The :class:`DataProfile` mirrors the Tournament Selector's profile object so the "Details"
panel can show the same numbers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.prep.grid import Grid


@dataclass
class DataProfile:
    n_steps: int
    n_inj: int
    n_prod: int
    dt_days: float
    od: float
    n_params_crmp: int
    n_params_crmip: int
    points_available: int
    ratio_crmp: float
    ratio_crmip: float
    inj_cv_min: float
    inj_cv_mean: float
    mean_water_cut: float
    events_per_well: float
    has_xy: bool
    has_bhp: bool
    has_relperm: bool
    monthly_allocated: bool
    tau_estimate_days: float | None = None
    sum_f_apparent: float | None = None
    pressure_source: str = "none"
    sum_f_per_producer: dict[str, float] = field(default_factory=dict)  # quick-CRMP apparent Σ_i f_ij
    sum_f_per_injector: dict[str, float] = field(default_factory=dict)  # quick-CRMP unconstrained Σ_j f_ij
    flank_wells: list[str] = field(default_factory=list)

    @property
    def tau_over_dt(self) -> float | None:
        return None if self.tau_estimate_days is None else self.tau_estimate_days / self.dt_days

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["tau_over_dt"] = self.tau_over_dt
        return d


def injection_cv(inj: np.ndarray, days_on: np.ndarray | None = None) -> np.ndarray:
    """Coefficient of variation per injector over its active steps."""
    out = np.zeros(inj.shape[1])
    for k in range(inj.shape[1]):
        x = inj[:, k]
        if days_on is not None:
            x = x[days_on[:, k] > 0]
        m = float(x.mean()) if len(x) else 0.0
        out[k] = float(x.std() / m) if m > 0 else 0.0
    return out


def n_params(n_inj: int, n_prod: int, has_bhp: bool) -> tuple[int, int]:
    """Parameter counts (Atlas CRMP tab §2): CRMP N_p(N_i+3) [+N_p with J], CRMIP 3·N_i·N_p [4· with J]."""
    crmp = n_prod * (n_inj + (4 if has_bhp else 3))
    crmip = (4 if has_bhp else 3) * n_inj * n_prod
    return crmp, crmip


def profile(
    grid: Grid,
    cfg: Config,
    events_per_well: float = 0.0,
    tau_estimate_days: float | None = None,
    sum_f_apparent: float | None = None,
    has_relperm: bool = False,
    pressure_source: str = "none",
    sum_f_per_producer: dict[str, float] | None = None,
    sum_f_per_injector: dict[str, float] | None = None,
) -> DataProfile:
    m, ni, npd = grid.n_steps, grid.n_inj, grid.n_prod
    has_bhp = grid.bhp is not None
    crmp, crmip = n_params(ni, npd, has_bhp)
    pts = int(grid.prod_mask.sum())
    cv = injection_cv(grid.inj, grid.days_on_inj)
    mask = grid.prod_mask
    wc = grid.water_cut
    mean_wc = float(wc[mask].mean()) if mask.any() else 0.0
    dt = grid.dt_mean()
    return DataProfile(
        n_steps=m,
        n_inj=ni,
        n_prod=npd,
        dt_days=dt,
        od=m / (ni + 1) if ni >= 0 else 0.0,
        n_params_crmp=crmp,
        n_params_crmip=crmip,
        points_available=pts,
        ratio_crmp=pts / crmp if crmp else 0.0,
        ratio_crmip=pts / crmip if crmip else 0.0,
        inj_cv_min=float(cv.min()) if len(cv) else 0.0,
        inj_cv_mean=float(cv.mean()) if len(cv) else 0.0,
        mean_water_cut=mean_wc,
        events_per_well=events_per_well,
        has_xy=grid.xy_inj is not None,
        has_bhp=has_bhp,
        has_relperm=has_relperm,
        monthly_allocated=dt >= 28.0,
        tau_estimate_days=tau_estimate_days,
        sum_f_apparent=sum_f_apparent,
        pressure_source=pressure_source,
        sum_f_per_producer=dict(sum_f_per_producer or {}),
        sum_f_per_injector=dict(sum_f_per_injector or {}),
    )


def flank_signature(sums: dict[str, float], sum_min: float, over_median_min: float) -> list[str]:
    """Producers whose apparent Σ_i f_ij exceeds ``sum_min`` and ``over_median_min`` × the median of the others."""
    if len(sums) < 3:
        return []
    out: list[str] = []
    for w, v in sums.items():
        others = [x for k, x in sums.items() if k != w]
        med = float(np.median(others)) if others else 0.0
        if v > sum_min and med > 0 and v > over_median_min * med:
            out.append(w)
    return out


def run_gates(p: DataProfile, cfg: Config, log: ConditionLog, scope: str = "field") -> dict[str, bool]:
    """Evaluate the §7/§9 gates on a profile, emit Conditions, return {gate: passed}."""
    g = cfg.section("gates")
    res: dict[str, bool] = {}
    res["history_min"] = p.n_steps >= int(g["min_steps_hard"])
    if not res["history_min"]:
        log.emit(ConditionCode.HISTORY_TOO_SHORT, scope=scope, steps=p.n_steps)
    elif p.n_steps < int(g["min_steps"]):
        log.emit(ConditionCode.HISTORY_SHORT, scope=scope, steps=p.n_steps)
    res["od"] = p.od > float(g["od_min"])
    if not res["od"]:
        log.emit(ConditionCode.OD_LOW, scope=scope, od=round(p.od, 1), threshold=g["od_min"])
    res["points_per_param"] = p.ratio_crmp >= float(g["points_per_param_min"])
    if not res["points_per_param"]:
        log.emit(ConditionCode.POINTS_PER_PARAM_LOW, scope=scope, ratio=p.ratio_crmp)
    res["crmip_data"] = p.ratio_crmip >= float(g["points_per_param_floor"])
    if not res["crmip_data"]:
        log.emit(ConditionCode.CRMIP_DATA_LOW, scope=scope, ratio=round(p.ratio_crmip, 1))
    res["inj_cv"] = p.inj_cv_min >= float(g["inj_cv_min"])
    if not res["inj_cv"]:
        log.emit(ConditionCode.INJ_CV_LOW, scope=scope, cv=round(p.inj_cv_min, 2))
    ratio = p.tau_over_dt
    res["tau_dt"] = ratio is None or ratio >= float(g["tau_over_dt_min"])
    if ratio is not None and not res["tau_dt"]:
        log.emit(ConditionCode.TAU_DT_LOW, scope=scope, ratio=round(ratio, 2))
    ratio = p.sum_f_apparent
    field_influx = ratio is not None and ratio > float(g["sum_f_aquifer"])
    max_inj = max(p.sum_f_per_injector.values(), default=0.0)
    injector_influx = max_inj > float(g["injector_sum_f_max"])
    p.flank_wells = flank_signature(
        p.sum_f_per_producer, float(g["flank_sum_f_min"]), float(g["flank_over_median_min"])
    )
    res["influx"] = field_influx or injector_influx
    if res["influx"]:
        log.emit(
            ConditionCode.SUM_F_HIGH,
            scope=scope,
            sum_f=round(max(ratio or 0.0, max_inj), 2),
            wells=", ".join(p.flank_wells),
        )
    res["event_heavy"] = p.events_per_well >= float(g["events_per_well_crossflow"])
    if res["event_heavy"] and not p.has_bhp:
        log.emit(ConditionCode.NO_BHP_EVENTS_HIGH, scope=scope, events=p.events_per_well)
    res["mature"] = p.mean_water_cut >= float(g["water_cut_two_phase_max"])
    res["long_history"] = p.n_steps >= int(g["history_ml_min"])
    res["has_xy"] = p.has_xy
    res["has_bhp"] = p.has_bhp
    res["has_relperm"] = p.has_relperm
    return res
