"""Two-phase coupled CRM — Atlas "Two-Phase Coupled CRM" tab (Cao, Luo & Lake 2014), §9.

Standard CRM assumes constant total mobility. Here a saturation balance is solved with the
pressure (rate) balance so τ evolves with the total mobility λ_t(S_w)::

    V_b (S_w c_t + φ dS_w/dp) dp/dt = i(t) − q(t),      τ(t) = c_t V_p / (J λ_t(S_w(t)) / λ_t,ref)

Per producer tank (drainage pore volume V_p, initial saturation S_w0) with a Corey rel-perm
fractional-flow curve (no capillary / gravity terms)::

    f_w(S_w) = (k_rw/μ_w) / (k_rw/μ_w + k_ro/μ_o),      V_p dS_w/dt = Σ_i f_ij i_i(t) − f_w(S_w) q(t)

The liquid recursion is the CRMP one with a step-dependent τ_n = τ_ref λ_t,ref / λ_t(S_w,n);
oil = q (1 − f_w). Parameters per producer: f_ij, τ_ref, V_p, S_w0 (+ J with BHP). Eligible only
when the mean water cut is below 0.5 and a rel-perm curve is supplied (§9 gates).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

from waterflood_app.config import Config
from waterflood_app.models.base import CRMModel, FitData, FitResult, ModelParams
from waterflood_app.models.solver import SolverSettings, fit_field, n_params_for
from waterflood_app.prep.grid import Grid

FArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class RelPerm:
    """Corey relative permeability: k_rw = k_rw0 S^nw, k_ro = k_ro0 (1−S)^no, S = (S_w−S_wc)/(1−S_wc−S_or)."""

    swc: float = 0.2
    sor: float = 0.25
    krw0: float = 0.4
    kro0: float = 0.9
    nw: float = 2.0
    no: float = 2.0
    mu_w: float = 0.5
    mu_o: float = 2.0

    def _s(self, sw: FArray | float) -> FArray:
        return np.clip((np.asarray(sw, dtype=np.float64) - self.swc) / max(1.0 - self.swc - self.sor, 1e-9), 0.0, 1.0)

    def mobility(self, sw: FArray | float) -> FArray:
        s = self._s(sw)
        return np.asarray(
            self.krw0 * s**self.nw / self.mu_w + self.kro0 * (1.0 - s) ** self.no / self.mu_o, dtype=np.float64
        )

    def fw(self, sw: FArray | float) -> FArray:
        s = self._s(sw)
        lw = self.krw0 * s**self.nw / self.mu_w
        lo = self.kro0 * (1.0 - s) ** self.no / self.mu_o
        return np.asarray(lw / np.maximum(lw + lo, 1e-12), dtype=np.float64)


def twophase_forward(
    f: FArray,
    tau_ref: float,
    v_p: float,
    sw0: float,
    inj: FArray,
    dt: FArray,
    q0: float,
    relperm: RelPerm,
    dpdt: FArray | None = None,
    J: float = 0.0,
) -> tuple[FArray, FArray, FArray]:
    """One producer: liquid q, water cut f_w and S_w per step (explicit saturation update)."""
    m = inj.shape[0]
    q = np.zeros(m)
    sw = np.zeros(m)
    q[0], sw[0] = q0, sw0
    support = inj @ f
    lam_ref = float(relperm.mobility(sw0))
    for n in range(1, m):
        tau_n = tau_ref * lam_ref / max(float(relperm.mobility(sw[n - 1])), 1e-9)
        e = np.exp(-dt[n] / tau_n)
        src = support[n] - (J * tau_n * dpdt[n] if dpdt is not None else 0.0)
        q[n] = q[n - 1] * e + (1.0 - e) * src
        fw_prev = float(relperm.fw(sw[n - 1]))
        sw[n] = np.clip(
            sw[n - 1] + dt[n] * (support[n] - fw_prev * q[n]) / max(v_p, 1e-9), relperm.swc, 1.0 - relperm.sor
        )
    return q, relperm.fw(sw), sw


class TwoPhaseCRM(CRMModel):
    name = "Two-phase coupled"
    variant = "twophase"

    def __init__(self, cfg: Config, relperm: RelPerm) -> None:
        super().__init__()
        self.cfg = cfg
        self.relperm = relperm

    def n_params(self, grid: Grid) -> int:
        return n_params_for("crmp", grid.n_inj, grid.n_prod, grid.bhp is not None) + 2 * grid.n_prod

    def fit(self, data: FitData, seed: int = 0, n_starts: int | None = None) -> FitResult:
        t0 = time.perf_counter()
        grid = data.grid
        n = data.split.n_train
        s = SolverSettings.from_config(self.cfg, float(np.min(grid.dt_days[1:])) if grid.n_steps > 1 else 30.0)
        s.free_primary = False
        if n_starts is not None:
            s.n_starts = n_starts
        base = fit_field(data, "crmp", s, seed)  # constant-τ start
        p = base.params
        rp = self.relperm
        f = p.f.copy()
        tau = p.tau.copy()
        v_p = np.zeros(grid.n_prod)
        sw0 = np.zeros(grid.n_prod)
        pred = np.zeros_like(grid.liq)
        fw_pred = np.zeros_like(grid.liq)
        for j in range(grid.n_prod):
            wc = grid.water_cut[:n, j]
            mask = grid.prod_mask[:n, j]
            q0 = float(grid.liq[0, j])
            dpdt = None
            if grid.bhp is not None:
                dpdt = np.concatenate([[0.0], np.diff(grid.bhp[:, j]) / grid.dt_days[1:]])
            Jj = float(p.J[j]) if p.J is not None else 0.0
            # initial S_w from the first observed water cut; V_p from a cumulative-injection scale
            sw_guess = float(
                np.clip(
                    _invert_fw(rp, float(wc[mask][:3].mean()) if mask.any() else 0.2),
                    rp.swc + 1e-3,
                    1.0 - rp.sor - 1e-3,
                )
            )
            vp_guess = max(float((grid.inj[:n] @ f[:, j] * grid.dt_days[:n]).sum()), 1.0)
            z0 = np.array([np.log(tau[j]), np.log(vp_guess), sw_guess])
            liq_obs = grid.liq[:, j]

            def loss(
                z: FArray,
                j: int = j,
                q0: float = q0,
                dpdt: FArray | None = dpdt,
                Jj: float = Jj,
                mask: npt.NDArray[np.bool_] = mask,
                liq_obs: FArray = liq_obs,
                wc: FArray = wc,
            ) -> float:
                tr, vp, s0 = (
                    float(np.exp(z[0])),
                    float(np.exp(z[1])),
                    float(np.clip(z[2], rp.swc + 1e-3, 1.0 - rp.sor - 1e-3)),
                )
                q, fw, _ = twophase_forward(f[:, j], tr, vp, s0, grid.inj, grid.dt_days, q0, rp, dpdt, Jj)
                scale = max(float(liq_obs[:n][mask].mean()), 1e-9)
                r_q = ((q[:n] - liq_obs[:n]) / scale)[mask]
                r_w = (fw[:n] - wc)[mask]
                return float((r_q**2).sum() + (r_w**2).sum())

            res = minimize(loss, z0, method="Nelder-Mead", options={"maxiter": 400, "xatol": 1e-4, "fatol": 1e-10})
            tau[j], v_p[j], sw0[j] = (
                float(np.exp(res.x[0])),
                float(np.exp(res.x[1])),
                float(np.clip(res.x[2], rp.swc + 1e-3, 1.0 - rp.sor - 1e-3)),
            )
            q, fw, _ = twophase_forward(f[:, j], tau[j], v_p[j], sw0[j], grid.inj, grid.dt_days, q0, rp, dpdt, Jj)
            pred[:, j], fw_pred[:, j] = q, fw
        params = ModelParams(
            f=f,
            tau=tau,
            J=p.J,
            gain_p=np.ones(grid.n_prod),
            tau_p=tau.copy(),
            extra={"v_p": v_p, "sw0": sw0, "water_cut_pred": fw_pred, "relperm": rp.__dict__},
        )
        r = (pred[:n] - grid.liq[:n]) * data.w[:n]
        self._result = FitResult(
            params=params,
            prediction=pred,
            sse_train=float((r * r).sum()),
            n_params=self.n_params(grid),
            runtime_s=time.perf_counter() - t0,
            ensemble=[params],
            starts_sse=[float((r * r).sum())],
            notes=["two-phase coupled τ(t); oil split from the fractional-flow curve, not the power law"],
        )
        return self._result

    def predict(self, grid: Grid, params: ModelParams | None = None) -> FArray:
        p = params or self.params
        out = np.zeros((grid.n_steps, grid.n_prod))
        for j in range(grid.n_prod):
            dpdt = None
            if grid.bhp is not None and p.J is not None:
                dpdt = np.concatenate([[0.0], np.diff(grid.bhp[:, j]) / grid.dt_days[1:]])
            q, _, _ = twophase_forward(
                p.f[:, j],
                float(p.tau[j]),
                float(p.extra["v_p"][j]),
                float(p.extra["sw0"][j]),
                grid.inj,
                grid.dt_days,
                float(grid.liq[0, j]),
                self.relperm,
                dpdt,
                float(p.J[j]) if p.J is not None else 0.0,
            )
            out[:, j] = q
        return out


def _invert_fw(rp: RelPerm, fw_target: float) -> float:
    grid = np.linspace(rp.swc, 1.0 - rp.sor, 400)
    fw = rp.fw(grid)
    return float(grid[int(np.argmin(np.abs(fw - fw_target)))])
