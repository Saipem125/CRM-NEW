"""CRMIP — one tank per injector–producer pair (Atlas "CRMIP" tab; Sayarpour et al. 2009), §9.

4·N_i·N_p parameters with a BHP term (3· without). Fit order rule (§9): CRMIP only where CRMP
residuals show pair structure; the tournament handles that gate.
"""

from __future__ import annotations

import numpy as np

from waterflood_app.config import Config
from waterflood_app.models.base import CRMModel, FitData, FitResult, ModelParams
from waterflood_app.models.crmp import _fit_pywaterflood
from waterflood_app.models.solver import SolverSettings, fit_field, n_params_for, predict_field
from waterflood_app.prep.grid import Grid


class CRMIP(CRMModel):
    name = "CRMIP (pair)"
    variant = "crmip"

    def __init__(self, cfg: Config, engine: str = "inhouse") -> None:
        super().__init__()
        self.cfg = cfg
        self.engine = engine

    def settings(self, grid: Grid) -> SolverSettings:
        return SolverSettings.from_config(self.cfg, float(np.min(grid.dt_days[1:])) if grid.n_steps > 1 else 30.0)

    def n_params(self, grid: Grid) -> int:
        return n_params_for("crmip", grid.n_inj, grid.n_prod, grid.bhp is not None)

    def fit(self, data: FitData, seed: int = 0, n_starts: int | None = None) -> FitResult:
        s = self.settings(data.grid)
        if n_starts is not None:
            s.n_starts = n_starts
        if self.engine == "pywaterflood":
            self._result = _fit_pywaterflood(data, "per-pair")
        else:
            self._result = fit_field(data, "crmip", s, seed)
        return self._result

    def predict(self, grid: Grid, params: ModelParams | None = None) -> np.ndarray:
        return predict_field(grid, params or self.params, "crmip")
