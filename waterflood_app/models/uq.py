"""Uncertainty from the multi-start ensemble — architecture §9 (parameter spread), §13 (P10/P50/P90).

The ensemble is every multi-start solution whose training SSE is within
``uq.ensemble_keep_within`` of the best (per producer). Parameter spread is the relative
standard deviation of f_ij over members, weighted by f_ij (large pairs matter, barriers do
not); τ spread likewise. Spread > ``uq.spread_warn`` raises MULTISTART_SPREAD_HIGH (§17).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.models.base import FitResult, ModelParams

FArray = npt.NDArray[np.float64]


@dataclass
class Spread:
    f_spread: float  # weighted relative std of f_ij
    tau_spread: float
    n_members: int
    f_p10: FArray
    f_p50: FArray
    f_p90: FArray

    def to_dict(self) -> dict[str, object]:
        return {
            "f_spread": self.f_spread,
            "tau_spread": self.tau_spread,
            "n_members": self.n_members,
        }


def select_members(result: FitResult, cfg: Config) -> list[ModelParams]:
    tol = float(cfg["uq.ensemble_keep_within"])
    if not result.ensemble:
        return [result.params]
    best = min(result.starts_sse) if result.starts_sse else 0.0
    members = [m for m, s in zip(result.ensemble, result.starts_sse, strict=False) if s <= best * (1.0 + tol) + 1e-12]
    return members or [result.params]


def parameter_spread(
    members: list[ModelParams],
    log: ConditionLog | None = None,
    cfg: Config | None = None,
    scope: str = "field",
) -> Spread:
    F = np.stack([m.f for m in members])  # (K, Ni, Np)
    T = np.stack([np.ravel(m.tau_per_producer()) for m in members])
    f_mean = F.mean(axis=0)
    f_std = F.std(axis=0)
    w = np.maximum(f_mean, 0.0)
    f_spread = float((f_std * w).sum() / w.sum()) if w.sum() > 0 else 0.0
    f_spread = f_spread / max(float((f_mean * w).sum() / w.sum()), 1e-9) if w.sum() > 0 else 0.0
    t_mean = np.maximum(T.mean(axis=0), 1e-9)
    tau_spread = float(np.mean(T.std(axis=0) / t_mean)) if T.shape[0] > 1 else 0.0
    sp = Spread(
        f_spread=f_spread,
        tau_spread=tau_spread,
        n_members=len(members),
        f_p10=np.percentile(F, 10, axis=0),
        f_p50=np.percentile(F, 50, axis=0),
        f_p90=np.percentile(F, 90, axis=0),
    )
    if log is not None and cfg is not None and sp.f_spread > float(cfg["uq.spread_warn"]):
        log.emit(ConditionCode.MULTISTART_SPREAD_HIGH, scope=scope, spread=round(sp.f_spread, 2))
    return sp


def prediction_band(predictions: list[FArray]) -> tuple[FArray, FArray, FArray]:
    """P10 / P50 / P90 of a list of (M, Np) predictions (ensemble forecast fan, §13)."""
    P = np.stack(predictions)
    return np.percentile(P, 10, axis=0), np.percentile(P, 50, axis=0), np.percentile(P, 90, axis=0)
