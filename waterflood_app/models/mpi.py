"""Multiwell Productivity Index — Atlas "MPI" tab (Valkó et al. 2000; Kaviani & Valkó 2010); §11.

    d⃗ = (α₁ μ B / 2π k h) ([A] + [Δ_s]) q⃗   ⇒   q⃗ = [J] d⃗,   [J] = (2π k h / α₁ μ B) ([A] + [Δ_s])⁻¹

[A] is the influence matrix of line sources in a closed rectangular reservoir (x_e × y_e):

    a(x_D, y_D; x_wD, y_wD) = 2π y_eD (1/3 − y_D/y_eD + (y_D² + y_wD²)/(2 y_eD²))
        + Σ_{m≥1} (2/m) cos(mπ x_D) cos(mπ x_wD)
          · [cosh(mπ(y_eD − |y_D − y_wD|)) + cosh(mπ(y_eD − y_D − y_wD))] / sinh(mπ y_eD)

with the diagonal evaluated at r_w. Used here as a *prior* on connectivity (no rate history):
the influence of injector i on producer j, normalised per injector and scaled to the
distance–f_ij trend among fitted pairs in the same sector (§11), gives f_ij^prior for new
injectors, conversions and infill producers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

FArray = npt.NDArray[np.float64]


def influence(x_d: float, y_d: float, xw_d: float, yw_d: float, ye_d: float, n_terms: int = 200) -> float:
    """Dimensionless influence a(·) of a line source at (xw, yw) on the point (x, y), closed rectangle.

    The series form holds for y_D ≥ y_wD; the two y arguments are ordered so that a is symmetric.
    """
    y_d, yw_d = max(y_d, yw_d), min(y_d, yw_d)
    base = 2.0 * np.pi * ye_d * (1.0 / 3.0 - y_d / ye_d + (y_d**2 + yw_d**2) / (2.0 * ye_d**2))
    m = np.arange(1, n_terms + 1, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        num = np.cosh(m * np.pi * (ye_d - abs(y_d - yw_d))) + np.cosh(m * np.pi * (ye_d - y_d - yw_d))
        den = np.sinh(m * np.pi * ye_d)
        ratio = np.where(np.isfinite(num / den), num / den, np.exp(-m * np.pi * min(abs(y_d - yw_d), y_d + yw_d)) + 0.0)
    series = (2.0 / m) * np.cos(m * np.pi * x_d) * np.cos(m * np.pi * xw_d) * ratio
    return float(base + series.sum())


@dataclass
class MPIResult:
    influence: FArray  # (n, n) [A] + [Δ_s]
    J: FArray  # (n, n) productivity-index matrix
    wells: list[str]
    x_e: float
    y_e: float


def mpi_matrix(
    xy: FArray,
    wells: list[str],
    k_md: float = 100.0,
    h_m: float = 10.0,
    mu_cp: float = 1.0,
    b_fvf: float = 1.0,
    skin: FArray | None = None,
    r_w: float = 0.1,
    margin: float = 0.5,
    n_terms: int = 200,
) -> MPIResult:
    """[J] for wells at ``xy`` (metres) in a closed rectangle enclosing them with a margin (fraction of extent)."""
    xy = np.asarray(xy, dtype=np.float64)
    n = len(wells)
    xmin, ymin = xy.min(axis=0)
    xmax, ymax = xy.max(axis=0)
    dx = max(xmax - xmin, 1.0)
    dy = max(ymax - ymin, 1.0)
    x0, y0 = xmin - margin * dx, ymin - margin * dy
    x_e, y_e = dx * (1 + 2 * margin), dy * (1 + 2 * margin)
    ye_d = y_e / x_e
    A = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            xi, yi = (xy[i, 0] - x0) / x_e, (xy[i, 1] - y0) / x_e
            xj, yj = (xy[j, 0] - x0) / x_e, (xy[j, 1] - y0) / x_e
            if i == j:
                A[i, i] = influence(xi + r_w / x_e, yi, xi, yi, ye_d, n_terms)
            else:
                A[i, j] = influence(xi, yi, xj, yj, ye_d, n_terms)
    if skin is not None:
        A = A + np.diag(np.asarray(skin, dtype=np.float64))
    alpha1 = 1.0  # unit-system constant absorbed in the scale; the prior only needs relative values
    scale = 2.0 * np.pi * k_md * h_m / (alpha1 * mu_cp * b_fvf)
    try:
        J = np.asarray(scale * np.linalg.inv(A), dtype=np.float64)
    except np.linalg.LinAlgError:
        J = np.asarray(scale * np.linalg.pinv(A), dtype=np.float64)
    return MPIResult(influence=A, J=J, wells=list(wells), x_e=x_e, y_e=y_e)


def prior_allocation(xy_inj: FArray, xy_prod: FArray, **kw: Any) -> FArray:
    """f_ij prior (Ni, Np) from the MPI coupling: |J_ij| between injector i and producer j, normalised per injector."""
    xy = np.vstack([xy_inj, xy_prod])
    wells = [f"I{k}" for k in range(len(xy_inj))] + [f"P{k}" for k in range(len(xy_prod))]
    res = mpi_matrix(xy, wells, **kw)
    ni = len(xy_inj)
    coupling = np.abs(res.J[:ni, ni:])
    rows = coupling.sum(axis=1, keepdims=True)
    return np.asarray(np.where(rows > 0, coupling / np.maximum(rows, 1e-12), 0.0), dtype=np.float64)


def distance_trend(f: FArray, d: FArray, min_pairs: int = 4) -> tuple[float, float] | None:
    """ln f = a + b·d fitted on pairs with f > 0.02 (the observed distance–f_ij trend, §11)."""
    ok = (f > 0.02) & np.isfinite(d)
    if ok.sum() < min_pairs:
        return None
    A = np.column_stack([np.ones(int(ok.sum())), d[ok]])
    coef, *_ = np.linalg.lstsq(A, np.log(f[ok]), rcond=None)
    return float(coef[0]), float(coef[1])


def scaled_prior(f_prior_row: FArray, d_row: FArray, trend: tuple[float, float] | None, row_sum: float) -> FArray:
    """Blend the MPI shape with the sector's distance trend and normalise the row to ``row_sum``."""
    shape = f_prior_row.copy()
    if trend is not None:
        a, b = trend
        expected = np.exp(a + b * d_row)
        shape = np.sqrt(np.maximum(shape, 1e-12) * np.maximum(expected, 1e-12))
    tot = shape.sum()
    return np.asarray(shape / tot * row_sum if tot > 0 else shape, dtype=np.float64)


def new_injector_prior(
    xy_new: FArray, xy_inj: FArray, xy_prod: FArray, f_fitted: FArray, row_sum: float | None = None, **kw: Any
) -> tuple[FArray, float]:
    """f_prior row (Np,) for a new injector at xy_new; confidence MEDIUM at best (§11)."""
    d_fit = np.linalg.norm(xy_inj[:, None, :] - xy_prod[None, :, :], axis=2)
    trend = distance_trend(f_fitted.ravel(), d_fit.ravel())
    allocation = prior_allocation(np.vstack([xy_inj, xy_new[None, :]]), xy_prod, **kw)[-1]
    d_new = np.linalg.norm(xy_prod - xy_new[None, :], axis=1)
    rs = float(np.median(f_fitted.sum(axis=1))) if row_sum is None else row_sum
    return scaled_prior(allocation, d_new, trend, rs), float(
        np.median(np.linalg.norm(xy_inj - xy_new[None, :], axis=1))
    )


def conversion_prior(producer_index: int, xy_inj: FArray, xy_prod: FArray, f_fitted: FArray, **kw: Any) -> FArray:
    """A producer converted to injector: f row to its neighbours from the MPI-scaled prior (§11)."""
    keep = [k for k in range(len(xy_prod)) if k != producer_index]
    row, _ = new_injector_prior(xy_prod[producer_index], xy_inj, xy_prod[keep], f_fitted[:, keep], **kw)
    return row


def infill_split(xy_new: FArray, xy_inj: FArray, xy_prod: FArray, f_fitted: FArray, **kw: Any) -> tuple[FArray, FArray]:
    """Infill producer: MPI-based split of the neighbouring injectors' allocation (LOW → re-fit after 12 months).

    Returns (f column for the new producer, adjusted existing f) with each injector's row sum preserved.
    """
    allocation = prior_allocation(xy_inj, np.vstack([xy_prod, xy_new[None, :]]), **kw)
    share = allocation[:, -1]  # fraction of each injector's coupling that goes to the new well
    new_col = f_fitted.sum(axis=1) * share
    adjusted = f_fitted * (1.0 - share)[:, None]
    return np.asarray(new_col, dtype=np.float64), np.asarray(adjusted, dtype=np.float64)
