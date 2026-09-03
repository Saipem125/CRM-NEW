"""CRM–Aquifer (CRMPA) — Atlas "CRM–Aquifer" tab (Parra, Samaniego-V. & Lake 2024; Koroglu et al. 2024), §9.

Finite Fetkovich aquifer tank coupled to the producer-based CRM::

    Ẇ_e = J_aq (p̄_aq − p̄_r),   dp̄_aq/dt = −Ẇ_e / (c_t V_p,aq),   c_t V_p,r dp̄_r/dt = Ẇ_e + i(t) − q(t)

With Δp = p̄_aq − p̄_r the influx obeys one linear ODE driven by the field imbalance I − Q::

    dẆ_e/dt = −k₁ Ẇ_e − k₂ (I − Q),     k₁ = J_aq (1/c_tV_aq + 1/c_tV_r),   k₂ = J_aq / c_tV_r

so the rates identify (Ẇ_e(0), k₁, k₂). The influx series then enters the producers as an extra
injector ("AQUIFER") whose allocation a_j (Σ_j a_j ≤ 1) is fitted linearly with the other f_ij
(variable projection, tied primary). The outer optimisation over (Ẇ_e(0), k₁, k₂) is a
Nelder–Mead on log parameters from a small grid of starts. Physical J_aq, c_tV_aq, c_tV_r need a
pressure scale: with static surveys p̄_r(t) the balance c_tV_r Δp̄_r = Σ(Ẇ_e + I − Q)Δt gives
c_tV_r, then J_aq = k₂ c_tV_r and c_tV_aq = J_aq/(k₁ − k₂). Without surveys the influx and the
rate constants are reported and the pore volumes are marked "not resolvable" (§8).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

from waterflood_app.config import Config
from waterflood_app.models.base import CRMModel, FitData, FitResult, ModelParams
from waterflood_app.models.solver import SolverSettings, fit_field, n_params_for, predict_field
from waterflood_app.prep.grid import Grid
from waterflood_app.prep.split import Split

FArray = npt.NDArray[np.float64]
AQUIFER_ID = "AQUIFER@I"


def influx_series(we0: float, k1: float, k2: float, imbalance: FArray, dt: FArray) -> FArray:
    """Ẇ_e(n) from the tank ODE, exact for piecewise-constant imbalance over each step."""
    m = len(dt)
    we = np.zeros(m)
    we[0] = we0
    for n in range(1, m):
        e = np.exp(-k1 * dt[n])
        forcing = -k2 * imbalance[n] / k1 if k1 > 0 else -k2 * imbalance[n] * dt[n]
        we[n] = we[n - 1] * e + (1.0 - e) * forcing if k1 > 0 else we[n - 1] + forcing
    return np.maximum(we, 0.0)


def augmented_grid(grid: Grid, we: FArray) -> Grid:
    """The sector grid with the aquifer influx appended as an extra injector column."""
    return replace(
        grid,
        injectors=[*grid.injectors, AQUIFER_ID],
        inj=np.column_stack([grid.inj, we]),
        days_on_inj=np.column_stack([grid.days_on_inj, grid.dt_days]),
        xy_inj=None if grid.xy_inj is None else np.vstack([grid.xy_inj, grid.xy_inj.mean(axis=0, keepdims=True)]),
        raw={},
    )


@dataclass
class AquiferTerms:
    we0: float  # influx at the first step (reservoir rate)
    k1: float  # 1/day
    k2: float  # 1/day
    influx: FArray  # (M,)
    allocation: FArray  # (Np,) a_j
    ct_v_r: float | None = None
    ct_v_aq: float | None = None
    j_aq: float | None = None
    pv_resolvable: bool = False

    def to_dict(self, producers: list[str]) -> dict[str, Any]:
        return {
            "we0": self.we0,
            "k1_per_day": self.k1,
            "k2_per_day": self.k2,
            "influx_first": float(self.influx[0]),
            "influx_last": float(self.influx[-1]),
            "allocation": dict(zip(producers, self.allocation.tolist(), strict=True)),
            "ct_v_r": self.ct_v_r,
            "ct_v_aq": self.ct_v_aq,
            "j_aq": self.j_aq,
            "pv_resolvable": self.pv_resolvable,
        }


class CRMPA(CRMModel):
    name = "CRM-Aquifer (CRMPA)"
    variant = "aquifer"

    def __init__(self, cfg: Config, static_pressure: FArray | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.static_pressure = static_pressure  # (M,) p̄_r on the grid (NaN where absent), optional
        self.terms: AquiferTerms | None = None

    def n_params(self, grid: Grid) -> int:
        return n_params_for("crmp", grid.n_inj + 1, grid.n_prod, grid.bhp is not None) + 3

    def _settings(self, grid: Grid) -> SolverSettings:
        s = SolverSettings.from_config(self.cfg, float(np.min(grid.dt_days[1:])) if grid.n_steps > 1 else 30.0)
        s.free_primary = False
        return s

    def fit(self, data: FitData, seed: int = 0, n_starts: int | None = None) -> FitResult:
        t0 = time.perf_counter()
        grid = data.grid
        n = data.split.n_train
        imbalance = grid.inj.sum(axis=1) - grid.liq.sum(axis=1)
        inner = self._settings(grid)
        inner.n_starts = 2
        inner.varpro_only = True
        inner.joint_sum_bound = (
            1e9  # no SLSQP coupling in the outer loop: a quadratic penalty on injector rows > 1 instead
        )
        var_scale = float(((grid.liq[:n] - grid.liq[:n].mean(axis=0)) ** 2).sum()) or 1.0
        a = self.cfg.section("aquifer")
        field_inj = float(grid.inj[:n].sum(axis=1).mean()) if n else 1.0

        def sse_of(z: FArray) -> float:
            we0, k1, k2 = np.exp(z)
            we = influx_series(we0, k1, k2, imbalance, grid.dt_days)
            res = fit_field(FitData(augmented_grid(grid, we), data.split, data.weights), "crmp", inner, seed)
            rows = res.params.sum_f_per_injector[:-1]  # physical injectors only
            a_sum = float(res.params.sum_f_per_injector[-1])  # aquifer allocation Σ_j a_j
            # closure Σ_j a_j = 1 (all influx is produced somewhere) breaks the Ẇ_e × a_j scale degeneracy
            penalty = (float((np.maximum(rows - 1.0, 0.0) ** 2).sum()) + (a_sum - 1.0) ** 2) * 10.0 * var_scale
            return res.sse_train + penalty

        starts = [np.log([fr * field_inj, k, k * 0.5]) for fr in a["we0_fraction_grid"] for k in a["k_grid_per_day"]]
        best_z, best_v = None, np.inf
        self.trace: list[tuple[float, float, float, float]] = []
        for z0 in starts:
            v = sse_of(z0)
            self.trace.append((float(np.exp(z0[0])), float(np.exp(z0[1])), float(np.exp(z0[2])), v))
            if v < best_v:
                best_z, best_v = z0, v
        assert best_z is not None
        res = minimize(
            sse_of,
            best_z,
            method="Nelder-Mead",
            options={"maxiter": int(a["outer_maxiter"]), "xatol": 1e-3, "fatol": 1e-9},
        )
        z = res.x if res.fun <= best_v else best_z
        we0, k1, k2 = np.exp(z)
        we = influx_series(we0, k1, k2, imbalance, grid.dt_days)
        final = self._settings(grid)
        if n_starts is not None:
            final.n_starts = n_starts
        aug = augmented_grid(grid, we)
        fit = fit_field(FitData(aug, data.split, data.weights), "crmp", final, seed)
        p = fit.params
        alloc = p.f[-1]
        params = ModelParams(
            f=p.f[:-1],
            tau=p.tau,
            J=p.J,
            gain_p=p.gain_p,
            tau_p=p.tau_p,
            extra={"aquifer_allocation": alloc, "influx": we, "we0": we0, "k1": k1, "k2": k2},
        )
        ct_v_r = ct_v_aq = j_aq = None
        resolvable = False
        if self.static_pressure is not None and np.isfinite(self.static_pressure).sum() >= 3:
            ok = np.isfinite(self.static_pressure)
            cum = np.cumsum((we + imbalance) * grid.dt_days)
            x = cum[ok] - cum[ok][0]
            y = self.static_pressure[ok] - self.static_pressure[ok][0]
            denom = float(x @ x)
            if denom > 0:
                slope = float(x @ y) / denom  # Δp̄_r = (1/c_tV_r)·Σ(Ẇ_e + I − Q)Δt
                if slope > 0:
                    ct_v_r = 1.0 / slope
                    j_aq = k2 * ct_v_r
                    ct_v_aq = j_aq / (k1 - k2) if k1 > k2 else None
                    resolvable = ct_v_aq is not None
        self.terms = AquiferTerms(
            we0=we0,
            k1=k1,
            k2=k2,
            influx=we,
            allocation=alloc,
            ct_v_r=ct_v_r,
            ct_v_aq=ct_v_aq,
            j_aq=j_aq,
            pv_resolvable=resolvable,
        )
        params.extra["aquifer"] = self.terms.to_dict(grid.producers)
        self._result = FitResult(
            params=params,
            prediction=fit.prediction,
            sse_train=fit.sse_train,
            n_params=self.n_params(grid),
            runtime_s=time.perf_counter() - t0,
            ensemble=[
                ModelParams(
                    f=m.f[:-1],
                    tau=m.tau,
                    J=m.J,
                    gain_p=m.gain_p,
                    tau_p=m.tau_p,
                    extra={"aquifer_allocation": m.f[-1], "influx": we},
                )
                for m in fit.ensemble
            ],
            starts_sse=fit.starts_sse,
            notes=[*fit.notes, "aquifer influx fitted as a tank-driven pseudo-injector"],
        )
        return self._result

    def predict(self, grid: Grid, params: ModelParams | None = None) -> FArray:
        p = params or self.params
        we = np.asarray(p.extra["influx"], dtype=np.float64)
        if len(we) < grid.n_steps:  # forecast: continue the tank with the plan's imbalance (production ≈ last observed)
            imb = np.concatenate(
                [
                    grid.inj[: len(we)].sum(axis=1) - grid.liq[: len(we)].sum(axis=1),
                    grid.inj[len(we) :].sum(axis=1) - grid.liq[: len(we)].sum(axis=1)[-1],
                ]
            )
            we = influx_series(float(p.extra["we0"]), float(p.extra["k1"]), float(p.extra["k2"]), imb, grid.dt_days)
        full = ModelParams(
            f=np.vstack([p.f, np.asarray(p.extra["aquifer_allocation"])[None, :]]),
            tau=p.tau,
            J=p.J,
            gain_p=p.gain_p,
            tau_p=p.tau_p,
        )
        return predict_field(augmented_grid(grid, we[: grid.n_steps]), full, "crmp")


def static_pressure_on_grid(grid: Grid, surveys: Any) -> FArray | None:
    """Monthly mean of static surveys (well, date, bhp) on the grid — the CRMPA pressure scale."""
    if surveys is None or surveys.is_empty() or "bhp" not in surveys.columns:
        return None
    import polars as pl

    out = np.full(grid.n_steps, np.nan)
    idx = {d: k for k, d in enumerate(grid.dates)}
    monthly = (
        surveys.with_columns(pl.col("date").dt.truncate("1mo").alias("_m")).group_by("_m").agg(pl.col("bhp").mean())
    )
    for d, v in monthly.select(["_m", "bhp"]).iter_rows():
        if d in idx and v is not None:
            out[idx[d]] = float(v)
    return out if np.isfinite(out).any() else None


def fit_data_for(grid: Grid, n_train: int) -> FitData:
    return FitData(grid, Split(n_train, grid.n_steps))
