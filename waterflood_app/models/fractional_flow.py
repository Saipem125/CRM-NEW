"""Oil-cut coupling — Atlas "CRMP" §2 (power-law WOR–CWI) and Koval (CRMPAF, Atlas "CRM–Aquifer" §1).

Power law::

    f_o(t) = 1 / (1 + α · CWI(t)^β)        ⇔   ln WOR = ln α + β ln CWI

fitted by least squares in log space on training steps with 0 < f_o < 1. CWI is a cumulative
volume per producer; the basis is configurable: cumulative *allocated* water Σ_i f_ij·i_i
(Atlas default) or cumulative liquid produced (some field data sets are keyed that way).

Koval (Koval 1963; used by CRMPAF): with t_D = cumulative injected / displaceable pore volume,

    f_w = 0                                  t_D < 1/K
    f_w = (K − sqrt(K/t_D)) / (K − 1)        1/K ≤ t_D ≤ K
    f_w = 1                                  t_D > K

parameters K (heterogeneity factor, ≥ 1) and V_pd (displaceable pore volume).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

FArray = npt.NDArray[np.float64]


# --------------------------------------------------------------------------------------
# Power-law WOR–CWI
# --------------------------------------------------------------------------------------
@dataclass
class PowerLawOilCut:
    alpha: float
    beta: float
    basis: str = "allocated_water"  # | "liquid_produced"
    offset: float = 0.0  # cumulative volume before the history starts (same unit as CWI)
    r2_log: float = 0.0

    def oil_cut(self, cwi: FArray) -> FArray:
        with np.errstate(over="ignore", invalid="ignore"):
            wor = self.alpha * np.power(np.maximum(self.offset + cwi, 1e-9), self.beta)
        # a runaway fit (α·CWI^β overflowing) means "all water", never NaN in a forecast
        return np.asarray(1.0 / (1.0 + np.nan_to_num(wor, nan=np.inf, posinf=np.inf)), dtype=np.float64)


def cumulative_basis(liq_pred: FArray, support: FArray, dt: FArray, basis: str, offset: float = 0.0) -> FArray:
    """Cumulative volume series (per step end) for the oil-cut model."""
    x = support if basis == "allocated_water" else liq_pred
    return offset + np.cumsum(np.asarray(x, dtype=np.float64) * np.asarray(dt, dtype=np.float64))


def _loglinear(cwi: FArray, fo: FArray, ok: npt.NDArray[np.bool_], offset: float) -> tuple[float, float, float, float]:
    """Least squares of ln(1/f_o − 1) on ln(offset + CWI). Returns (alpha, beta, sse, ss_tot)."""
    y = np.log(1.0 / fo[ok] - 1.0)
    x = np.log(offset + cwi[ok])
    A = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    return (
        float(np.exp(min(float(coef[0]), 700.0))),  # ln α capped: exp overflow on degenerate wells
        float(np.clip(coef[1], -50.0, 50.0)),
        float(((y - pred) ** 2).sum()),
        float(((y - y.mean()) ** 2).sum()) or 1.0,
    )


def fit_power_law(
    cwi: FArray,
    oil: FArray,
    liq: FArray,
    mask: npt.NDArray[np.bool_],
    basis: str = "allocated_water",
    fit_offset: bool = True,
) -> PowerLawOilCut:
    """Fit α, β (and the pre-history cumulative offset C0) on masked steps with 0 < f_o < 1.

    ln(1/f_o − 1) = ln α + β ln(C0 + CWI): linear in (ln α, β) for fixed C0, so C0 is searched on
    a log grid and refined with a bounded scalar minimiser (variable projection).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        fo = np.where(liq > 0, oil / np.where(liq > 0, liq, 1.0), np.nan)
    ok = mask & np.isfinite(fo) & (fo > 1e-4) & (fo < 1 - 1e-4) & (cwi >= 0)
    if ok.sum() < 3:
        mean_fo = float(np.nanmean(fo[mask])) if np.isfinite(fo[mask]).any() else 0.5
        mean_fo = min(max(mean_fo, 1e-3), 1 - 1e-3)
        return PowerLawOilCut(alpha=(1 / mean_fo - 1), beta=0.0, basis=basis)
    cmax = float(np.nanmax(cwi[ok])) or 1.0
    ok = ok & (cwi > 0) if not fit_offset else ok
    best: tuple[float, float, float, float, float] | None = None  # (sse, offset, alpha, beta, ss_tot)
    offsets = (
        [0.0] if not fit_offset else [0.0, *np.logspace(np.log10(cmax * 1e-3), np.log10(cmax * 50.0), 24).tolist()]
    )
    for c0 in offsets:
        if (c0 + cwi[ok] <= 0).any():
            continue
        alpha, beta, sse, ss_tot = _loglinear(cwi, fo, ok, c0)
        if best is None or sse < best[0]:
            best = (sse, c0, alpha, beta, ss_tot)
    assert best is not None
    if fit_offset and best[1] > 0:
        from scipy.optimize import minimize_scalar

        lo, hi = best[1] / 3.0, best[1] * 3.0
        res = minimize_scalar(
            lambda z: _loglinear(cwi, fo, ok, float(np.exp(z)))[2], bounds=(np.log(lo), np.log(hi)), method="bounded"
        )
        c0 = float(np.exp(res.x))
        alpha, beta, sse, ss_tot = _loglinear(cwi, fo, ok, c0)
        if sse <= best[0]:
            best = (sse, c0, alpha, beta, ss_tot)
    sse, c0, alpha, beta, ss_tot = best
    return PowerLawOilCut(alpha=alpha, beta=max(beta, 0.0), basis=basis, offset=c0, r2_log=1.0 - sse / ss_tot)


# --------------------------------------------------------------------------------------
# Koval
# --------------------------------------------------------------------------------------
@dataclass
class KovalFractionalFlow:
    k_val: float  # heterogeneity factor ≥ 1
    v_pd: float  # displaceable pore volume (same volume unit as the cumulative input)

    def water_cut(self, cum_injected: FArray) -> FArray:
        td = np.maximum(np.asarray(cum_injected, dtype=np.float64), 0.0) / max(self.v_pd, 1e-9)
        k = max(self.k_val, 1.0 + 1e-9)
        with np.errstate(divide="ignore", invalid="ignore"):
            mid = (k - np.sqrt(k / np.where(td > 0, td, 1e-12))) / (k - 1.0)
        fw = np.where(td < 1.0 / k, 0.0, np.where(td > k, 1.0, mid))
        return np.clip(fw, 0.0, 1.0)

    def oil_cut(self, cum_injected: FArray) -> FArray:
        return 1.0 - self.water_cut(cum_injected)


def fit_koval(cum_injected: FArray, water_cut: FArray, mask: npt.NDArray[np.bool_]) -> KovalFractionalFlow:
    """Fit (K, V_pd) by bounded least squares on the observed water cut."""
    ok = mask & np.isfinite(water_cut) & (cum_injected > 0)
    if ok.sum() < 3:
        return KovalFractionalFlow(k_val=2.0, v_pd=float(cum_injected[ok].max()) if ok.any() else 1.0)
    x, y = cum_injected[ok], np.clip(water_cut[ok], 0.0, 1.0)

    def loss(p: FArray) -> float:
        model = KovalFractionalFlow(k_val=float(np.exp(p[0])) + 1.0, v_pd=float(np.exp(p[1])))
        return float(((model.water_cut(x) - y) ** 2).sum())

    best = None
    for k0 in (1.5, 3.0, 8.0):
        for v0 in (x.max() * 0.5, x.max(), x.max() * 3.0):
            res = minimize(
                loss,
                np.array([np.log(k0 - 1.0), np.log(v0)]),
                method="Nelder-Mead",
                options={"xatol": 1e-6, "fatol": 1e-10, "maxiter": 2000},
            )
            if best is None or res.fun < best.fun:
                best = res
    assert best is not None
    return KovalFractionalFlow(k_val=float(np.exp(best.x[0])) + 1.0, v_pd=float(np.exp(best.x[1])))
