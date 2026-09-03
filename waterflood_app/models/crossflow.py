"""Productivity-coefficient / crossflow CRM — Atlas "Productivity & Crossflow" tab (Olenchikov & Posvyanskii 2019), §9.

    c_t V_p,k dp̄_k/dt = i_k(t) − q_k(t) + Σ_{m≠k} X_km (p̄_m − p̄_k),      q_k = J_k (p̄_k − p_wf,k)

Tanks are the producers' drainage volumes; injection reaches tank k through fixed allocation
weights taken from the CRMP fit (or a geometric prior); crossflow transmissibilities X_km live
on the distance-graph edges between neighbouring tanks. Parameters per tank: c_tV_k, J_k, p̄_k(0);
per edge: X_km ≥ 0. Requires producer BHP (the J·(p̄ − p_wf) inflow uses it directly). Fitted
with L-BFGS-B on log parameters (finite differences; a few dozen parameters per sector).
Eligible when events ≥ 4/well and BHP exists (§9 gates).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

from waterflood_app.config import Config
from waterflood_app.models.base import CRMModel, FitData, FitResult, ModelParams
from waterflood_app.models.solver import SolverSettings, fit_field
from waterflood_app.prep.grid import Grid

FArray = npt.NDArray[np.float64]


@dataclass
class CrossflowParams:
    ct_v: FArray  # (Np,)
    J: FArray  # (Np,)
    p0: FArray  # (Np,) initial tank pressures
    X: FArray  # (Np, Np) symmetric, zero off the edge set
    f: FArray  # (Ni, Np) fixed allocation of injection to tanks


def crossflow_forward(cp: CrossflowParams, inj: FArray, bhp: FArray, dt: FArray) -> tuple[FArray, FArray]:
    """Implicit-in-crossflow, explicit-in-inflow step of the tank network. Returns (q, p̄) (M, Np)."""
    m, npd = bhp.shape
    p = np.zeros((m, npd))
    q = np.zeros((m, npd))
    p[0] = cp.p0
    q[0] = cp.J * (p[0] - bhp[0])
    support = inj @ cp.f
    L = np.diag(cp.X.sum(axis=1)) - cp.X  # graph Laplacian: Σ_m X_km (p_m − p_k) = −(L p)_k
    for n in range(1, m):
        # c_tV (p_n − p_{n−1})/Δt = s_n − J (p_n − p_wf,n) − L p_n
        #   →  (c_tV/Δt + J + L) p_n = c_tV/Δt p_{n−1} + s_n + J p_wf,n
        A = np.diag(cp.ct_v / dt[n] + cp.J) + L
        b = cp.ct_v / dt[n] * p[n - 1] + support[n] + cp.J * bhp[n]
        p[n] = np.linalg.solve(A, b)
        q[n] = np.maximum(cp.J * (p[n] - bhp[n]), 0.0)
    return q, p


class CrossflowCRM(CRMModel):
    name = "Crossflow / productivity-coeff."
    variant = "crossflow"

    def __init__(self, cfg: Config, edges: list[tuple[int, int]] | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.edges = edges  # producer-index pairs allowed to exchange fluid; None → nearest-neighbour graph
        self.cp: CrossflowParams | None = None

    def n_params(self, grid: Grid) -> int:
        return 3 * grid.n_prod + len(self._edges(grid))

    def _edges(self, grid: Grid) -> list[tuple[int, int]]:
        if self.edges is not None:
            return self.edges
        if grid.xy_prod is None or grid.n_prod < 2:
            return [(a, b) for a in range(grid.n_prod) for b in range(a + 1, grid.n_prod)]
        d = np.linalg.norm(grid.xy_prod[:, None, :] - grid.xy_prod[None, :, :], axis=2)
        np.fill_diagonal(d, np.inf)
        cutoff = 1.5 * float(np.median(d.min(axis=1)))
        return [(a, b) for a in range(grid.n_prod) for b in range(a + 1, grid.n_prod) if d[a, b] <= cutoff]

    def fit(self, data: FitData, seed: int = 0, n_starts: int | None = None) -> FitResult:
        t0 = time.perf_counter()
        grid = data.grid
        if grid.bhp is None:
            raise ValueError("the crossflow variant needs producer BHP")
        n = data.split.n_train
        s = SolverSettings.from_config(self.cfg, float(np.min(grid.dt_days[1:])) if grid.n_steps > 1 else 30.0)
        s.n_starts = 2
        base = fit_field(data, "crmp", s, seed)  # allocation weights and τ scale from CRMP
        f = base.params.f
        edges = self._edges(grid)
        npd, bhp, dt = grid.n_prod, grid.bhp, grid.dt_days
        tau0 = np.maximum(base.params.tau, 1.0)
        q_mean = np.maximum(grid.liq[:n].mean(axis=0), 1e-9)
        dp_guess = np.maximum(np.nanstd(bhp[:n], axis=0), 1.0) * 5.0  # drawdown scale
        J0 = q_mean / dp_guess
        ctv0 = tau0 * J0
        p00 = bhp[0] + q_mean / J0
        scale = np.maximum(q_mean, 1e-9)

        def unpack(z: FArray) -> CrossflowParams:
            ctv = np.exp(z[:npd])
            J = np.exp(z[npd : 2 * npd])
            p0 = z[2 * npd : 3 * npd]
            X = np.zeros((npd, npd))
            for k, (a, b) in enumerate(edges):
                X[a, b] = X[b, a] = np.exp(z[3 * npd + k])
            return CrossflowParams(ctv, J, p0, X, f)

        def loss(z: FArray) -> float:
            q, _ = crossflow_forward(unpack(z), grid.inj, bhp, dt)
            r = ((q[:n] - grid.liq[:n]) / scale[None, :]) * data.w[:n]
            return float((r * r).sum())

        z0 = np.concatenate([np.log(ctv0), np.log(J0), p00, np.full(len(edges), np.log(1e-3 * float(J0.mean())))])
        res = minimize(loss, z0, method="L-BFGS-B", options={"maxiter": 300})
        self.cp = unpack(res.x)
        q, p = crossflow_forward(self.cp, grid.inj, bhp, dt)
        r = (q[:n] - grid.liq[:n]) * data.w[:n]
        tau_eff = self.cp.ct_v / np.maximum(self.cp.J, 1e-12)
        params = ModelParams(
            f=f,
            tau=tau_eff,
            J=self.cp.J,
            gain_p=np.ones(npd),
            tau_p=tau_eff.copy(),
            extra={"ct_v": self.cp.ct_v, "p0": self.cp.p0, "X": self.cp.X, "edges": edges, "p_tank": p},
        )
        self._result = FitResult(
            params=params,
            prediction=q,
            sse_train=float((r * r).sum()),
            n_params=self.n_params(grid),
            runtime_s=time.perf_counter() - t0,
            ensemble=[params],
            starts_sse=[float((r * r).sum())],
            converged=bool(res.success),
            notes=["productivity coefficients + crossflow transmissibilities; allocation weights fixed from CRMP"],
        )
        return self._result

    def predict(self, grid: Grid, params: ModelParams | None = None) -> FArray:
        p = params or self.params
        if grid.bhp is None:
            raise ValueError("the crossflow variant needs producer BHP")
        cp = CrossflowParams(
            np.asarray(p.extra["ct_v"]), np.asarray(p.J), np.asarray(p.extra["p0"]), np.asarray(p.extra["X"]), p.f
        )
        q, _ = crossflow_forward(cp, grid.inj, grid.bhp, grid.dt_days)
        return q
