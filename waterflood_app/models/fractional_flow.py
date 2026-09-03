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
    r2_log: float = 0.0

    def oil_cut(self, cwi: FArray) -> FArray:
        return 1.0 / (1.0 + self.alpha * np.power(np.maximum(cwi, 1e-9), self.beta))


def cumulative_basis(liq_pred: FArray, support: FArray, dt: FArray, basis: str, offset: float = 0.0) -> FArray:
    """Cumulative volume series (per step end) for the oil-cut model."""
    x = support if basis == "allocated_water" else liq_pred
    return offset + np.cumsum(np.asarray(x, dtype=np.float64) * np.asarray(dt, dtype=np.float64))


def fit_power_law(
    cwi: FArray,
    oil: FArray,
    liq: FArray,
    mask: npt.NDArray[np.bool_],
    basis: str = "allocated_water",
) -> PowerLawOilCut:
    """Least squares on ln(WOR) vs ln(CWI) over masked steps with 0 < f_o < 1."""
    with np.errstate(divide="ignore", invalid="ignore"):
        fo = np.where(liq > 0, oil / np.where(liq > 0, liq, 1.0), np.nan)
    ok = mask & np.isfinite(fo) & (fo > 1e-4) & (fo < 1 - 1e-4) & (cwi > 0)
    if ok.sum() < 3:
        mean_fo = float(np.nanmean(fo[mask])) if np.isfinite(fo[mask]).any() else 0.5
        mean_fo = min(max(mean_fo, 1e-3), 1 - 1e-3)
        return PowerLawOilCut(alpha=(1 / mean_fo - 1), beta=0.0, basis=basis)
    y = np.log(1.0 / fo[ok] - 1.0)
    x = np.log(cwi[ok])
    A = np.column_stack([np.ones_like(x), x])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    beta = float(max(coef[1], 0.0))
    return PowerLawOilCut(alpha=float(np.exp(coef[0])), beta=beta, basis=basis, r2_log=1.0 - ss_res / ss_tot)


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
