"""CRMP — one tank per producer (Atlas "CRMP" tab; Sayarpour et al. 2009), §9.

Two engines: ``"inhouse"`` (analytic-gradient solver, ``solver.py``) and ``"pywaterflood"``
(Phase-1 engine and permanent regression baseline, §9/§16). The pywaterflood engine receives
the M0 convention alignment (i(0) zeroed) so both engines fit the same model.
"""

from __future__ import annotations

import time

import numpy as np

from waterflood_app.config import Config
from waterflood_app.models.base import CRMModel, FitData, FitResult, ModelParams
from waterflood_app.models.solver import SolverSettings, fit_field, n_params_for, predict_field
from waterflood_app.prep.grid import Grid


class CRMP(CRMModel):
    name = "CRMP (producer)"
    variant = "crmp"

    def __init__(self, cfg: Config, engine: str = "inhouse") -> None:
        super().__init__()
        self.cfg = cfg
        self.engine = engine

    def settings(self, grid: Grid) -> SolverSettings:
        return SolverSettings.from_config(self.cfg, float(np.min(grid.dt_days[1:])) if grid.n_steps > 1 else 30.0)

    def n_params(self, grid: Grid) -> int:
        return n_params_for("crmp", grid.n_inj, grid.n_prod, grid.bhp is not None)

    def fit(self, data: FitData, seed: int = 0, n_starts: int | None = None) -> FitResult:
        s = self.settings(data.grid)
        if n_starts is not None:
            s.n_starts = n_starts
        if self.engine == "pywaterflood":
            self._result = _fit_pywaterflood(data, "per-producer")
        else:
            self._result = fit_field(data, "crmp", s, seed)
        return self._result

    def predict(self, grid: Grid, params: ModelParams | None = None) -> np.ndarray:
        return predict_field(grid, params or self.params, "crmp")


def _fit_pywaterflood(data: FitData, tau_selection: str) -> FitResult:
    """pywaterflood baseline (Male 2024). Constant-BHP only; training window only."""
    from pywaterflood import CRM

    t0 = time.perf_counter()
    grid = data.grid
    n = data.split.n_train
    inj = grid.inj[:n].copy()
    inj[0] = 0.0  # M0 convention alignment (DECISIONS.md)
    crm = CRM(primary=True, tau_selection=tau_selection, constraints="up-to one")
    crm.fit(grid.liq[:n], inj, grid.time_days[:n])
    gains = np.asarray(crm.gains, dtype=np.float64)  # (Np, Ni)
    tau = np.asarray(crm.tau, dtype=np.float64)
    gp = np.asarray(crm.gains_producer, dtype=np.float64).ravel()
    tp = np.asarray(crm.tau_producer, dtype=np.float64).ravel()
    if tau_selection == "per-producer":
        params = ModelParams(f=gains.T, tau=tau.ravel(), J=None, gain_p=gp, tau_p=tp)
        variant = "crmp"
    else:
        params = ModelParams(f=gains.T, tau=tau.T, J=None, gain_p=gp, tau_p=tp)
        variant = "crmip"
    pred = predict_field(grid, params, variant)
    r = (pred[:n] - grid.liq[:n]) * data.w[:n]
    return FitResult(
        params=params,
        prediction=pred,
        sse_train=float((r * r).sum()),
        n_params=n_params_for(variant, grid.n_inj, grid.n_prod, False),
        runtime_s=time.perf_counter() - t0,
        ensemble=[params],
        starts_sse=[float((r * r).sum())],
        notes=["engine: pywaterflood"],
    )
