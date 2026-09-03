"""Model tournament — architecture §9 and the Tournament Selector reference.

gates → eligible variants → fit all eligible (same split & seeds) → score (blind R², MAPE,
AICc, multi-start stability, plausibility, runtime) → composite → winner or score-weighted
ensemble of the top 3 → confidence badge (HIGH / MEDIUM / LOW, thresholds in config).

Registry (§9): CRMT · CRMP · CRMIP available now; CRM-Aquifer, two-phase, crossflow, MPI and
ML variants have their eligibility rules here (so exclusions are explained) and are fitted
from Milestone 2 onward (``VARIANT_NOT_AVAILABLE``). RNN fails the interpretability gate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.models.base import CRMModel, FitData, FitResult, ModelParams
from waterflood_app.models.crmip import CRMIP
from waterflood_app.models.crmp import CRMP
from waterflood_app.models.crmt import CRMT
from waterflood_app.models.solver import SolverSettings
from waterflood_app.models.uq import Spread, parameter_spread, select_members
from waterflood_app.models.verify import VerifyReport, verify
from waterflood_app.prep.gates import DataProfile

AVAILABLE = {"crmt", "crmp", "crmip"}
ALL_VARIANTS = [
    "mpi",
    "mlr",
    "crmt",
    "crmp",
    "crmip",
    "aquifer",
    "twophase",
    "crossflow",
    "pinn",
    "rnn",
]
LABELS = {
    "mpi": "MPI (geometric)",
    "mlr": "MLR / BMLR",
    "crmt": "CRMT (field tank)",
    "crmp": "CRMP (producer)",
    "crmip": "CRMIP (pair)",
    "aquifer": "CRM-Aquifer (CRMPA/AQ)",
    "twophase": "Two-phase coupled",
    "crossflow": "Crossflow / productivity-coeff.",
    "pinn": "PINN / residual ML",
    "rnn": "Pure RNN / LSTM",
}


@dataclass
class Eligibility:
    variant: str
    eligible: bool
    reason: str  # exclusion reason or suitability rationale (Tournament Selector wording)
    suitability: float = 0.0
    available: bool = True


def eligibility(p: DataProfile, gates: dict[str, bool], cfg: Config) -> list[Eligibility]:
    """Rules of the Tournament Selector (published applicability limits), one entry per variant."""
    g = cfg.section("gates")
    out: list[Eligibility] = []
    od, r_p, r_ip = p.od, p.ratio_crmp, p.ratio_crmip
    rich, influx, heavy, long_h, mature = (
        gates["inj_cv"],
        gates["influx"],
        gates["event_heavy"],
        gates["long_history"],
        gates["mature"],
    )
    # MPI
    out.append(
        Eligibility("mpi", False, "no well coordinates → influence matrix cannot be built", available=False)
        if not p.has_xy
        else Eligibility(
            "mpi",
            True,
            "usable as a geometric prior / sanity check on f_ij; not a history-match method itself",
            0.45,
            available=False,
        )
    )
    # MLR
    if od < float(g["od_regression_floor"]):
        out.append(
            Eligibility(
                "mlr",
                False,
                f"O_d={od:.1f} < 3 — regression hopelessly under-determined",
                available=False,
            )
        )
    else:
        out.append(
            Eligibility(
                "mlr",
                True,
                "cheap screening for barriers; no dynamics, superseded by CRM for forecasting",
                0.35 + (0.15 if od > float(g["od_min"]) else 0.0),
                available=False,
            )
        )
    # CRMT
    if p.n_steps < int(g["min_steps_hard"]):
        out.append(Eligibility("crmt", False, "fewer than 8 time steps — cannot fit even 2 parameters"))
    else:
        out.append(
            Eligibility(
                "crmt",
                True,
                "2 parameters, always identifiable; ideal first pass and fallback when data is thin",
                0.55 + (0.0 if rich else 0.1) + (0.15 if od < float(g["od_min"]) else 0.0),
            )
        )
    # CRMP
    if r_p < 1:
        out.append(
            Eligibility(
                "crmp",
                False,
                f"only {p.points_available} rate points for {p.n_params_crmp} parameters",
            )
        )
    else:
        s, why = 0.6, []
        if od > float(g["od_min"]):
            s += 0.15
            why.append(f"O_d={od:.1f}>6")
        else:
            s -= 0.15
            why.append(f"O_d={od:.1f}<6 → expect non-unique f_ij, sectorize")
        if r_p >= float(g["points_per_param_min"]):
            s += 0.1
            why.append("data ≥4× parameters")
        else:
            why.append(f"data only {r_p:.1f}× parameters")
        if rich:
            s += 0.05
            why.append("rich injection signal")
        else:
            s -= 0.1
            why.append("flat injection signal (CV<0.15) → weak identifiability")
        if influx:
            s -= 0.1
            why.append("Σf_ij>1 suggests influx — pair with aquifer variant")
        if heavy:
            s -= 0.1
            why.append("frequent events → allocations may drift")
        out.append(Eligibility("crmp", True, " · ".join(why), min(1.0, s)))
    # CRMIP
    if r_ip < 1:
        out.append(
            Eligibility(
                "crmip",
                False,
                f"{p.n_params_crmip} parameters exceed {p.points_available} rate points",
            )
        )
    elif r_ip < float(g["points_per_param_floor"]):
        out.append(Eligibility("crmip", False, f"data only {r_ip:.1f}× parameters (need ≥4×; 2× absolute floor)"))
    else:
        s, why = 0.45, []
        if r_ip >= float(g["points_per_param_min"]):
            s += 0.2
            why.append("enough data for pair-level τ")
        else:
            why.append(f"data {r_ip:.1f}× parameters — borderline")
        if rich:
            s += 0.1
        else:
            s -= 0.15
            why.append("flat signal hurts pair-level resolution most")
        why.append("use where CRMP residuals show pair structure")
        out.append(Eligibility("crmip", True, " · ".join(why), min(1.0, s)))
    # Aquifer
    if not influx:
        out.append(
            Eligibility(
                "aquifer",
                False,
                f"Σf_ij≈{(p.sum_f_apparent or 0):.2f} — no influx signature; "
                "extra aquifer parameters would be unidentifiable",
                available=False,
            )
        )
    elif r_p < float(g["points_per_param_floor"]):
        out.append(Eligibility("aquifer", False, "not enough data for CRMP + aquifer PV/J_aq", available=False))
    else:
        out.append(
            Eligibility(
                "aquifer",
                True,
                f"Σf_ij={(p.sum_f_apparent or 0):.2f}>1.15 indicates external support; "
                "explicit aquifer tank beats pseudo-injectors",
                0.8,
                available=False,
            )
        )
    # Two-phase
    if mature:
        out.append(
            Eligibility(
                "twophase",
                False,
                f"mean water cut {p.mean_water_cut:.2f} ≥ 0.5 — mobility stable, constant-τ CRM sufficient",
                available=False,
            )
        )
    elif not p.has_relperm:
        out.append(
            Eligibility(
                "twophase",
                False,
                "no rel-perm / fractional-flow curve loaded (required for saturation coupling)",
                available=False,
            )
        )
    else:
        out.append(
            Eligibility(
                "twophase",
                True,
                f"water cut {p.mean_water_cut:.2f} still rising → τ varies with mobility; "
                "power-law oil cut invalid here",
                0.78,
                available=False,
            )
        )
    # Crossflow
    if not heavy:
        out.append(
            Eligibility(
                "crossflow",
                False,
                f"{p.events_per_well:.0f} events/well — operating modes stable, classical f_ij holds",
                available=False,
            )
        )
    elif not p.has_bhp:
        out.append(
            Eligibility(
                "crossflow",
                False,
                "no BHP — pressure-driven crossflow term cannot be constrained",
                available=False,
            )
        )
    else:
        out.append(
            Eligibility(
                "crossflow",
                True,
                f"{p.events_per_well:.0f} shut-ins/chokes per well → allocation weights drift; "
                "pressure-based formulation is robust to it",
                0.72,
                available=False,
            )
        )
    # PINN
    if not long_h:
        out.append(
            Eligibility(
                "pinn",
                False,
                f"{p.n_steps} time steps < 120 — insufficient history to train a network without overfitting",
                available=False,
            )
        )
    elif od < float(g["od_min"]):
        out.append(
            Eligibility(
                "pinn",
                False,
                "base CRM not identifiable → nothing physical for the network to correct",
                available=False,
            )
        )
    else:
        out.append(
            Eligibility(
                "pinn",
                True,
                "long history available; run only if classical residuals show structure (autocorrelation)",
                0.5 + (0.15 if heavy else 0.0),
                available=False,
            )
        )
    out.append(
        Eligibility(
            "rnn",
            False,
            "fails interpretability gate — exposes no f_ij / τ for the optimizer (benchmark only)",
            available=False,
        )
    )
    return out


@dataclass
class Entry:
    variant: str
    label: str
    model: CRMModel
    fit: FitResult
    report: VerifyReport
    spread: Spread
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    rank: int = 0


@dataclass
class TournamentResult:
    eligibility: list[Eligibility]
    entries: list[Entry]  # ranked
    winner: Entry | None
    ensemble: list[tuple[Entry, float]]  # (entry, weight) — [(winner, 1.0)] when no ensemble
    confidence: str
    confidence_reasons: list[str]
    prediction: np.ndarray | None  # (M, Np) ensemble-weighted liquid prediction
    conditions: ConditionLog
    runtime_s: float

    @property
    def leaderboard(self) -> list[dict[str, Any]]:
        rows = [
            {
                "rank": e.rank,
                "variant": e.variant,
                "label": e.label,
                "score": round(e.score, 4),
                "blind_r2": e.report.blind_r2_field,
                "blind_mape": e.report.blind_mape_median,
                "aicc": e.report.aicc,
                "spread": e.spread.f_spread,
                "runtime_s": e.fit.runtime_s,
                "n_params": e.fit.n_params,
                **{f"c_{k}": round(v, 4) for k, v in e.components.items()},
            }
            for e in self.entries
        ]
        rows += [
            {
                "rank": None,
                "variant": el.variant,
                "label": LABELS[el.variant],
                "excluded": el.reason,
            }
            for el in self.eligibility
            if not el.eligible or not el.available
        ]
        return rows

    def params(self) -> ModelParams | None:
        return self.winner.fit.params if self.winner else None


def _score(entries: list[Entry], cfg: Config) -> None:
    w = cfg.section("tournament")["weights"]
    r2s = np.array([e.report.blind_r2_field if np.isfinite(e.report.blind_r2_field) else -1.0 for e in entries])
    mapes = np.array(
        [e.report.blind_mape_median if np.isfinite(e.report.blind_mape_median) else 100.0 for e in entries]
    )
    aiccs = np.array([e.report.aicc if np.isfinite(e.report.aicc) else 1e12 for e in entries])
    spreads = np.array([e.spread.f_spread for e in entries])
    runtimes = np.array([e.fit.runtime_s for e in entries])
    plaus = np.array([1.0 if e.report.plausible else 0.0 for e in entries])

    def unit(x: np.ndarray, higher_better: bool) -> np.ndarray:
        lo, hi = float(x.min()), float(x.max())
        if hi - lo < 1e-12:
            return np.ones_like(x)
        u = (x - lo) / (hi - lo)
        return u if higher_better else 1.0 - u

    c_r2 = np.clip(r2s, 0.0, 1.0)  # absolute: blind R² is meaningful on its own
    c_mape = np.clip(1.0 - mapes / 50.0, 0.0, 1.0)
    c_aicc = unit(aiccs, False)
    c_stab = np.clip(1.0 - spreads / 0.5, 0.0, 1.0)
    c_rt = unit(np.log1p(runtimes), False)
    for k, e in enumerate(entries):
        e.components = {
            "blind_r2": float(c_r2[k]),
            "mape": float(c_mape[k]),
            "aicc": float(c_aicc[k]),
            "stability": float(c_stab[k]),
            "plausibility": float(plaus[k]),
            "runtime": float(c_rt[k]),
        }
        e.score = float(sum(float(w[key]) * e.components[key] for key in w))


def confidence_badge(entry: Entry | None, conditions: ConditionLog, cfg: Config) -> tuple[str, list[str]]:
    """HIGH: blind R² ≥ 0.85, spread < 15 %, no gate warnings · MEDIUM: R² ≥ 0.7 or one warning · LOW otherwise (§9)."""
    c = cfg.section("confidence")
    if entry is None:
        return "LOW", ["no model could be fitted"]
    r2v = entry.report.blind_r2_field
    n_warn = len([x for x in conditions.warnings() if x.code not in (ConditionCode.CONFIDENCE_LOW,)])
    reasons: list[str] = []
    if (
        np.isfinite(r2v)
        and r2v >= float(c["high"]["blind_r2_min"])
        and entry.spread.f_spread < float(c["high"]["spread_max"])
        and n_warn <= int(c["high"]["max_warnings"])
        and entry.report.plausible
    ):
        return "HIGH", [
            f"blind R² {r2v:.2f}",
            f"parameter spread {entry.spread.f_spread:.0%}",
            "no gate warnings",
        ]
    if np.isfinite(r2v) and r2v < float(c["high"]["blind_r2_min"]):
        reasons.append(f"blind R² {r2v:.2f} below {c['high']['blind_r2_min']}")
    if entry.spread.f_spread >= float(c["high"]["spread_max"]):
        reasons.append(f"parameter spread {entry.spread.f_spread:.0%}")
    if n_warn:
        reasons.append(f"{n_warn} gate warning(s)")
    if not entry.report.plausible:
        reasons.append("plausibility check failed: " + "; ".join(entry.report.plausibility_notes))
    medium_ok = (
        np.isfinite(r2v)
        and r2v >= float(c["medium"]["blind_r2_min"])
        and n_warn <= int(c["medium"]["max_warnings"])
        and entry.report.plausible
    ) or (np.isfinite(r2v) and r2v >= float(c["high"]["blind_r2_min"]) and n_warn <= int(c["medium"]["max_warnings"]))
    if medium_ok:
        return "MEDIUM", reasons
    conditions.emit(ConditionCode.CONFIDENCE_LOW)
    return "LOW", reasons


def run_tournament(
    data: FitData,
    profile: DataProfile,
    gates: dict[str, bool],
    cfg: Config,
    log: ConditionLog,
    seed: int = 0,
    engine: str = "inhouse",
    variants: list[str] | None = None,
) -> TournamentResult:
    t0 = time.perf_counter()
    grid = data.grid
    elig = eligibility(profile, gates, cfg)
    for el in elig:
        if el.eligible and not el.available:
            log.emit(ConditionCode.VARIANT_NOT_AVAILABLE, variant=LABELS[el.variant])
    to_fit = [
        el.variant for el in elig if el.eligible and el.available and (variants is None or el.variant in variants)
    ]
    settings = SolverSettings.from_config(cfg, float(np.min(grid.dt_days[1:])) if grid.n_steps > 1 else 30.0)
    entries: list[Entry] = []
    for v in to_fit:
        model: CRMModel = CRMT(cfg) if v == "crmt" else CRMP(cfg, engine) if v == "crmp" else CRMIP(cfg, engine)
        res = model.fit(data, seed=seed)
        vlog = ConditionLog()
        report = verify(
            grid,
            res.prediction,
            res.params,
            data.split,
            v,
            res.n_params,
            res.sse_train,
            cfg,
            vlog,
            (settings.tau_min, settings.tau_max),
        )
        spread = parameter_spread(select_members(res, cfg), None, cfg)
        entries.append(Entry(v, LABELS[v], model, res, report, spread))
    if entries:
        _score(entries, cfg)
        entries.sort(key=lambda e: (-e.score, e.fit.n_params))  # tie → simpler
        for k, e in enumerate(entries):
            e.rank = k + 1
    winner = entries[0] if entries else None
    ens: list[tuple[Entry, float]] = []
    prediction = None
    if winner is not None:
        gap = float(cfg["tournament.ensemble_if_score_gap_below"])
        top = [
            e
            for e in entries[: int(cfg["tournament.ensemble_top_n"])]
            if winner.score - e.score <= gap and e.report.plausible
        ]
        if len(top) > 1:
            ws = np.array([max(e.score, 1e-9) for e in top])
            ws = ws / ws.sum()
            ens = list(zip(top, ws.tolist(), strict=True))
            prediction = np.sum([w * e.fit.prediction for e, w in ens], axis=0)
        else:
            ens = [(winner, 1.0)]
            prediction = winner.fit.prediction
        # winner's verification conditions join the run log; spread warning from the winner
        verify(
            grid,
            winner.fit.prediction,
            winner.fit.params,
            data.split,
            winner.variant,
            winner.fit.n_params,
            winner.fit.sse_train,
            cfg,
            log,
            (settings.tau_min, settings.tau_max),
        )
        parameter_spread(select_members(winner.fit, cfg), log, cfg)
    badge, reasons = confidence_badge(winner, log, cfg)
    return TournamentResult(elig, entries, winner, ens, badge, reasons, prediction, log, time.perf_counter() - t0)
