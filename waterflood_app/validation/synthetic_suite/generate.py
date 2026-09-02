"""Synthetic ground-truth suite generator — architecture §16 Tier 1.

Five cases with *known* CRM parameters, frozen as Parquet + ``truth.json``:

``streak_5x4``      5 injectors, 4 producers, one high-permeability streak (I-3 → P-3),
                    120 monthly steps; ``noise_free`` and ``noise_5pct`` variants.
``aquifer_6x9``     6 injectors, 9 producers, partial natural water drive from a finite
                    Fetkovich aquifer tank (Atlas "CRM–Aquifer" tab), 160 steps; the
                    per-producer Σ_i f_ij that a plain CRMP would see is ≈ 1.4.
``converted_wells`` 4 injectors, 6 producers; P-5 and P-6 are converted to injectors at
                    month 48 (simultaneous P+I in the conversion month), 96 steps.
``allocated_noisy`` 5×4 field, monthly-allocated volumes with days-on (shut-ins and partial
                    months) and 8 % allocation noise, 120 steps.
``sectored_60``     60 wells (2 × [12 injectors + 18 producers]) in two sealed sectors
                    separated by a fault, 120 steps.

Forward model (Atlas "CRMP" tab, Sayarpour et al. 2009, discrete analytical solution)::

    q_j(n) = q_j(n-1)·exp(-Δt_n/τ_j) + (1 − exp(-Δt_n/τ_j)) · Σ_i f_ij·i_i(n)

with the pair-allocation mass constraint Σ_j f_ij ≤ 1 per injector (see DECISIONS.md) and
the power-law oil-cut model of the Atlas CRMP tab::

    f_o(t) = 1 / (1 + α·CWI(t)^β)

where CWI is the cumulative water allocated to the producer (in Mbbl).

The suite is *immutable* after Milestone 0: ``manifest.json`` stores the SHA-256 of every
Parquet file and ``tests/test_fixtures.py`` asserts they have not changed.

Run ``python -m waterflood_app.validation.synthetic_suite.generate`` to regenerate
(only when intentionally re-freezing; bump ``GENERATOR_VERSION``).
"""

from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl
from scipy.optimize import minimize

GENERATOR_VERSION = "1.0.0"
SUITE_DIR = Path(__file__).resolve().parent
START = date(2010, 1, 1)
MBBL = 1000.0

FArray = npt.NDArray[np.float64]


# --------------------------------------------------------------------------------------
# Time grid
# --------------------------------------------------------------------------------------
def month_grid(n_steps: int, start: date = START) -> tuple[list[date], FArray]:
    """Monthly grid (§8 default): first-of-month dates and Δt_n = t_n − t_{n−1} in days.

    Δt_n is the length of the interval that *ends* at grid point n (the days in month n−1);
    Δt_0 is set equal to the length of month 0 and is never used by the recursion. This matches
    ``synthetic_suite.to_matrices`` and the convolution convention of pywaterflood.
    """
    dates: list[date] = []
    y, m = start.year, start.month
    for _ in range(n_steps):
        dates.append(date(y, m, 1))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    dt = np.array(
        [
            float((dates[k] - dates[k - 1]).days)
            if k > 0
            else float(calendar.monthrange(dates[0].year, dates[0].month)[1])
            for k in range(n_steps)
        ],
        dtype=np.float64,
    )
    return dates, dt


# --------------------------------------------------------------------------------------
# Signals and forward model
# --------------------------------------------------------------------------------------
def injection_signal(
    rng: np.random.Generator,
    n_steps: int,
    n_inj: int,
    base: float,
    step_sigma: float = 0.35,
    block_len: tuple[int, int] = (4, 10),
    meter_noise: float = 0.01,
) -> FArray:
    """Piecewise-constant injection with random re-targets (CV well above the §7 gate of 0.15)."""
    out = np.zeros((n_steps, n_inj))
    for i in range(n_inj):
        t = 0
        while t < n_steps:
            length = int(rng.integers(block_len[0], block_len[1] + 1))
            level = base * max(0.3, 1.0 + step_sigma * rng.standard_normal())
            out[t : t + length, i] = level
            t += length
    out *= 1.0 + meter_noise * rng.standard_normal(out.shape)
    return out


def crmp_forward(inj: FArray, f: FArray, tau: FArray, q0: FArray, dt: FArray) -> FArray:
    """CRMP discrete solution (Atlas CRMP tab; Sayarpour 2009).

    inj: (M, Ni) injection rates; f: (Ni, Np) allocation; tau: (Np,) days; q0: (Np,) initial
    liquid rate; dt: (M,) step lengths Δt_n = t_n − t_{n−1} in days. Returns liquid rates q (M, Np).
    Step 0 reports q0, the state at the first grid point: i_i(0) is the injection of the interval
    that ended at t_0, whose effect is already contained in q0 (Sayarpour 2009 convention).
    """
    n_steps = inj.shape[0]
    q = np.zeros((n_steps, f.shape[1]))
    q[0] = q0
    support = inj @ f
    for n in range(1, n_steps):
        e = np.exp(-dt[n] / tau)
        q[n] = q[n - 1] * e + (1.0 - e) * support[n]
    return q


def oil_cut(cwi_mbbl: FArray, alpha: FArray, beta: FArray) -> FArray:
    """Power-law WOR–CWI oil cut f_o = 1/(1+α·CWI^β) (Atlas CRMP tab §2)."""
    return 1.0 / (1.0 + alpha * np.power(np.maximum(cwi_mbbl, 1e-9), beta))


def split_oil_water(
    q_liq: FArray,
    support: FArray,
    dt: FArray,
    alpha: FArray,
    beta: FArray,
    cwi0: FArray,
) -> tuple[FArray, FArray, FArray]:
    """Split liquid into oil and water using cumulative allocated water per producer."""
    cwi = cwi0 + np.cumsum(support * dt[:, None], axis=0) / MBBL
    fo = oil_cut(cwi, alpha, beta)
    return q_liq * fo, q_liq * (1.0 - fo), cwi


def distance_allocation(
    xy_inj: FArray,
    xy_prod: FArray,
    length_scale: float,
    per_injector_sum: float,
    cutoff: float,
) -> FArray:
    """Distance-decay allocation f_ij ∝ exp(-d/L), zero beyond cutoff, Σ_j f_ij = "
    "per_injector_sum."""
    d = np.linalg.norm(xy_inj[:, None, :] - xy_prod[None, :, :], axis=2)
    f = np.exp(-d / length_scale)
    f[d > cutoff] = 0.0
    row = f.sum(axis=1, keepdims=True)
    row[row == 0] = 1.0
    return np.asarray(f / row * per_injector_sum, dtype=np.float64)


# --------------------------------------------------------------------------------------
# Case container
# --------------------------------------------------------------------------------------
@dataclass
class SyntheticCase:
    """Everything one fixture writes to disk."""

    name: str
    rates: pl.DataFrame
    coords: pl.DataFrame
    category: pl.DataFrame
    events: pl.DataFrame
    truth: dict[str, Any]
    pressure: pl.DataFrame | None = None
    extra_frames: dict[str, pl.DataFrame] = field(default_factory=dict)


def _rates_frame(
    dates: list[date],
    dt: FArray,
    inj_ids: list[str],
    prod_ids: list[str],
    inj: FArray,
    q_oil: FArray,
    q_water: FArray,
    days_on_prod: FArray | None = None,
    days_on_inj: FArray | None = None,
) -> pl.DataFrame:
    """Long-format rates table (DECISIONS.md schema). Rates are calendar-day bbl/d."""
    rows: list[dict[str, Any]] = []
    n = len(dates)
    for k, wid in enumerate(inj_ids):
        for t in range(n):
            don = dt[t] if days_on_inj is None else days_on_inj[t, k]
            rows.append(
                {
                    "well_id": wid,
                    "date": dates[t],
                    "days_on": float(don),
                    "q_oil": 0.0,
                    "q_water": 0.0,
                    "q_inj": float(inj[t, k]),
                    "bhp": None,
                }
            )
    for k, wid in enumerate(prod_ids):
        for t in range(n):
            don = dt[t] if days_on_prod is None else days_on_prod[t, k]
            rows.append(
                {
                    "well_id": wid,
                    "date": dates[t],
                    "days_on": float(don),
                    "q_oil": float(q_oil[t, k]),
                    "q_water": float(q_water[t, k]),
                    "q_inj": 0.0,
                    "bhp": None,
                }
            )
    return pl.DataFrame(
        rows,
        schema={
            "well_id": pl.Utf8,
            "date": pl.Date,
            "days_on": pl.Float64,
            "q_oil": pl.Float64,
            "q_water": pl.Float64,
            "q_inj": pl.Float64,
            "bhp": pl.Float64,
        },
    ).sort(["well_id", "date"])


def _coords_frame(ids: list[str], xy: FArray) -> pl.DataFrame:
    return pl.DataFrame(
        {"well_id": ids, "x": xy[:, 0].tolist(), "y": xy[:, 1].tolist()},
        schema={"well_id": pl.Utf8, "x": pl.Float64, "y": pl.Float64},
    )


def _category_frame(ids: list[str], block: list[str], field_name: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "well_id": ids,
            "field": [field_name] * len(ids),
            "reservoir": ["R1"] * len(ids),
            "block": block,
            "alloc_factor": [1.0] * len(ids),
        },
        schema={
            "well_id": pl.Utf8,
            "field": pl.Utf8,
            "reservoir": pl.Utf8,
            "block": pl.Utf8,
            "alloc_factor": pl.Float64,
        },
    )


def _events_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"well_id": pl.Utf8, "date": pl.Date, "event": pl.Utf8, "note": pl.Utf8},
    )


def _f_dict(inj_ids: list[str], prod_ids: list[str], f: FArray) -> dict[str, dict[str, float]]:
    return {
        inj_ids[i]: {prod_ids[j]: round(float(f[i, j]), 6) for j in range(len(prod_ids))}
        for i in range(len(inj_ids))
    }


# --------------------------------------------------------------------------------------
# Optimizer ground truth (used by the Milestone 2 acceptance test)
# --------------------------------------------------------------------------------------
def true_optimal_reallocation(
    f: FArray,
    tau: FArray,
    q_last: FArray,
    cwi_last: FArray,
    alpha: FArray,
    beta: FArray,
    inj_current: FArray,
    horizon_months: int = 24,
    extra_support: FArray | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    """Cumulative-oil-maximising constant reallocation with the same total water.

    Objective: Σ_n Σ_j q_j(n)·f_o(CWI_j(n))·Δt over a 24-month horizon (§12 "max cumulative
    oil"). Constraints: Σ_i x_i = Σ_i current; 0 ≤ x_i ≤ 2·mean(current). Baselines recorded:
    equal split of the same water and hold-current.
    """
    n_inj = f.shape[0]
    dt = np.full(horizon_months, 365.25 / 12.0)
    total = float(inj_current.sum())
    ub = 2.0 * total / n_inj
    extra = np.zeros(f.shape[1]) if extra_support is None else extra_support

    def cum_oil(x: FArray) -> float:
        inj = np.tile(x, (horizon_months, 1))
        support = inj @ f + extra[None, :]
        q = crmp_forward(inj, f, tau, q_last, dt)
        # continue the CRMP recursion from the last historical state (q_last)
        support_cum = np.cumsum(support * dt[:, None], axis=0) / MBBL
        fo = oil_cut(cwi_last + support_cum, alpha, beta)
        return float((q * fo * dt[:, None]).sum())

    def neg(x: FArray) -> float:
        return -cum_oil(x)

    cons = [{"type": "eq", "fun": lambda x: float(x.sum() - total)}]
    bounds = [(0.0, ub)] * n_inj
    rng = np.random.default_rng(seed)
    starts = [inj_current.copy(), np.full(n_inj, total / n_inj)]
    for _ in range(6):
        w = rng.dirichlet(np.ones(n_inj))
        starts.append(np.minimum(w * total, ub))
    best_x, best_val = inj_current.copy(), -np.inf
    for x0 in starts:
        res = minimize(neg, x0, method="SLSQP", bounds=bounds, constraints=cons)
        if res.success and -res.fun > best_val:
            best_x, best_val = np.asarray(res.x, dtype=np.float64), float(-res.fun)
    oil_hold = cum_oil(inj_current)
    oil_equal = cum_oil(np.full(n_inj, total / n_inj))
    return {
        "objective": "max_cumulative_oil",
        "horizon_months": horizon_months,
        "total_water_bbl_d": total,
        "bounds_bbl_d": [0.0, ub],
        "baseline_equal_split_oil_bbl": oil_equal,
        "baseline_hold_current_oil_bbl": oil_hold,
        "optimal_oil_bbl": best_val,
        "optimal_rates_bbl_d": [float(v) for v in best_x],
        "gain_vs_equal_split_pct": 100.0 * (best_val - oil_equal) / oil_equal,
        "gain_vs_hold_current_pct": 100.0 * (best_val - oil_hold) / oil_hold,
    }


# --------------------------------------------------------------------------------------
# Case builders
# --------------------------------------------------------------------------------------
def _simulate_crmp_case(
    rng: np.random.Generator,
    n_steps: int,
    inj_ids: list[str],
    prod_ids: list[str],
    f: FArray,
    tau: FArray,
    alpha: FArray,
    beta: FArray,
    inj_base: float,
    q0_factor: float = 0.8,
    extra_support: FArray | None = None,
) -> dict[str, Any]:
    """Common CRMP forward run; returns arrays for framing."""
    dates, dt = month_grid(n_steps)
    inj = injection_signal(rng, n_steps, len(inj_ids), inj_base)
    support = inj @ f
    if extra_support is not None:
        support = support + extra_support
    q0 = q0_factor * support[0]
    q_liq = np.zeros((n_steps, len(prod_ids)))
    q_liq[0] = q0
    for n in range(1, n_steps):
        e = np.exp(-dt[n] / tau)
        q_liq[n] = q_liq[n - 1] * e + (1.0 - e) * support[n]
    cwi0 = np.full(len(prod_ids), 50.0)  # Mbbl already injected before the history starts
    q_oil, q_water, cwi = split_oil_water(q_liq, support, dt, alpha, beta, cwi0)
    return {
        "dates": dates,
        "dt": dt,
        "inj": inj,
        "support": support,
        "q0": q0,
        "q_liq": q_liq,
        "q_oil": q_oil,
        "q_water": q_water,
        "cwi": cwi,
        "cwi0": cwi0,
    }


def _common_truth(
    name: str,
    description: str,
    seed: int,
    sim: dict[str, Any],
    inj_ids: list[str],
    prod_ids: list[str],
    f: FArray,
    tau: FArray,
    alpha: FArray,
    beta: FArray,
    expected_variant: str,
    notes: list[str],
    n_inj_per_sector: int | None = None,
) -> dict[str, Any]:
    inj_cv = (sim["inj"].std(axis=0) / sim["inj"].mean(axis=0)).tolist()
    n_inj_eff = len(inj_ids) if n_inj_per_sector is None else n_inj_per_sector
    truth: dict[str, Any] = {
        "case": name,
        "description": description,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "n_steps": len(sim["dates"]),
        "dt_days_mean": float(np.mean(sim["dt"])),
        "injectors": inj_ids,
        "producers": prod_ids,
        "f_ij": _f_dict(inj_ids, prod_ids, f),
        "tau_days": {p: float(t) for p, t in zip(prod_ids, tau, strict=True)},
        "q0_bbl_d": {p: float(v) for p, v in zip(prod_ids, sim["q0"], strict=True)},
        "oil_cut": {
            "model": "f_o = 1/(1+alpha*CWI^beta), CWI in Mbbl (Atlas CRMP tab)",
            "alpha": {p: float(a) for p, a in zip(prod_ids, alpha, strict=True)},
            "beta": {p: float(b) for p, b in zip(prod_ids, beta, strict=True)},
            "cwi0_mbbl": {p: float(c) for p, c in zip(prod_ids, sim["cwi0"], strict=True)},
        },
        "sum_f_per_injector": {i: float(f[k].sum()) for k, i in enumerate(inj_ids)},
        "sum_f_per_producer": {p: float(f[:, k].sum()) for k, p in enumerate(prod_ids)},
        "injection_cv": {i: float(c) for i, c in zip(inj_ids, inj_cv, strict=True)},
        "tau_over_dt_min": float(tau.min() / np.mean(sim["dt"])),
        "identifiability": {
            "O_d": float(len(sim["dates"]) / (n_inj_eff + 1)),
            "O_d_basis": "per sector" if n_inj_per_sector else "whole field",
            "O_d_field": float(len(sim["dates"]) / (len(inj_ids) + 1)),
            "points_per_param_crmp": float(len(sim["dates"]) / (n_inj_eff + 3)),
        },
        "expected_variant": expected_variant,
        "tournament_notes": notes,
        "noise": {"production_pct": 0.0, "injection_pct": 1.0, "allocation_pct": 0.0},
    }
    return truth


def build_streak_5x4(noise_pct: float = 0.0) -> SyntheticCase:
    """5 injectors in a row, 4 producers offset; high-permeability streak I-3 → P-3."""
    seed = 5401
    rng = np.random.default_rng(seed)
    inj_ids = [f"I-{k}" for k in range(1, 6)]
    prod_ids = [f"P-{k}" for k in range(1, 5)]
    xy_inj = np.array([[0.0, 0.0], [500.0, 0.0], [1000.0, 0.0], [1500.0, 0.0], [2000.0, 0.0]])
    xy_prod = np.array([[250.0, 600.0], [750.0, 600.0], [1250.0, 600.0], [1750.0, 600.0]])
    f = distance_allocation(
        xy_inj, xy_prod, length_scale=450.0, per_injector_sum=0.9, cutoff=1300.0
    )
    # streak: I-3 sends 75 % of its water to P-3; its other pairs share the remaining 15 %
    others = f[2].copy()
    others[2] = 0.0
    f[2] = others / others.sum() * 0.15
    f[2, 2] = 0.75
    # sealing barrier between I-5 and P-1 (already far) and I-1 / P-4 — exact zeros
    f[4, 0] = 0.0
    f[0, 3] = 0.0
    tau = np.array([150.0, 110.0, 95.0, 180.0])
    alpha = np.array([0.0035, 0.0030, 0.0050, 0.0028])
    beta = np.array([1.25, 1.30, 1.20, 1.35])
    n_steps = 120
    sim = _simulate_crmp_case(rng, n_steps, inj_ids, prod_ids, f, tau, alpha, beta, inj_base=1500.0)
    q_oil, q_water = sim["q_oil"], sim["q_water"]
    if noise_pct > 0:
        nrng = np.random.default_rng(seed + 1)
        q_oil = q_oil * (1.0 + noise_pct / 100.0 * nrng.standard_normal(q_oil.shape))
        q_water = q_water * (1.0 + noise_pct / 100.0 * nrng.standard_normal(q_water.shape))
        q_oil, q_water = np.maximum(q_oil, 0.0), np.maximum(q_water, 0.0)
    name = "streak_5x4" if noise_pct == 0 else "streak_5x4_noise5"
    truth = _common_truth(
        name,
        "5 injectors × 4 producers, one high-permeability streak I-3→P-3 (f=0.75), two barriers",
        seed,
        sim,
        inj_ids,
        prod_ids,
        f,
        tau,
        alpha,
        beta,
        expected_variant="CRMP",
        notes=[
            "All gates pass (O_d=20, CV>0.15, tau/dt>=3); CRMP beats CRMIP on AICc because the "
            "truth is CRMP.",
            "CRMT eligible but under-fits per-well; aquifer variant not triggered (sum_f per "
            "producer < 1.15).",
        ],
    )
    truth["noise"]["production_pct"] = noise_pct
    truth["streak"] = {"injector": "I-3", "producer": "P-3", "f_ij": 0.75}
    truth["barriers"] = [["I-5", "P-1"], ["I-1", "P-4"]]
    truth["optimizer"] = true_optimal_reallocation(
        f, tau, sim["q_liq"][-1], sim["cwi"][-1], alpha, beta, sim["inj"][-1]
    )
    rates = _rates_frame(sim["dates"], sim["dt"], inj_ids, prod_ids, sim["inj"], q_oil, q_water)
    ids = inj_ids + prod_ids
    coords = _coords_frame(ids, np.vstack([xy_inj, xy_prod]))
    category = _category_frame(ids, ["B1"] * len(ids), "STREAK")
    events = _events_frame([])
    return SyntheticCase(name, rates, coords, category, events, truth)


def build_aquifer_6x9(
    j_aq: float = 14.0,
    p_aq0: float = 3400.0,
    p_r0: float = 3000.0,
    ct_v_r: float = 4.0e4,
    ct_v_aq: float = 2.5e5,
) -> SyntheticCase:
    """6 injectors, 9 producers, finite Fetkovich aquifer on the east edge (Atlas CRM–Aquifer).

    j_aq [bbl/d/psi], p_aq0 / p_r0 [psi], ct_v_r / ct_v_aq [bbl/psi] = c_t·V_p of
    reservoir / aquifer.
    """
    seed = 6901
    rng = np.random.default_rng(seed)
    inj_ids = [f"I-{k}" for k in range(1, 7)]
    prod_ids = [f"P-{k}" for k in range(1, 10)]
    xy_prod = np.array([[x, y] for y in (0.0, 1000.0, 2000.0) for x in (0.0, 1000.0, 2000.0)])
    xy_inj = np.array(
        [
            [500.0, 500.0],
            [1500.0, 500.0],
            [500.0, 1500.0],
            [1500.0, 1500.0],
            [-600.0, 1000.0],
            [1000.0, -600.0],
        ]
    )
    f = distance_allocation(
        xy_inj, xy_prod, length_scale=600.0, per_injector_sum=0.92, cutoff=1600.0
    )
    tau = rng.uniform(100.0, 220.0, size=9)
    alpha = rng.uniform(0.002, 0.005, size=9)
    beta = rng.uniform(1.15, 1.4, size=9)
    # aquifer allocation: east column (x=2000) gets most of the influx
    f_aq = np.array([0.03, 0.07, 0.20, 0.04, 0.10, 0.25, 0.03, 0.08, 0.20])
    f_aq = f_aq / f_aq.sum()
    n_steps = 160
    dates, dt = month_grid(n_steps)
    inj = injection_signal(rng, n_steps, len(inj_ids), base=1400.0)
    # Fetkovich aquifer tank (Atlas CRM–Aquifer tab):
    #   We' = J_aq (p_aq - p_r); dp_aq/dt = -We'/(ct V_aq)
    p_aq = p_aq0
    p_r = p_r0
    we = np.zeros(n_steps)
    p_r_hist = np.zeros(n_steps)
    p_aq_hist = np.zeros(n_steps)
    q_liq = np.zeros((n_steps, 9))
    support_inj = inj @ f
    support = np.zeros_like(q_liq)
    for n in range(n_steps):
        we[n] = j_aq * (p_aq - p_r)
        support[n] = support_inj[n] + f_aq * we[n]
        if n == 0:
            q_liq[0] = 0.85 * support[0]
        else:
            e = np.exp(-dt[n] / tau)
            q_liq[n] = q_liq[n - 1] * e + (1.0 - e) * support[n]
        p_r_hist[n], p_aq_hist[n] = p_r, p_aq
        p_aq -= we[n] * dt[n] / ct_v_aq
        p_r += (we[n] + inj[n].sum() - q_liq[n].sum()) * dt[n] / ct_v_r
    cwi0 = np.full(9, 60.0)
    q_oil, q_water, cwi = split_oil_water(q_liq, support, dt, alpha, beta, cwi0)
    sim = {
        "dates": dates,
        "dt": dt,
        "inj": inj,
        "support": support,
        "q0": q_liq[0],
        "q_liq": q_liq,
        "q_oil": q_oil,
        "q_water": q_water,
        "cwi": cwi,
        "cwi0": cwi0,
    }
    truth = _common_truth(
        "aquifer_6x9",
        "6 injectors × 9 producers with a finite Fetkovich aquifer on the east edge (partial "
        "water drive)",
        seed,
        sim,
        inj_ids,
        prod_ids,
        f,
        tau,
        alpha,
        beta,
        expected_variant="CRMPA",
        notes=[
            "A plain CRMP fit sees per-producer sum_f well above 1.15 → aquifer gate fires → "
            "CRMPA expected.",
            "Static pressure surveys (quarterly) are provided to validate the aquifer/reservoir "
            "pore volumes.",
        ],
    )
    truth["aquifer"] = {
        "model": "Fetkovich tank: We'=J_aq(p_aq-p_r); dp_aq/dt=-We'/(ct*V_aq); "
        "ct*V_r*dp_r/dt=We'+i-q",
        "J_aq_bbl_d_psi": j_aq,
        "ct_V_aq_bbl_psi": ct_v_aq,
        "ct_V_r_bbl_psi": ct_v_r,
        "p_aq0_psi": p_aq0,
        "p_r0_psi": p_r0,
        "f_aq": {p: float(v) for p, v in zip(prod_ids, f_aq, strict=True)},
        "influx_bbl_d_first": float(we[0]),
        "influx_bbl_d_last": float(we[-1]),
        "apparent_sum_f_signature": float(q_liq.mean() * 9 / inj.mean() / 6 * 1.0),
    }
    truth["apparent_support_ratio"] = float(q_liq.sum() / inj.sum())
    truth["optimizer"] = true_optimal_reallocation(
        f, tau, q_liq[-1], cwi[-1], alpha, beta, inj[-1], extra_support=f_aq * we[-1]
    )
    rates = _rates_frame(dates, dt, inj_ids, prod_ids, inj, q_oil, q_water)
    ids = inj_ids + prod_ids
    coords = _coords_frame(ids, np.vstack([xy_inj, xy_prod]))
    category = _category_frame(ids, ["B1"] * len(ids), "AQUIFER")
    events = _events_frame([])
    survey_idx = list(range(0, n_steps, 3))
    pressure = pl.DataFrame(
        {
            "well_id": ["FIELD"] * len(survey_idx),
            "date": [dates[k] for k in survey_idx],
            "p_static_psi": [float(p_r_hist[k]) for k in survey_idx],
            "source": ["static_survey"] * len(survey_idx),
        },
        schema={
            "well_id": pl.Utf8,
            "date": pl.Date,
            "p_static_psi": pl.Float64,
            "source": pl.Utf8,
        },
    )
    return SyntheticCase("aquifer_6x9", rates, coords, category, events, truth, pressure=pressure)


def build_converted_wells() -> SyntheticCase:
    """4 injectors + 6 producers; P-5 and P-6 convert to injectors at month 48 (§5, §11)."""
    seed = 4801
    rng = np.random.default_rng(seed)
    inj_ids = [f"I-{k}" for k in range(1, 5)]
    prod_ids = [f"P-{k}" for k in range(1, 7)]
    xy_inj = np.array([[0.0, 0.0], [1200.0, 0.0], [0.0, 1200.0], [1200.0, 1200.0]])
    xy_prod = np.array(
        [
            [600.0, 300.0],
            [600.0, 900.0],
            [300.0, 600.0],
            [900.0, 600.0],
            [1800.0, 300.0],
            [1800.0, 900.0],
        ]
    )
    n_steps, t_conv = 96, 48
    dates, dt = month_grid(n_steps)
    # window 1: 4 injectors → 6 producers
    f1 = distance_allocation(
        xy_inj, xy_prod, length_scale=550.0, per_injector_sum=0.9, cutoff=1500.0
    )
    tau1 = np.array([120.0, 130.0, 100.0, 140.0, 160.0, 170.0])
    alpha = np.array([0.003, 0.0032, 0.0028, 0.0035, 0.004, 0.0038])
    beta = np.array([1.25, 1.2, 1.3, 1.25, 1.2, 1.3])
    inj1 = injection_signal(rng, n_steps, 4, base=1300.0)
    # window 2: 6 injectors (I-1..4 + P-5, P-6 as injectors) → 4 producers
    xy_inj2 = np.vstack([xy_inj, xy_prod[4:]])
    f2 = distance_allocation(
        xy_inj2, xy_prod[:4], length_scale=550.0, per_injector_sum=0.9, cutoff=1500.0
    )
    f2[:4] = (
        f1[:, :4] / f1[:, :4].sum(axis=1, keepdims=True) * 0.9
    )  # same neighbours, re-normalised
    tau2 = tau1[:4]
    inj_conv = injection_signal(rng, n_steps, 2, base=1100.0)
    inj_conv[:t_conv] = 0.0
    # conversion month: producer for 10 days, injector for the last 15 days → simultaneous P+I step
    inj_conv[t_conv] *= 15.0 / dt[t_conv]
    support = np.zeros((n_steps, 6))
    support[:t_conv] = inj1[:t_conv] @ f1
    q_liq = np.zeros((n_steps, 6))
    q_liq[0] = 0.8 * support[0]
    for n in range(1, n_steps):
        if n < t_conv:
            e = np.exp(-dt[n] / tau1)
            q_liq[n] = q_liq[n - 1] * e + (1.0 - e) * support[n]
        else:
            inj_all = np.concatenate([inj1[n], inj_conv[n]])
            support[n, :4] = inj_all @ f2
            e = np.exp(-dt[n] / tau2)
            q_liq[n, :4] = q_liq[n - 1, :4] * e + (1.0 - e) * support[n, :4]
            if n == t_conv:
                # partial production month for the converted wells (10 producing days)
                q_liq[n, 4:] = q_liq[n - 1, 4:] * 10.0 / dt[n]
    cwi0 = np.full(6, 40.0)
    q_oil, q_water, cwi = split_oil_water(q_liq, support, dt, alpha, beta, cwi0)
    inj_full = np.hstack([inj1, inj_conv])
    all_inj_ids = [*inj_ids, "P-5", "P-6"]
    days_on_prod = np.tile(dt[:, None], (1, 6))
    days_on_prod[t_conv, 4:] = 10.0
    days_on_prod[t_conv + 1 :, 4:] = 0.0
    # rates frame: P-5/P-6 carry both production (before) and injection (after) under one well_id
    rates = _rates_frame(
        dates, dt, inj_ids, prod_ids, inj1, q_oil, q_water, days_on_prod=days_on_prod
    )
    conv_rows = pl.DataFrame(
        {
            "well_id": ["P-5"] * n_steps + ["P-6"] * n_steps,
            "date": dates + dates,
            "q_inj": inj_conv[:, 0].tolist() + inj_conv[:, 1].tolist(),
        },
        schema={"well_id": pl.Utf8, "date": pl.Date, "q_inj": pl.Float64},
    )
    rates = (
        rates.join(conv_rows, on=["well_id", "date"], how="left", suffix="_conv")
        .with_columns(
            pl.when(pl.col("q_inj_conv").is_not_null())
            .then(pl.col("q_inj_conv"))
            .otherwise(pl.col("q_inj"))
            .alias("q_inj"),
            pl.when((pl.col("well_id").is_in(["P-5", "P-6"])) & (pl.col("date") > dates[t_conv]))
            .then(pl.lit(1.0) * pl.col("date").dt.month_end().dt.day().cast(pl.Float64))
            .when((pl.col("well_id").is_in(["P-5", "P-6"])) & (pl.col("date") == dates[t_conv]))
            .then(pl.lit(25.0))
            .otherwise(pl.col("days_on"))
            .alias("days_on"),
        )
        .drop("q_inj_conv")
        .sort(["well_id", "date"])
    )
    sim = {
        "dates": dates,
        "dt": dt,
        "inj": inj1,
        "support": support,
        "q0": q_liq[0],
        "q_liq": q_liq,
        "q_oil": q_oil,
        "q_water": q_water,
        "cwi": cwi,
        "cwi0": cwi0,
    }
    truth = _common_truth(
        "converted_wells",
        "4 injectors × 6 producers; P-5 and P-6 converted to injection at month 48 (window break)",
        seed,
        sim,
        inj_ids,
        prod_ids,
        f1,
        tau1,
        alpha,
        beta,
        expected_variant="CRMP",
        notes=[
            "Well type must be derived as MIXED for P-5/P-6 with conversion date 2014-01-01 (§5).",
            "Conversion month has production and injection in the same step (days_on 10 + 15).",
            "Two windows: before (4×6) and after (6×4) conversion; parameters per window below.",
        ],
    )
    truth["windows"] = [
        {
            "name": "before_conversion",
            "steps": [0, t_conv - 1],
            "injectors": inj_ids,
            "producers": prod_ids,
            "f_ij": _f_dict(inj_ids, prod_ids, f1),
            "tau_days": {p: float(t) for p, t in zip(prod_ids, tau1, strict=True)},
        },
        {
            "name": "after_conversion",
            "steps": [t_conv, n_steps - 1],
            "injectors": all_inj_ids,
            "producers": prod_ids[:4],
            "f_ij": _f_dict(all_inj_ids, prod_ids[:4], f2),
            "tau_days": {p: float(t) for p, t in zip(prod_ids[:4], tau2, strict=True)},
        },
    ]
    truth["conversions"] = [
        {
            "well_id": "P-5",
            "date": dates[t_conv].isoformat(),
            "from": "PROD",
            "to": "INJ",
        },
        {
            "well_id": "P-6",
            "date": dates[t_conv].isoformat(),
            "from": "PROD",
            "to": "INJ",
        },
    ]
    truth["expected_well_types"] = {
        **{i: "INJ" for i in inj_ids},
        **{p: "PROD" for p in prod_ids[:4]},
        "P-5": "MIXED",
        "P-6": "MIXED",
    }
    truth["optimizer"] = true_optimal_reallocation(
        f2, tau2, q_liq[-1, :4], cwi[-1, :4], alpha[:4], beta[:4], inj_full[-1]
    )
    ids = inj_ids + prod_ids
    coords = _coords_frame(ids, np.vstack([xy_inj, xy_prod]))
    category = _category_frame(ids, ["B1"] * len(ids), "CONVERT")
    events = _events_frame(
        [
            {
                "well_id": "P-5",
                "date": dates[t_conv],
                "event": "conversion",
                "note": "PROD→INJ",
            },
            {
                "well_id": "P-6",
                "date": dates[t_conv],
                "event": "conversion",
                "note": "PROD→INJ",
            },
        ]
    )
    return SyntheticCase("converted_wells", rates, coords, category, events, truth)


def build_allocated_noisy() -> SyntheticCase:
    """5×4 field reported as monthly-allocated volumes with days-on and 8 % allocation noise."""
    seed = 8801
    rng = np.random.default_rng(seed)
    inj_ids = [f"I-{k}" for k in range(1, 6)]
    prod_ids = [f"P-{k}" for k in range(1, 5)]
    xy_inj = np.array([[0.0, 0.0], [600.0, 0.0], [1200.0, 0.0], [1800.0, 0.0], [2400.0, 0.0]])
    xy_prod = np.array([[300.0, 700.0], [900.0, 700.0], [1500.0, 700.0], [2100.0, 700.0]])
    f = distance_allocation(
        xy_inj, xy_prod, length_scale=500.0, per_injector_sum=0.88, cutoff=1400.0
    )
    tau = np.array([130.0, 160.0, 120.0, 190.0])
    alpha = np.array([0.003, 0.0034, 0.0029, 0.0031])
    beta = np.array([1.25, 1.2, 1.3, 1.25])
    n_steps = 120
    sim = _simulate_crmp_case(rng, n_steps, inj_ids, prod_ids, f, tau, alpha, beta, inj_base=1600.0)
    dt = sim["dt"]
    # days-on: each producer has 1–2 full shut-in months and a few partial months
    days_on = np.tile(dt[:, None], (1, 4))
    shutins: dict[str, list[str]] = {}
    for j, pid in enumerate(prod_ids):
        months = rng.choice(np.arange(6, n_steps - 6), size=int(rng.integers(1, 3)), replace=False)
        for m in months:
            days_on[m, j] = 0.0
        partial = rng.choice(np.arange(3, n_steps - 3), size=3, replace=False)
        for m in partial:
            if days_on[m, j] > 0:
                days_on[m, j] = float(rng.integers(8, 22))
        shutins[pid] = [sim["dates"][m].isoformat() for m in sorted(months)]
    frac = days_on / dt[:, None]

    # allocation noise: 8 % log-normal per well, renormalised so the field monthly total is
    # preserved
    def allocate(q_true: FArray, sigma: float, r: np.random.Generator) -> FArray:
        vol = q_true * frac
        noisy = vol * np.exp(sigma * r.standard_normal(vol.shape) - 0.5 * sigma**2)
        tot_true, tot_noisy = (
            vol.sum(axis=1, keepdims=True),
            noisy.sum(axis=1, keepdims=True),
        )
        tot_noisy[tot_noisy == 0] = 1.0
        return np.asarray(noisy * tot_true / tot_noisy, dtype=np.float64)

    arng = np.random.default_rng(seed + 7)
    q_oil = allocate(sim["q_oil"], 0.08, arng)
    q_water = allocate(sim["q_water"], 0.08, arng)
    inj = sim["inj"] * np.exp(0.02 * arng.standard_normal(sim["inj"].shape) - 0.5 * 0.02**2)
    truth = _common_truth(
        "allocated_noisy",
        "5×4 field, monthly-allocated volumes with days-on (shut-ins, partial months) and 8 % "
        "allocation noise",
        seed,
        sim,
        inj_ids,
        prod_ids,
        f,
        tau,
        alpha,
        beta,
        expected_variant="CRMP",
        notes=[
            "Monthly-allocated (not metered): §8 says stay monthly and prefer CRMT/CRMP; CRMIP "
            "should be excluded or lose.",
            "Rates are calendar-day (volume ÷ days in month); the loader must normalise by "
            "days_on (§6).",
            "Shut-in months exercise days_on=0 handling; they are reporting shut-ins layered on "
            "the clean model.",
        ],
    )
    truth["noise"] = {
        "production_pct": 0.0,
        "injection_pct": 2.0,
        "allocation_pct": 8.0,
    }
    truth["reporting"] = {"basis": "monthly_allocated", "shut_in_months": shutins}
    truth["optimizer"] = true_optimal_reallocation(
        f, tau, sim["q_liq"][-1], sim["cwi"][-1], alpha, beta, sim["inj"][-1]
    )
    rates = _rates_frame(
        sim["dates"], dt, inj_ids, prod_ids, inj, q_oil, q_water, days_on_prod=days_on
    )
    ids = inj_ids + prod_ids
    coords = _coords_frame(ids, np.vstack([xy_inj, xy_prod]))
    category = _category_frame(ids, ["B1"] * len(ids), "ALLOC")
    events = _events_frame(
        [
            {
                "well_id": pid,
                "date": date.fromisoformat(d),
                "event": "shut_in",
                "note": "full month",
            }
            for pid, ds in shutins.items()
            for d in ds
        ]
    )
    return SyntheticCase("allocated_noisy", rates, coords, category, events, truth)


def build_sectored_60() -> SyntheticCase:
    """60 wells in two sealed sectors (fault at x = 3000 m); 12 injectors + 18 producers each."""
    seed = 6001
    rng = np.random.default_rng(seed)
    inj_ids: list[str] = []
    prod_ids: list[str] = []
    xy_inj_list: list[list[float]] = []
    xy_prod_list: list[list[float]] = []
    blocks_inj: list[str] = []
    blocks_prod: list[str] = []
    for s, x0 in (("A", 0.0), ("B", 3600.0)):
        # producers on a 6×3 grid, injectors staggered between rows
        for r in range(3):
            for c in range(6):
                prod_ids.append(f"{s}P-{r * 6 + c + 1:02d}")
                xy_prod_list.append([x0 + c * 450.0, r * 900.0])
                blocks_prod.append(s)
        for r in range(2):
            for c in range(6):
                inj_ids.append(f"{s}I-{r * 6 + c + 1:02d}")
                xy_inj_list.append([x0 + c * 450.0 + 225.0, r * 900.0 + 450.0])
                blocks_inj.append(s)
    xy_inj, xy_prod = np.array(xy_inj_list), np.array(xy_prod_list)
    f = distance_allocation(xy_inj, xy_prod, length_scale=400.0, per_injector_sum=0.9, cutoff=900.0)
    # enforce sealed sectors: no cross-fault allocation
    for i, bi in enumerate(blocks_inj):
        for j, bj in enumerate(blocks_prod):
            if bi != bj:
                f[i, j] = 0.0
    row = f.sum(axis=1, keepdims=True)
    f = f / row * 0.9
    tau = rng.uniform(95.0, 250.0, size=36)
    alpha = rng.uniform(0.0025, 0.0045, size=36)
    beta = rng.uniform(1.15, 1.35, size=36)
    n_steps = 120
    sim = _simulate_crmp_case(rng, n_steps, inj_ids, prod_ids, f, tau, alpha, beta, inj_base=900.0)
    truth = _common_truth(
        "sectored_60",
        "60 wells: two sealed sectors (A west, B east) separated by a fault at x=3000 m; 24 "
        "injectors, 36 producers",
        seed,
        sim,
        inj_ids,
        prod_ids,
        f,
        tau,
        alpha,
        beta,
        expected_variant="CRMP",
        notes=[
            "Sectorization (§7) must split A and B; any cross-fault f_ij > 0.02 is a failure.",
            "Runtime case: full tournament per sector should run in parallel (joblib).",
            "O_d is 4.8 for the whole field (fails the gate) but 9.2 per sector (passes) — "
            "sectorization is required.",
        ],
        n_inj_per_sector=12,
    )
    truth["sectors"] = {
        "A": [w for w in inj_ids + prod_ids if w.startswith("A")],
        "B": [w for w in inj_ids + prod_ids if w.startswith("B")],
    }
    truth["fault"] = {
        "type": "sealing",
        "polyline": [[3000.0, -500.0], [3000.0, 2500.0]],
    }
    truth["optimizer"] = true_optimal_reallocation(
        f, tau, sim["q_liq"][-1], sim["cwi"][-1], alpha, beta, sim["inj"][-1]
    )
    rates = _rates_frame(
        sim["dates"],
        sim["dt"],
        inj_ids,
        prod_ids,
        sim["inj"],
        sim["q_oil"],
        sim["q_water"],
    )
    ids = inj_ids + prod_ids
    coords = _coords_frame(ids, np.vstack([xy_inj, xy_prod]))
    category = _category_frame(ids, blocks_inj + blocks_prod, "SECTORED")
    faults = pl.DataFrame(
        {
            "fault_id": ["F1", "F1"],
            "x": [3000.0, 3000.0],
            "y": [-500.0, 2500.0],
            "order": [0, 1],
        },
        schema={
            "fault_id": pl.Utf8,
            "x": pl.Float64,
            "y": pl.Float64,
            "order": pl.Int64,
        },
    )
    events = _events_frame([])
    return SyntheticCase(
        "sectored_60",
        rates,
        coords,
        category,
        events,
        truth,
        extra_frames={"faults": faults},
    )


BUILDERS = {
    "streak_5x4": lambda: build_streak_5x4(0.0),
    "streak_5x4_noise5": lambda: build_streak_5x4(5.0),
    "aquifer_6x9": build_aquifer_6x9,
    "converted_wells": build_converted_wells,
    "allocated_noisy": build_allocated_noisy,
    "sectored_60": build_sectored_60,
}


# --------------------------------------------------------------------------------------
# Writing / freezing
# --------------------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_case(case: SyntheticCase, root: Path) -> dict[str, str]:
    out = root / case.name
    out.mkdir(parents=True, exist_ok=True)
    frames: dict[str, pl.DataFrame] = {
        "rates": case.rates,
        "coords": case.coords,
        "category": case.category,
        "events": case.events,
        **case.extra_frames,
    }
    if case.pressure is not None:
        frames["pressure"] = case.pressure
    hashes: dict[str, str] = {}
    for key, df in frames.items():
        path = out / f"{key}.parquet"
        df.write_parquet(path, compression="zstd", statistics=False)
        hashes[f"{case.name}/{key}.parquet"] = sha256_file(path)
    truth = dict(case.truth)
    truth["files"] = {k: v for k, v in hashes.items()}
    (out / "truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return hashes


def generate_all(root: Path = SUITE_DIR) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for name, builder in BUILDERS.items():
        case = builder()
        manifest.update(write_case(case, root))
        print(f"wrote {name}: {case.rates.height} rate rows")
    (root / "manifest.json").write_text(
        json.dumps({"generator_version": GENERATOR_VERSION, "files": manifest}, indent=2),
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    generate_all()
