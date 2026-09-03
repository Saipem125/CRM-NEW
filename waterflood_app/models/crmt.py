"""CRMT — whole-field tank (Atlas "CRMT" tab; Sayarpour et al. 2009), §9.

    q_F(t_n) = q_F(t_{n−1}) e^{−Δt_n/τ_F} + I_F(n) (1 − e^{−Δt_n/τ_F})

Implemented as CRMP on a field-summed grid (one super-injector, one super-producer): the
allocation f_F ≤ 1 absorbs out-of-pattern losses, τ_F is the field time constant, and the
primary term carries q_F(t₀). Used per sector as the first sanity pass and as the τ estimate
for the Δt/τ gate (§8). Producer-level predictions are the field prediction split by each
producer's training-window share (for plots only).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from waterflood_app.config import Config
from waterflood_app.models.base import CRMModel, FitData, FitResult, ModelParams
from waterflood_app.models.solver import SolverSettings, fit_field, predict_field
from waterflood_app.prep.grid import Grid
from waterflood_app.prep.split import Split


def field_grid(grid: Grid) -> Grid:
    """Collapse to one injector and one producer (sums), keeping the time axis and masks."""
    inj = grid.inj.sum(axis=1, keepdims=True)
    liq = grid.liq.sum(axis=1, keepdims=True)
    oil = grid.oil.sum(axis=1, keepdims=True)
    water = grid.water.sum(axis=1, keepdims=True)
    don_p = grid.days_on_prod.max(axis=1, keepdims=True) if grid.n_prod else np.zeros((grid.n_steps, 1))
    don_i = grid.days_on_inj.max(axis=1, keepdims=True) if grid.n_inj else np.zeros((grid.n_steps, 1))
    bhp = None
    if grid.bhp is not None:
        w = np.maximum(grid.liq, 0)
        bhp = np.where(
            w.sum(axis=1, keepdims=True) > 0,
            (grid.bhp * w).sum(axis=1, keepdims=True) / np.maximum(w.sum(axis=1, keepdims=True), 1e-9),
            grid.bhp.mean(axis=1, keepdims=True),
        )
    return replace(
        grid,
        injectors=["FIELD@I"],
        producers=["FIELD@P"],
        inj=inj,
        liq=liq,
        oil=oil,
        water=water,
        days_on_prod=don_p,
        days_on_inj=don_i,
        bhp=bhp,
        xy_inj=None,
        xy_prod=None,
        raw={},
    )


class CRMT(CRMModel):
    name = "CRMT (field tank)"
    variant = "crmt"

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self._shares: np.ndarray | None = None

    def n_params(self, grid: Grid) -> int:
        return 4 + (1 if grid.bhp is not None else 0)

    def fit(self, data: FitData, seed: int = 0, n_starts: int | None = None) -> FitResult:
        grid = data.grid
        fg = field_grid(grid)
        s = SolverSettings.from_config(self.cfg, float(np.min(grid.dt_days[1:])) if grid.n_steps > 1 else 30.0)
        if n_starts is not None:
            s.n_starts = n_starts
        fdata = FitData(fg, Split(data.split.n_train, data.split.n_total), None)
        res = fit_field(fdata, "crmp", s, seed)
        n = data.split.n_train
        tot = np.maximum(grid.liq[:n].sum(axis=0), 0.0)
        self._shares = tot / tot.sum() if tot.sum() > 0 else np.full(grid.n_prod, 1.0 / max(grid.n_prod, 1))
        pred = res.prediction[:, [0]] * self._shares[None, :]
        r = (pred[:n] - grid.liq[:n]) * data.w[:n]
        p = res.params
        params = ModelParams(
            f=np.full((grid.n_inj, grid.n_prod), float(p.f[0, 0]) / max(grid.n_prod, 1))
            * (self._shares[None, :] * grid.n_prod),
            tau=np.full(grid.n_prod, float(p.tau[0])),
            J=None if p.J is None else np.full(grid.n_prod, float(p.J[0])),
            gain_p=np.full(grid.n_prod, float(p.gain_p[0])),
            tau_p=np.full(grid.n_prod, float(p.tau_p[0])),
            extra={
                "f_field": float(p.f[0, 0]),
                "tau_field_days": float(p.tau[0]),
                "shares": self._shares.copy(),
            },
        )
        self._result = FitResult(
            params=params,
            prediction=pred,
            sse_train=float((r * r).sum()),
            n_params=self.n_params(grid),
            runtime_s=res.runtime_s,
            ensemble=[params],
            starts_sse=res.starts_sse,
            notes=res.notes,
        )
        return self._result

    def predict(self, grid: Grid, params: ModelParams | None = None) -> np.ndarray:
        p = params or self.params
        fg = field_grid(grid)
        fp = ModelParams(
            f=np.array([[p.extra["f_field"]]]),
            tau=np.array([p.extra["tau_field_days"]]),
            J=None if p.J is None else np.array([p.J[0]]),
            gain_p=np.array([p.gain_p[0]]),
            tau_p=np.array([p.tau_p[0]]),
        )
        shares = np.asarray(p.extra.get("shares", np.full(grid.n_prod, 1.0 / max(grid.n_prod, 1))))
        return np.asarray(predict_field(fg, fp, "crmp")[:, [0]] * shares[None, :], dtype=np.float64)

    @property
    def tau_field(self) -> float:
        return float(self.params.extra["tau_field_days"])
