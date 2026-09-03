"""Verification — architecture §9 "Score & rank" and §16 (blind R², MAPE, residual autocorrelation, plausibility).

All metrics are computed on producing days only (days_on > 0) when configured (§6 time base);
blind metrics use the held-out window (§7 split) with the model run in forecast mode.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.models.base import ModelParams
from waterflood_app.prep.grid import Grid
from waterflood_app.prep.split import Split

FArray = npt.NDArray[np.float64]
BArray = npt.NDArray[np.bool_]


def r2(obs: FArray, pred: FArray, mask: BArray) -> float:
    o, p = obs[mask], pred[mask]
    if len(o) < 2:
        return float("nan")
    ss_tot = float(((o - o.mean()) ** 2).sum())
    if ss_tot <= 0:
        return float("nan")
    return 1.0 - float(((o - p) ** 2).sum()) / ss_tot


def mape(obs: FArray, pred: FArray, mask: BArray) -> float:
    o, p = obs[mask], pred[mask]
    ok = o > 0
    if ok.sum() == 0:
        return float("nan")
    return float(np.mean(np.abs(p[ok] - o[ok]) / o[ok]) * 100.0)


def lag1_autocorr(resid: FArray, mask: BArray) -> float:
    r = resid[mask]
    if len(r) < 4:
        return 0.0
    r = r - r.mean()
    den = float((r * r).sum())
    return float((r[1:] * r[:-1]).sum() / den) if den > 0 else 0.0


def aicc(n: int, k: int, sse: float) -> float:
    """Corrected Akaike information criterion for Gaussian residuals (parsimony score, §9)."""
    if n <= 0 or sse <= 0:
        return float("inf")
    aic = n * np.log(sse / n) + 2 * k
    return float(aic + (2 * k * (k + 1)) / (n - k - 1)) if n - k - 1 > 0 else float("inf")


@dataclass
class ProducerMetrics:
    producer: str
    train_r2: float
    blind_r2: float
    blind_mape: float
    train_mape: float
    autocorr_lag1: float
    n_blind_points: int


@dataclass
class VerifyReport:
    variant: str
    per_producer: list[ProducerMetrics]
    blind_r2_field: float  # pooled over producers
    blind_r2_median: float
    blind_mape_median: float
    train_r2_field: float
    autocorr_median: float
    aicc: float
    n_train_points: int
    n_params: int
    plausible: bool
    plausibility_notes: list[str]

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        return d


def verify(
    grid: Grid,
    prediction: FArray,
    params: ModelParams,
    split: Split,
    variant: str,
    n_params: int,
    sse_train: float,
    cfg: Config,
    log: ConditionLog,
    tau_bounds: tuple[float, float] | None = None,
) -> VerifyReport:
    v = cfg.section("verify")
    mask = grid.prod_mask if bool(v["mape_producing_days_only"]) else np.ones_like(grid.prod_mask)
    train = np.zeros(grid.n_steps, dtype=bool)
    train[split.train] = True
    blind = ~train
    per: list[ProducerMetrics] = []
    for j, w in enumerate(grid.producers):
        obs, pred, mk = grid.liq[:, j], prediction[:, j], mask[:, j]
        resid = obs - pred
        pm = ProducerMetrics(
            producer=w,
            train_r2=r2(obs, pred, mk & train),
            blind_r2=r2(obs, pred, mk & blind),
            blind_mape=mape(obs, pred, mk & blind),
            train_mape=mape(obs, pred, mk & train),
            autocorr_lag1=lag1_autocorr(resid, mk & train),
            n_blind_points=int((mk & blind).sum()),
        )
        per.append(pm)
        if pm.autocorr_lag1 > float(v["autocorr_lag1_warn"]):
            log.emit(
                ConditionCode.RESIDUAL_AUTOCORRELATED,
                scope=f"well:{w}",
                well=w,
                rho=round(pm.autocorr_lag1, 2),
            )
        # per-producer R² alone is misleading when the blind window is flat (noise ≈ signal variance):
        # a producer is flagged only when its blind MAPE is also poor
        poor_mape = np.isfinite(pm.blind_mape) and pm.blind_mape > float(v["blind_mape_poor_pct"])
        if poor_mape and np.isfinite(pm.blind_r2) and pm.blind_r2 < 0.5:
            log.emit(
                ConditionCode.BLIND_FIT_POOR,
                scope=f"well:{w}",
                well=w,
                r2=round(pm.blind_r2, 2),
                mape=round(pm.blind_mape, 1),
            )
    pooled_blind = r2(grid.liq, prediction, mask & blind[:, None])
    pooled_train = r2(grid.liq, prediction, mask & train[:, None])
    notes: list[str] = []
    plausible = True
    if (params.f < -1e-9).any() or (params.f > 1 + 1e-9).any():
        notes.append("f_ij outside [0, 1]")
        plausible = False
    if (params.sum_f_per_injector > 1.0 + 1e-6).any():
        notes.append("Σ_j f_ij > 1 for an injector")
        plausible = False
    if (prediction[mask] < 0).any():
        notes.append("negative predicted rates")
        plausible = False
    if tau_bounds is not None:
        tau = np.ravel(params.tau)
        lo, hi = tau_bounds
        at_bound = (tau <= lo * 1.001) | (tau >= hi * 0.999)
        if at_bound.any():
            notes.append(f"τ at a bound for {int(at_bound.sum())} value(s)")
            for j, w in enumerate(grid.producers):
                tj = params.tau[j] if params.tau.ndim == 1 else params.tau[:, j]
                if ((np.atleast_1d(tj) <= lo * 1.001) | (np.atleast_1d(tj) >= hi * 0.999)).any():
                    log.emit(ConditionCode.TAU_AT_BOUND, scope=f"well:{w}", well=w)
    n_train_pts = int((mask & train[:, None]).sum())
    blind_r2s = np.array([p.blind_r2 for p in per])
    blind_mapes = np.array([p.blind_mape for p in per])
    return VerifyReport(
        variant=variant,
        per_producer=per,
        blind_r2_field=pooled_blind,
        blind_r2_median=float(np.nanmedian(blind_r2s)) if np.isfinite(blind_r2s).any() else float("nan"),
        blind_mape_median=float(np.nanmedian(blind_mapes)) if np.isfinite(blind_mapes).any() else float("nan"),
        train_r2_field=pooled_train,
        autocorr_median=float(np.median([p.autocorr_lag1 for p in per])) if per else 0.0,
        aicc=aicc(n_train_pts, n_params, sse_train),
        n_train_points=n_train_pts,
        n_params=n_params,
        plausible=plausible,
        plausibility_notes=notes,
    )
