"""Independent sanity check for the synthetic fixtures (two-stage tau-grid + NNLS CRMP, distance-masked).
Run: python verify_fixtures.py   — prints per-case recovery metrics against truth.json."""

import csv
import json
import math
import os

import numpy as np
from scipy.optimize import nnls

HERE = os.path.dirname(os.path.abspath(__file__))


def load(case):
    d = os.path.join(HERE, case)
    rows = list(csv.DictReader(open(f"{d}/rates.csv")))
    dates = sorted({r["date"] for r in rows})
    di = {x: i for i, x in enumerate(dates)}
    inj = {}
    prod = {}
    don = {}
    for r in rows:
        w = r["well_id"]
        t = di[r["date"]]
        dd = float(r["days_on"] or 0)
        if r["type"] == "INJ":
            inj.setdefault(w, np.zeros(len(dates)))[t] = float(r["q_inj"] or 0) * 1.02
        else:
            prod.setdefault(w, np.zeros(len(dates)))[t] = (
                float(r["q_oil"] or 0) * 1.25 + float(r["q_water"] or 0) * 1.02
            )
            don.setdefault(w, np.zeros(len(dates)))[t] = dd
    xy = {
        r["well_id"]: (float(r["x"]), float(r["y"]))
        for r in csv.DictReader(open(f"{d}/coords.csv"))
    }
    return dates, inj, prod, don, xy, json.load(open(f"{d}/truth.json"))


def filt(I, tau):
    e = np.exp(-1 / tau)
    X = np.zeros_like(I)
    for t in range(1, len(I)):
        X[t] = X[t - 1] * e + I[t] * (1 - e)
    return X


def fit(I, q, ok, ntrain, mask):
    best = None
    Im = I[:, mask]
    for tau in np.arange(3, 120, 1.0):
        e = np.exp(-1 / tau)
        X = filt(Im, tau)
        y = q - q[0] * e ** np.arange(len(q))
        tr = ok[:ntrain]
        f, res = nnls(X[:ntrain][tr], y[:ntrain][tr])
        if best is None or res < best[0]:
            best = (res, tau, f)
    res, tau, f = best
    e = np.exp(-1 / tau)
    m = filt(Im, tau) @ f + q[0] * e ** np.arange(len(q))
    bl = np.arange(len(q)) >= ntrain
    bl &= ok
    mape = np.mean(np.abs(m[bl] - q[bl]) / q[bl]) * 100
    full = np.zeros(I.shape[1])
    full[mask] = f
    return tau, full, mape


for case, dist in [
    ("streak_5x4_clean", 1e9),
    ("streak_5x4_noisy", 1e9),
    ("allocated_noisy", 1e9),
    ("aquifer_9x9", 4500),
    ("converted_wells", 1e9),
    ("sectored_60", 3500),
]:
    dates, inj, prod, don, xy, truth = load(case)
    inj_n = sorted(inj)
    I = np.column_stack([inj[w] for w in inj_n])
    ntrain = int(0.75 * len(dates))
    print(f"\n== {case}: {len(inj_n)} injectors, {len(prod)} producers, {len(dates)} months")
    mapes = []
    sums = {}
    for pw in sorted(prod):
        ok = don[pw] > 0
        q = prod[pw].copy()
        if case == "converted_wells" and (~ok).sum() > 0:
            continue  # skip converted wells here; type derivation is tested separately
        mask = np.array([math.dist(xy[w], xy[pw]) < dist for w in inj_n])
        tau, f, mape = fit(I, q, ok, ntrain, mask)
        mapes.append(mape)
        sums[pw] = f.sum()
        tt = truth.get("tau_days", {}).get(pw) or truth.get("phase_A", {}).get("tau_days", {}).get(
            pw
        )
        if len(prod) <= 9:
            print(f"  {pw}: tau {tau:5.1f} (true {tt})  blind MAPE {mape:4.1f}%  Σf {f.sum():.2f}")
    print(f"  median blind MAPE {np.median(mapes):.1f}%   acceptance: {truth.get('acceptance')}")
    if case == "aquifer_9x9":
        print(
            "  flank Σf:",
            [round(sums[p], 2) for p in ["P-1", "P-2", "P-3"]],
            "interior median:",
            round(np.median([sums[p] for p in sums if p not in ("P-1", "P-2", "P-3")]), 2),
        )
