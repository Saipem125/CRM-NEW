"""In-house CRM solver with analytic gradients — architecture §9; Atlas CRMP / CRMIP tabs.

Governing equation (Atlas "CRMP" §1, Sayarpour et al. 2009)::

    dq_j/dt + q_j/τ_j = (1/τ_j) Σ_i f_ij i_i(t) − J_j dp_wf,j/dt

with Σ_i f_ij ≤ 1 (Atlas) applied per injector as Σ_j f_ij ≤ 1 (DECISIONS.md, M0).

Discrete analytical solution for piecewise-constant i and piecewise-linear p_wf over a step
Δt_n = t_n − t_{n−1} (e_n = exp(−Δt_n/τ))::

    x_n = x_{n−1} e_n + (1 − e_n) [ Σ_i f_ij i_i(n) − J_j τ_j Δp_n/Δt_n ],   x_0 = 0
    q̂_j(n) = g_p,j q_j(0) exp(−t_n/τ_p,j) + x_n

The first term is the primary-depletion decay of the initial state. The Atlas capacitance
model has q(t₀)e^{−(t−t₀)/τ}: g_p = 1 and τ_p = τ ("tied primary", the default). The free
form (g_p, τ_p fitted, as pywaterflood does) is available with ``solver.free_primary`` — it
can absorb slowly declining external support that should instead show up as Σf > 1 (§9).
The injection of the interval ending at t_0 is already inside q(0) (M0 convention).

CRMIP (Atlas "CRMIP" §1) keeps one state per pair::

    x_ij(n) = x_ij(n−1) e_ij + (1 − e_ij) [ f_ij i_i(n) − J_ij τ_ij Δp_n/Δt_n ],   q̂_j = Σ_i x_ij + primary

Solution strategy per producer: variable projection first (for fixed τ, τ_p the model is
linear in f, g_p, J → bounded least squares; the outer search over τ (and τ_p) is a grid plus
Nelder–Mead), then a bounded L-BFGS-B polish with analytic gradients from the VarPro solution
and from random starts (multi-start ensemble). Producers are fitted independently; a joint
SLSQP refinement enforces Σ_j f_ij ≤ 1 per injector when violated.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from joblib import Parallel, delayed
from scipy.optimize import lsq_linear, minimize

from waterflood_app.config import Config, seed_from_hash
from waterflood_app.models.base import FitData, FitResult, ModelParams
from waterflood_app.prep.grid import Grid

FArray = npt.NDArray[np.float64]
BArray = npt.NDArray[np.bool_]
Forward = Callable[[FArray, "ProducerData", bool], tuple[FArray, FArray | None]]


# --------------------------------------------------------------------------------------
# Per-producer data and settings
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ProducerData:
    """Everything the per-producer objective needs, pre-scaled."""

    inj: FArray  # (M, Ni) injection, scaled
    q: FArray  # (M,) observed liquid, scaled
    w: FArray  # (M,) weights (producing days → 1)
    t: FArray  # (M,) days
    dt: FArray  # (M,) days
    dpdt: FArray | None  # (M,) Δp_n / Δt_n (pressure per day), None → constant BHP
    q0: float  # scaled q(0)
    n_train: int
    dist: FArray | None = None  # (Ni,) distances for the distance penalty
    allowed: BArray | None = None  # (Ni,) injectors allowed to connect (distance mask)


@dataclass
class SolverSettings:
    n_starts: int = 8
    tau_min: float = 30.0
    tau_max: float = 3650.0
    sparsity_lambda: float = 0.0
    distance_lambda: float = 0.0
    maxiter: int = 400
    ftol: float = 1e-10
    n_jobs: int = -1
    joint_sum_bound: float = 1.0
    free_primary: bool = False

    @classmethod
    def from_config(cls, cfg: Config, dt_min: float) -> SolverSettings:
        s = cfg.section("solver")
        return cls(
            n_starts=int(s["multistart"]),
            tau_min=float(s["tau_min_dt_multiple"]) * dt_min,
            tau_max=float(s["tau_max_days"]),
            sparsity_lambda=float(s["sparsity_lambda"]),
            maxiter=int(s["maxiter"]),
            ftol=float(s["ftol"]),
            n_jobs=int(s["n_jobs"]),
            joint_sum_bound=float(s["joint_refine_if_sum_f_exceeds"]),
            free_primary=bool(s.get("free_primary", False)),
        )


# --------------------------------------------------------------------------------------
# Forward models with sensitivities (one producer)
# --------------------------------------------------------------------------------------
def crmp_forward(
    theta: FArray, d: ProducerData, need_grad: bool = True, tied: bool = False
) -> tuple[FArray, FArray | None]:
    """CRMP per producer.

    θ = [f_1..f_Ni, τ, g_p, τ_p, (J)] (free primary) or [f_1..f_Ni, τ, (J)] (tied: g_p = 1, τ_p = τ).
    Returns q̂ (M,) and the Jacobian (M, k) in the same layout.
    """
    m, ni = d.inj.shape
    has_j = d.dpdt is not None
    f = theta[:ni]
    tau = theta[ni]
    if tied:
        g_p, tau_p = 1.0, tau
        J = theta[ni + 1] if has_j else 0.0
    else:
        g_p, tau_p = theta[ni + 1], theta[ni + 2]
        J = theta[ni + 3] if has_j else 0.0
    e = np.exp(-d.dt / tau)
    S = d.inj @ f
    bhp_src = np.zeros(m) if d.dpdt is None else -J * tau * d.dpdt
    x = np.zeros(m)
    c = np.zeros((m, ni))  # ∂x/∂f_i
    dx_dtau = np.zeros(m)
    dx_dJ = np.zeros(m)
    for n in range(1, m):
        en = e[n]
        src = S[n] + bhp_src[n]
        x[n] = x[n - 1] * en + (1.0 - en) * src
        if need_grad:
            c[n] = c[n - 1] * en + (1.0 - en) * d.inj[n]
            de = en * d.dt[n] / tau**2
            dsrc_dtau = 0.0 if d.dpdt is None else -J * d.dpdt[n]
            dx_dtau[n] = dx_dtau[n - 1] * en + x[n - 1] * de - de * src + (1.0 - en) * dsrc_dtau
            if has_j:
                assert d.dpdt is not None
                dx_dJ[n] = dx_dJ[n - 1] * en + (1.0 - en) * (-tau * d.dpdt[n])
    decay = np.exp(-d.t / tau_p)
    prim = g_p * d.q0 * decay
    qhat = prim + x
    if not need_grad:
        return qhat, None
    dprim_dtaup = prim * d.t / tau_p**2
    if tied:
        k = ni + 1 + (1 if has_j else 0)
        jac = np.zeros((m, k))
        jac[:, :ni] = c
        jac[:, ni] = dx_dtau + dprim_dtaup
        if has_j:
            jac[:, ni + 1] = dx_dJ
        return qhat, jac
    k = ni + 3 + (1 if has_j else 0)
    jac = np.zeros((m, k))
    jac[:, :ni] = c
    jac[:, ni] = dx_dtau
    jac[:, ni + 1] = d.q0 * decay
    jac[:, ni + 2] = dprim_dtaup
    if has_j:
        jac[:, ni + 3] = dx_dJ
    return qhat, jac


def crmip_forward(theta: FArray, d: ProducerData, need_grad: bool = True) -> tuple[FArray, FArray | None]:
    """CRMIP per producer. θ = [f_1..f_Ni, τ_1..τ_Ni, g_p, τ_p, (J_1..J_Ni)] (free primary)."""
    m, ni = d.inj.shape
    has_j = d.dpdt is not None
    f = theta[:ni]
    tau = theta[ni : 2 * ni]
    g_p, tau_p = theta[2 * ni], theta[2 * ni + 1]
    J = theta[2 * ni + 2 : 3 * ni + 2] if has_j else np.zeros(ni)
    e = np.exp(-d.dt[:, None] / tau[None, :])  # (M, Ni)
    dp = np.zeros(m) if d.dpdt is None else d.dpdt
    x = np.zeros((m, ni))
    c = np.zeros((m, ni))
    dx_dtau = np.zeros((m, ni))
    dx_dJ = np.zeros((m, ni))
    for n in range(1, m):
        en = e[n]
        src = f * d.inj[n] - J * tau * dp[n]
        x[n] = x[n - 1] * en + (1.0 - en) * src
        if need_grad:
            c[n] = c[n - 1] * en + (1.0 - en) * d.inj[n]
            de = en * d.dt[n] / tau**2
            dx_dtau[n] = dx_dtau[n - 1] * en + x[n - 1] * de - de * src + (1.0 - en) * (-J * dp[n])
            if has_j:
                dx_dJ[n] = dx_dJ[n - 1] * en + (1.0 - en) * (-tau * dp[n])
    decay = np.exp(-d.t / tau_p)
    prim = g_p * d.q0 * decay
    qhat = prim + x.sum(axis=1)
    if not need_grad:
        return qhat, None
    k = 2 * ni + 2 + (ni if has_j else 0)
    jac = np.zeros((m, k))
    jac[:, :ni] = c
    jac[:, ni : 2 * ni] = dx_dtau
    jac[:, 2 * ni] = d.q0 * decay
    jac[:, 2 * ni + 1] = prim * d.t / tau_p**2
    if has_j:
        jac[:, 2 * ni + 2 :] = dx_dJ
    return qhat, jac


def make_forward(variant: str, tied: bool) -> Forward:
    if variant == "crmip":
        return crmip_forward
    if tied:
        return lambda th, d, g: crmp_forward(th, d, g, tied=True)
    return lambda th, d, g: crmp_forward(th, d, g, tied=False)


# --------------------------------------------------------------------------------------
# Objective, bounds, starts
# --------------------------------------------------------------------------------------
def _objective(
    theta: FArray, d: ProducerData, forward: Forward, lam: float, lam_d: float, ni: int
) -> tuple[float, FArray]:
    qhat, jac = forward(theta, d, True)
    assert jac is not None
    n = d.n_train
    r = (qhat[:n] - d.q[:n]) * d.w[:n]
    val = 0.5 * float(r @ r)
    grad = jac[:n].T @ (r * d.w[:n])
    if lam > 0:
        val += lam * float(theta[:ni].sum())
        grad[:ni] += lam
    if lam_d > 0 and d.dist is not None:
        pen = lam_d * d.dist / max(float(np.median(d.dist)), 1e-9)
        val += float(pen @ theta[:ni])
        grad[:ni] += pen
    return val, grad


def _bounds(d: ProducerData, variant: str, s: SolverSettings) -> list[tuple[float, float]]:
    ni = d.inj.shape[1]
    has_j = d.dpdt is not None
    allowed = np.ones(ni, dtype=bool) if d.allowed is None else d.allowed
    f_b = [(0.0, 1.0) if a else (0.0, 0.0) for a in allowed]
    tau_b = (s.tau_min, s.tau_max)
    prim = [(0.0, 2.0), (s.tau_min, 3.0 * s.tau_max)]
    if variant == "crmip":
        return f_b + [tau_b] * ni + prim + ([(0.0, 1e6)] * ni if has_j else [])
    if s.free_primary:
        return f_b + [tau_b] + prim + ([(0.0, 1e6)] if has_j else [])
    return f_b + [tau_b] + ([(0.0, 1e6)] if has_j else [])


def _pack_theta(
    variant: str,
    s: SolverSettings,
    has_j: bool,
    ni: int,
    f: FArray,
    tau: FArray | float,
    g: float,
    tp: float,
    J: FArray | float,
) -> FArray:
    if variant == "crmip":
        tau_v = np.full(ni, tau) if np.ndim(tau) == 0 else np.asarray(tau)
        parts = [f, tau_v, [g, tp]]
        if has_j:
            parts.append(np.full(ni, J) if np.ndim(J) == 0 else np.asarray(J))
    else:
        parts = [f, [float(np.mean(tau))]]
        if s.free_primary:
            parts.append([g, tp])
        if has_j:
            parts.append([float(np.mean(J))])
    return np.concatenate([np.atleast_1d(np.asarray(p, dtype=np.float64)) for p in parts])


def _starts(d: ProducerData, variant: str, s: SolverSettings, rng: np.random.Generator) -> list[FArray]:
    m, ni = d.inj.shape
    has_j = d.dpdt is not None
    mean_i = np.maximum(d.inj[: d.n_train].mean(axis=0), 1e-9)
    tr_mask = d.w[: d.n_train] > 0
    mean_q = float(d.q[: d.n_train][tr_mask].mean()) if tr_mask.any() else 1.0
    prop = np.clip(np.full(ni, mean_q / max(mean_i.sum(), 1e-9)), 0.0, 1.0)
    if d.dist is not None and (d.dist > 0).all():
        prox = (1.0 / d.dist**2) / (1.0 / d.dist**2).sum()
        prop = np.clip(prox * mean_q / max(mean_i.sum(), 1e-9) * ni, 0.0, 1.0)
    dt_bar = float(np.mean(d.dt[1:])) if m > 1 else 30.0
    tau0 = float(np.clip(3.0 * dt_bar, s.tau_min, s.tau_max))
    f1 = np.full(ni, min(1.0, 2.0 / max(1, ni)) * min(1.0, mean_q / max(mean_i.sum(), 1e-9)))
    starts = [
        _pack_theta(variant, s, has_j, ni, prop, tau0, 1.0, tau0, 0.0),
        _pack_theta(variant, s, has_j, ni, f1, 6.0 * dt_bar, 1.0, 6.0 * dt_bar, 0.0),
    ]
    while len(starts) < s.n_starts:
        f = np.clip(
            rng.dirichlet(np.ones(ni)) * rng.uniform(0.6, 1.4) * mean_q / max(mean_i.mean(), 1e-9) / ni, 0.0, 1.0
        )
        tau = np.exp(rng.uniform(np.log(max(s.tau_min, 1.0)), np.log(min(s.tau_max, 40.0 * dt_bar)), size=ni))
        tp = float(np.exp(rng.uniform(np.log(max(s.tau_min, 1.0)), np.log(min(3.0 * s.tau_max, 60.0 * dt_bar)))))
        starts.append(_pack_theta(variant, s, has_j, ni, f, tau, float(rng.uniform(0.6, 1.2)), tp, 0.0))
    return starts


# --------------------------------------------------------------------------------------
# Variable projection (CRMP): for fixed (τ, τ_p) the model is linear in [f, g_p, J]
# --------------------------------------------------------------------------------------
def _design_crmp(tau: float, d: ProducerData) -> tuple[FArray, FArray | None]:
    """Filtered injections c_i (M, Ni) and the BHP column b (M,) for τ, so x = c·f + b·J."""
    m, ni = d.inj.shape
    e = np.exp(-d.dt / tau)
    c = np.zeros((m, ni))
    b = np.zeros(m) if d.dpdt is not None else None
    for n in range(1, m):
        c[n] = c[n - 1] * e[n] + (1.0 - e[n]) * d.inj[n]
        if b is not None and d.dpdt is not None:
            b[n] = b[n - 1] * e[n] + (1.0 - e[n]) * (-tau * d.dpdt[n])
    return c, b


def _varpro_solve(tau: float, tau_p: float, d: ProducerData, s: SolverSettings) -> tuple[float, FArray]:
    """Bounded weighted least squares for the linear parameters; returns (SSE, θ in the fit layout)."""
    ni = d.inj.shape[1]
    has_j = d.dpdt is not None
    c, b = _design_crmp(tau, d)
    n = d.n_train
    w = d.w[:n]
    allowed = np.ones(ni, dtype=bool) if d.allowed is None else d.allowed
    n_allowed = int(allowed.sum())
    cols = [c[:, allowed]]  # disallowed pairs are fixed at f = 0 (lsq_linear needs lb < ub)
    lb = [np.zeros(n_allowed)]
    ub = [np.ones(n_allowed)]
    if s.free_primary:
        cols.append((d.q0 * np.exp(-d.t / tau_p))[:, None])
        lb.append(np.array([0.0]))
        ub.append(np.array([2.0]))
        y = d.q.copy()
    else:
        y = d.q - d.q0 * np.exp(-d.t / tau)
    if has_j and b is not None:
        cols.append(b[:, None])
        lb.append(np.array([0.0]))
        ub.append(np.array([np.inf]))
    A = np.hstack(cols)
    Aw = A[:n] * w[:, None]
    yw = y[:n] * w
    res = lsq_linear(Aw, yw, bounds=(np.concatenate(lb), np.concatenate(ub)), method="bvls", tol=1e-12, max_iter=500)
    beta = np.asarray(res.x, dtype=np.float64)
    r = Aw @ beta - yw
    f_full = np.zeros(ni)
    f_full[allowed] = beta[:n_allowed]
    rest = beta[n_allowed:]
    if s.free_primary:
        theta = np.concatenate([f_full, [tau, rest[0], tau_p], rest[1:]])
    else:
        theta = np.concatenate([f_full, [tau], rest])
    return float(r @ r), theta


def varpro_crmp(d: ProducerData, s: SolverSettings, n_refine: int = 3) -> list[tuple[float, FArray]]:
    """Coarse grid over τ (and τ_p) → Nelder–Mead refinement of the best cells → [(SSE, θ)] ascending."""
    m = d.inj.shape[0]
    dt_bar = float(np.mean(d.dt[1:])) if m > 1 else 30.0
    tau_hi = min(s.tau_max, 60.0 * dt_bar)
    taus = np.exp(np.linspace(np.log(max(s.tau_min, 1e-3)), np.log(tau_hi), 16))
    out: list[tuple[float, FArray]] = []
    if s.free_primary:
        tau_ps = np.exp(np.linspace(np.log(max(s.tau_min, 1e-3)), np.log(min(3.0 * s.tau_max, 200.0 * dt_bar)), 8))
        cells = sorted(((_varpro_solve(t, tp, d, s)[0], t, tp) for t in taus for tp in tau_ps), key=lambda c: c[0])
        lo = np.log([s.tau_min, s.tau_min])
        hi = np.log([s.tau_max, 3.0 * s.tau_max])

        def sse2(z: FArray) -> float:
            zc = np.clip(z, lo, hi)
            pen = 1e6 * float(((z - zc) ** 2).sum())
            return _varpro_solve(float(np.exp(zc[0])), float(np.exp(zc[1])), d, s)[0] + pen

        for _sse0, t, tp in cells[:n_refine]:
            res = minimize(
                sse2, np.log([t, tp]), method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-12, "maxiter": 400}
            )
            z = np.clip(res.x, lo, hi)
            out.append(_varpro_solve(float(np.exp(z[0])), float(np.exp(z[1])), d, s))
    else:
        cells1 = sorted(((_varpro_solve(t, t, d, s)[0], t) for t in taus), key=lambda c: c[0])
        lo1, hi1 = np.log(s.tau_min), np.log(s.tau_max)

        def sse1(z: FArray) -> float:
            zc = float(np.clip(z[0], lo1, hi1))
            return _varpro_solve(float(np.exp(zc)), float(np.exp(zc)), d, s)[0] + float(1e6 * (z[0] - zc) ** 2)

        for _sse0, t in cells1[:n_refine]:
            res = minimize(
                sse1,
                np.array([np.log(t)]),
                method="Nelder-Mead",
                options={"xatol": 1e-5, "fatol": 1e-12, "maxiter": 200},
            )
            tv = float(np.exp(np.clip(res.x[0], lo1, hi1)))
            out.append(_varpro_solve(tv, tv, d, s))
    out.sort(key=lambda t: t[0])
    return out


# --------------------------------------------------------------------------------------
# Per-producer fit
# --------------------------------------------------------------------------------------
def fit_producer(d: ProducerData, variant: str, s: SolverSettings, seed: int) -> tuple[list[FArray], list[float]]:
    """VarPro + multi-start bounded L-BFGS-B fit of one producer. Returns solutions and SSEs (ascending)."""
    ni = d.inj.shape[1]
    has_j = d.dpdt is not None
    tied = variant != "crmip" and not s.free_primary
    forward = make_forward(variant, tied)
    rng = np.random.default_rng(seed)
    bounds = _bounds(d, variant, s)
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    starts = _starts(d, variant, s, rng)
    vp = varpro_crmp(d, s)
    if variant == "crmip":
        for _, th in vp:
            f, tau = th[:ni], th[ni]
            if s.free_primary:
                gp, tp, jv = th[ni + 1], th[ni + 2], (th[ni + 3] if has_j else 0.0)
            else:
                gp, tp, jv = 1.0, tau, (th[ni + 1] if has_j else 0.0)
            parts = [f, np.full(ni, tau), [gp, tp]]
            if has_j:
                parts.append(np.full(ni, jv / ni))
            starts.insert(0, np.concatenate([np.atleast_1d(np.asarray(p, dtype=np.float64)) for p in parts]))
    else:
        for _, th in vp:
            starts.insert(0, th)
    sols: list[tuple[float, FArray]] = []
    for x0 in starts:
        x0 = np.clip(x0, lo, hi)
        res = minimize(
            _objective,
            x0,
            args=(d, forward, s.sparsity_lambda, s.distance_lambda, ni),
            jac=True,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": s.maxiter, "ftol": s.ftol, "gtol": 1e-9},
        )
        x = np.clip(np.asarray(res.x, dtype=np.float64), lo, hi)
        qhat, _ = forward(x, d, False)
        r = (qhat[: d.n_train] - d.q[: d.n_train]) * d.w[: d.n_train]
        sols.append((float(r @ r), x))
    sols.sort(key=lambda t: t[0])
    return [x for _, x in sols], [v for v, _ in sols]


# --------------------------------------------------------------------------------------
# Field-level driver
# --------------------------------------------------------------------------------------
def producer_data(
    grid: Grid, j: int, n_train: int, weights: FArray, scale: float, allowed: BArray | None = None
) -> ProducerData:
    dpdt = None
    if grid.bhp is not None:
        dp = np.zeros(grid.n_steps)
        dp[1:] = np.diff(grid.bhp[:, j]) / grid.dt_days[1:]
        dpdt = dp
    dist = None
    d = grid.distances()
    if d is not None:
        dist = d[:, j]
    return ProducerData(
        inj=grid.inj / scale,
        q=grid.liq[:, j] / scale,
        w=weights[:, j],
        t=grid.time_days,
        dt=grid.dt_days,
        dpdt=dpdt,
        q0=float(grid.liq[0, j] / scale),
        n_train=n_train,
        dist=dist,
        allowed=allowed,
    )


def _unpack(
    theta: FArray, ni: int, variant: str, s: SolverSettings, has_j: bool, scale: float
) -> tuple[FArray, FArray, FArray | None, float, float]:
    f = theta[:ni]
    if variant == "crmip":
        tau = theta[ni : 2 * ni]
        g_p, tau_p = theta[2 * ni], theta[2 * ni + 1]
        J = theta[2 * ni + 2 : 3 * ni + 2] * scale if has_j else None
        return f, tau, J, float(g_p), float(tau_p)
    tau = np.atleast_1d(theta[ni])
    if s.free_primary:
        g_p, tau_p = theta[ni + 1], theta[ni + 2]
        J = np.atleast_1d(theta[ni + 3] * scale) if has_j else None
    else:
        g_p, tau_p = 1.0, float(tau[0])
        J = np.atleast_1d(theta[ni + 1] * scale) if has_j else None
    return f, tau, J, float(g_p), float(tau_p)


def _free_theta(params: ModelParams, j: int, variant: str, has_j: bool) -> FArray:
    """Full (free-primary) θ for one producer from ModelParams — used for prediction."""
    if variant == "crmip":
        parts = [params.f[:, j], params.tau[:, j], [params.gain_p[j], params.tau_p[j]]]
        if has_j:
            assert params.J is not None
            parts.append(params.J[:, j])
    else:
        parts = [params.f[:, j], [params.tau[j], params.gain_p[j], params.tau_p[j]]]
        if has_j:
            assert params.J is not None
            parts.append([params.J[j]])
    return np.concatenate([np.atleast_1d(np.asarray(p, dtype=np.float64)) for p in parts])


def predict_field(grid: Grid, params: ModelParams, variant: str) -> FArray:
    """Liquid prediction (M, Np) for a grid with fitted parameters (forecast mode from q(0))."""
    out = np.zeros((grid.n_steps, grid.n_prod))
    has_j = params.J is not None and grid.bhp is not None
    forward = make_forward(variant, tied=False)
    for j in range(grid.n_prod):
        d = producer_data(grid, j, grid.n_steps, np.ones((grid.n_steps, grid.n_prod)), 1.0)
        if not has_j:
            d = ProducerData(d.inj, d.q, d.w, d.t, d.dt, None, d.q0, d.n_train, d.dist)
        qhat, _ = forward(_free_theta(params, j, variant, has_j), d, False)
        out[:, j] = qhat
    return out


def n_params_for(variant: str, ni: int, npd: int, has_j: bool, free_primary: bool = False) -> int:
    if variant == "crmip":
        return npd * (2 * ni + 2 + (ni if has_j else 0))
    return npd * (ni + (3 if free_primary else 1) + (1 if has_j else 0))


def fit_field(
    data: FitData, variant: str, settings: SolverSettings, seed: int, allowed: BArray | None = None
) -> FitResult:
    """Fit all producers (parallel, VarPro + multi-start), then joint refinement if Σ_j f_ij > bound.

    ``allowed`` is an optional (Ni, Np) mask of injector–producer pairs allowed to connect
    (distance prior); disallowed pairs are fixed at f_ij = 0.
    """
    t0 = time.perf_counter()
    grid = data.grid
    ni, npd = grid.n_inj, grid.n_prod
    has_j = grid.bhp is not None
    n_train = data.split.n_train
    scales = np.array(
        [
            max(
                float(grid.liq[:n_train, j][data.w[:n_train, j] > 0].mean())
                if (data.w[:n_train, j] > 0).any()
                else 1.0,
                1e-9,
            )
            for j in range(npd)
        ]
    )
    pdata = [
        producer_data(grid, j, n_train, data.w, scales[j], None if allowed is None else allowed[:, j])
        for j in range(npd)
    ]
    seeds = [seed_from_hash(f"{seed}", f"prod{j}") for j in range(npd)]
    jobs = Parallel(n_jobs=settings.n_jobs if npd > 4 else 1, prefer="processes")(
        delayed(fit_producer)(pdata[j], variant, settings, seeds[j]) for j in range(npd)
    )
    best_theta = [sols[0] for sols, _ in jobs]

    def assemble(thetas: list[FArray]) -> ModelParams:
        f = np.zeros((ni, npd))
        tau = np.zeros((ni, npd)) if variant == "crmip" else np.zeros(npd)
        J: FArray | None = (np.zeros((ni, npd)) if variant == "crmip" else np.zeros(npd)) if has_j else None
        g = np.zeros(npd)
        tp = np.zeros(npd)
        for j, th in enumerate(thetas):
            fj, tj, Jj, gj, tpj = _unpack(th, ni, variant, settings, has_j, scales[j])
            f[:, j] = fj
            if variant == "crmip":
                tau[:, j] = tj
                if J is not None and Jj is not None:
                    J[:, j] = Jj
            else:
                tau[j] = tj[0]
                if J is not None and Jj is not None:
                    J[j] = Jj[0]
            g[j], tp[j] = gj, tpj
        return ModelParams(f=f, tau=tau, J=J, gain_p=g, tau_p=tp)

    params = assemble(best_theta)
    notes: list[str] = []
    if (params.sum_f_per_injector > settings.joint_sum_bound + 1e-9).any():
        params = _joint_refine(pdata, best_theta, variant, settings, ni, npd, assemble)
        notes.append("joint refinement applied: per-injector Σ_j f_ij bound was active")
    prediction = predict_field(grid, params, variant)
    r = (prediction[:n_train] - grid.liq[:n_train]) * data.w[:n_train]
    sse = float((r * r).sum())
    ensemble: list[ModelParams] = []
    n_members = max(len(sols) for sols, _ in jobs)
    for k in range(n_members):
        ensemble.append(assemble([sols[min(k, len(sols) - 1)] for sols, _ in jobs]))
    starts_sse = [
        float(sum(sses[min(k, len(sses) - 1)] * scales[j] ** 2 for j, (_, sses) in enumerate(jobs)))
        for k in range(n_members)
    ]
    k_params = n_params_for(variant, ni, npd, has_j, settings.free_primary)
    return FitResult(
        params=params,
        prediction=prediction,
        sse_train=sse,
        n_params=k_params,
        runtime_s=time.perf_counter() - t0,
        ensemble=ensemble,
        starts_sse=starts_sse,
        notes=notes,
    )


def _joint_refine(
    pdata: list[ProducerData],
    thetas: list[FArray],
    variant: str,
    s: SolverSettings,
    ni: int,
    npd: int,
    assemble: Callable[[list[FArray]], ModelParams],
) -> ModelParams:
    tied = variant != "crmip" and not s.free_primary
    forward = make_forward(variant, tied)
    sizes = [len(t) for t in thetas]
    offs = np.cumsum([0, *sizes])
    x0 = np.concatenate(thetas)
    bounds: list[tuple[float, float]] = []
    for j in range(npd):
        bounds += _bounds(pdata[j], variant, s)

    def fun(x: FArray) -> tuple[float, FArray]:
        val = 0.0
        grad = np.zeros_like(x)
        for j in range(npd):
            th = x[offs[j] : offs[j + 1]]
            v, g = _objective(th, pdata[j], forward, s.sparsity_lambda, s.distance_lambda, ni)
            val += v
            grad[offs[j] : offs[j + 1]] = g
        return val, grad

    A = np.zeros((ni, len(x0)))
    for j in range(npd):
        A[np.arange(ni), offs[j] + np.arange(ni)] = 1.0
    cons = {"type": "ineq", "fun": lambda x: s.joint_sum_bound - A @ x, "jac": lambda x: -A}
    res = minimize(
        fun, x0, jac=True, method="SLSQP", bounds=bounds, constraints=[cons], options={"maxiter": 200, "ftol": 1e-12}
    )
    x = np.clip(res.x, [b[0] for b in bounds], [b[1] for b in bounds])
    return assemble([x[offs[j] : offs[j + 1]] for j in range(npd)])
