"""Model interface — architecture §9 ("Calibrated model object": f_ij, τ, J, q₀, fit metrics, UQ ensemble).

Every deployed model exposes ``fit``, ``predict``, ``params`` and ``residuals`` (interpretability
gate, §1): f_ij, τ and J are always available to the optimizer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from waterflood_app.prep.grid import Grid
from waterflood_app.prep.split import Split

FArray = npt.NDArray[np.float64]


def distance_mask(grid: Grid, cfg: Any) -> npt.NDArray[np.bool_] | None:
    """Influence radius (``solver.distance_cutoff_factor`` × median nearest injector–producer distance).

    Pairs farther apart are fixed at f_ij = 0 in every fit; every producer keeps its nearest injector.
    None when the factor is unset or there are no coordinates.
    """
    factor = cfg.get("solver.distance_cutoff_factor")
    d = grid.distances()
    if not factor or d is None or d.size == 0:
        return None
    nearest = np.concatenate([d.min(axis=1), d.min(axis=0)])
    allowed = d <= float(factor) * float(np.median(nearest))
    allowed |= d <= d.min(axis=0, keepdims=True)
    return np.asarray(allowed, dtype=bool)


def fit_weights(grid: Grid, cfg: Any) -> FArray:
    """(M, Np) fit weights: the producing-day mask, with operational transients switched off.

    ``solver.restart_transient_months`` zeroes the first k flowing steps after a producer comes back
    on stream (flush / build-up the CRM cannot represent); ``solver.pre_shutin_months`` zeroes the
    last k flowing steps before a shut-in (ramp-down). Both default to 0 (mask only).
    """
    w = grid.prod_mask.astype(np.float64)
    k_after = int(cfg.get("solver.restart_transient_months", 0) or 0)
    k_before = int(cfg.get("solver.pre_shutin_months", 0) or 0)
    if k_after <= 0 and k_before <= 0:
        return w
    on = grid.prod_mask.astype(int)
    m = grid.n_steps
    for j in range(grid.n_prod):
        col = on[:, j]
        for i in range(1, m):
            if col[i] == 1 and col[i - 1] == 0:  # restart at step i
                w[i : min(m, i + k_after), j] = 0.0
            if col[i] == 0 and col[i - 1] == 1:  # shut-in at step i
                w[max(0, i - k_before) : i, j] = 0.0
    return w


@dataclass
class FitData:
    """One sector × one window, with its train/blind split and the producing-day weights."""

    grid: Grid
    split: Split
    weights: FArray | None = None  # (M, Np) — default: producing-day mask
    allowed: npt.NDArray[np.bool_] | None = None  # (Ni, Np) influence radius: pairs allowed to connect

    def __post_init__(self) -> None:
        if self.weights is None:
            self.weights = self.grid.prod_mask.astype(np.float64)

    @property
    def w(self) -> FArray:
        assert self.weights is not None
        return self.weights


@dataclass
class ModelParams:
    """Physical parameters shared by the CRM family (Atlas CRMT/CRMP/CRMIP tabs)."""

    f: FArray  # (Ni, Np) allocation fractions
    tau: FArray  # (Np,) for CRMT/CRMP, (Ni, Np) for CRMIP  [days]
    J: FArray | None  # (Np,) or (Ni, Np) productivity [res-vol/d per pressure unit]; None → constant BHP
    gain_p: FArray  # (Np,) primary-depletion gain on q(0)
    tau_p: FArray  # (Np,) primary-depletion time constant [days]
    extra: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> ModelParams:
        return ModelParams(
            self.f.copy(),
            self.tau.copy(),
            None if self.J is None else self.J.copy(),
            self.gain_p.copy(),
            self.tau_p.copy(),
            dict(self.extra),
        )

    def as_vector(self) -> FArray:
        parts = [self.f.ravel(), np.ravel(self.tau), self.gain_p, self.tau_p]
        if self.J is not None:
            parts.append(np.ravel(self.J))
        return np.concatenate(parts)

    @property
    def sum_f_per_injector(self) -> FArray:
        return np.asarray(self.f.sum(axis=1), dtype=np.float64)

    @property
    def sum_f_per_producer(self) -> FArray:
        return np.asarray(self.f.sum(axis=0), dtype=np.float64)

    def tau_per_producer(self) -> FArray:
        """Producer-level τ (allocation-weighted for CRMIP) for gates and plots."""
        if self.tau.ndim == 1:
            return self.tau
        w = self.f / np.maximum(self.f.sum(axis=0, keepdims=True), 1e-12)
        return np.asarray((self.tau * w).sum(axis=0), dtype=np.float64)

    def to_dict(self, injectors: list[str], producers: list[str]) -> dict[str, Any]:
        d: dict[str, Any] = {
            "f_ij": {i: {p: float(self.f[a, b]) for b, p in enumerate(producers)} for a, i in enumerate(injectors)},
            "gain_p": dict(zip(producers, self.gain_p.tolist(), strict=True)),
            "tau_p_days": dict(zip(producers, self.tau_p.tolist(), strict=True)),
            "extra": {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in self.extra.items()},
        }
        if self.tau.ndim == 1:
            d["tau_days"] = dict(zip(producers, self.tau.tolist(), strict=True))
        else:
            d["tau_ij_days"] = {
                i: {p: float(self.tau[a, b]) for b, p in enumerate(producers)} for a, i in enumerate(injectors)
            }
        if self.J is not None:
            if self.J.ndim == 1:
                d["J"] = dict(zip(producers, self.J.tolist(), strict=True))
            else:
                d["J_ij"] = {
                    i: {p: float(self.J[a, b]) for b, p in enumerate(producers)} for a, i in enumerate(injectors)
                }
        return d


@dataclass
class FitResult:
    params: ModelParams
    prediction: FArray  # (M, Np) liquid, full history (train + blind, forecast mode)
    sse_train: float
    n_params: int
    runtime_s: float
    ensemble: list[ModelParams] = field(default_factory=list)  # multi-start members within tolerance
    starts_sse: list[float] = field(default_factory=list)
    converged: bool = True
    notes: list[str] = field(default_factory=list)


class CRMModel(ABC):
    """Interface every L3 model implements (§9)."""

    name: str = "CRM"
    variant: str = "crm"

    def __init__(self) -> None:
        self._result: FitResult | None = None

    @abstractmethod
    def fit(self, data: FitData, seed: int = 0, n_starts: int | None = None) -> FitResult: ...

    @abstractmethod
    def predict(self, grid: Grid, params: ModelParams | None = None) -> FArray: ...

    @abstractmethod
    def n_params(self, grid: Grid) -> int: ...

    @property
    def result(self) -> FitResult:
        if self._result is None:
            raise RuntimeError(f"{self.name} is not fitted")
        return self._result

    @property
    def params(self) -> ModelParams:
        return self.result.params

    def residuals(self, grid: Grid) -> FArray:
        return grid.liq - self.predict(grid)
