"""Load test — prompt Milestone 5: "300-well project runs a full tournament in under 10 minutes on 8 cores".

Builds a 300-well synthetic field in memory (10 sealed sectors of 12 injectors + 18 producers, 120
monthly steps, the same generator physics as the frozen ``sectored_60`` fixture) and times the
full engine path: load → clean → gates → tournament per sector → optimizer per sector → bundle.

    python scripts/load_test.py [--cores 8] [--wells 300] [--steps 120] [--json out.json]

The process is pinned to ``--cores`` through joblib (``solver.n_jobs``) and the loky / BLAS
thread caps, so the figure is what an 8-core deployment would see; the fixture itself is not
written to the validation suite (it would be 30 MB of Parquet for a timing case).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_case(n_sectors: int, n_steps: int, seed: int = 30001) -> tuple[Any, ...]:
    import numpy as np

    from waterflood_app.validation.synthetic_suite import generate as G

    rng = np.random.default_rng(seed)
    inj_ids: list[str] = []
    prod_ids: list[str] = []
    xy_inj: list[list[float]] = []
    xy_prod: list[list[float]] = []
    blocks_inj: list[str] = []
    blocks_prod: list[str] = []
    for s in range(n_sectors):
        tag = chr(ord("A") + s) if s < 26 else f"S{s}"
        x0, y0 = (s % 5) * 3600.0, (s // 5) * 3600.0
        for r in range(3):
            for c in range(6):
                prod_ids.append(f"{tag}P-{r * 6 + c + 1:02d}")
                xy_prod.append([x0 + c * 450.0, y0 + r * 900.0])
                blocks_prod.append(tag)
        for r in range(2):
            for c in range(6):
                inj_ids.append(f"{tag}I-{r * 6 + c + 1:02d}")
                xy_inj.append([x0 + c * 450.0 + 225.0, y0 + r * 900.0 + 450.0])
                blocks_inj.append(tag)
    xi, xp = np.array(xy_inj), np.array(xy_prod)
    f = G.distance_allocation(xi, xp, length_scale=400.0, per_injector_sum=0.9, cutoff=900.0)
    for i, bi in enumerate(blocks_inj):
        for j, bj in enumerate(blocks_prod):
            if bi != bj:
                f[i, j] = 0.0
    row = f.sum(axis=1, keepdims=True)
    f = np.where(row > 0, f / np.where(row > 0, row, 1.0) * 0.9, 0.0)
    n_p = len(prod_ids)
    tau = rng.uniform(95.0, 250.0, size=n_p)
    alpha = rng.uniform(0.0025, 0.0045, size=n_p)
    beta = rng.uniform(1.15, 1.35, size=n_p)
    sim = G._simulate_crmp_case(rng, n_steps, inj_ids, prod_ids, f, tau, alpha, beta, inj_base=900.0)
    rates = G._rates_frame(sim["dates"], sim["dt"], inj_ids, prod_ids, sim["inj"], sim["q_oil"], sim["q_water"])
    ids = inj_ids + prod_ids
    coords = G._coords_frame(ids, np.vstack([xi, xp]))
    category = G._category_frame(ids, blocks_inj + blocks_prod, "LOADTEST")
    events = G._events_frame([])
    return rates, coords, category, events, f, tau


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cores", type=int, default=8)
    ap.add_argument("--wells", type=int, default=300, help="rounded down to a multiple of 30 (one sector)")
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--variants", nargs="*", default=None, help="restrict the tournament (default: full)")
    a = ap.parse_args()
    for var in ("LOKY_MAX_CPU_COUNT", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = str(a.cores if var == "LOKY_MAX_CPU_COUNT" else 1)
    import polars as pl

    from waterflood_app.api.bundle import build_bundle
    from waterflood_app.config import load_config
    from waterflood_app.engine import run_engine
    from waterflood_app.ingest.connectors import LoadedData
    from waterflood_app.ingest.units import PVT
    from waterflood_app.optimize.run import optimize_sectors

    n_sectors = max(1, a.wells // 30)
    t0 = time.perf_counter()
    rates, coords, category, _events, f_true, _tau_true = build_case(n_sectors, a.steps)
    t_build = time.perf_counter() - t0
    loaded = LoadedData(
        rates=rates.rename({"well_id": "well"}).with_columns(pl.lit(None, dtype=pl.Float64).alias("q_gas")),
        coords=coords.rename({"well_id": "well"}),
        category=category.rename({"well_id": "well"}),
        events=None,
        pressure=None,
    )
    cfg = load_config().with_overrides({"solver": {"n_jobs": a.cores}})
    n_wells = n_sectors * 30
    print(
        f"[load test] {n_wells} wells in {n_sectors} sectors × {a.steps} months, {a.cores} cores; "
        f"fixture built in {t_build:.1f}s",
        flush=True,
    )
    t1 = time.perf_counter()
    run = run_engine(loaded, cfg, PVT(), seed=0, variants=a.variants)
    t_engine = time.perf_counter() - t1
    latest = run.latest()
    print(
        f"[load test] engine: {t_engine:.1f}s — {len(latest)} sectors fitted, confidence {run.confidence}", flush=True
    )
    t2 = time.perf_counter()
    recs, errors = optimize_sectors(latest, cfg, "oil", "balanced", seed=run.seed)
    for sid, err in errors.items():
        print(f"  optimizer failed for sector {sid}: {err}")
    t_opt = time.perf_counter() - t2
    t3 = time.perf_counter()
    bundle = build_bundle(run, recs, cfg, "loadtest", {"unit_system": "field"})
    t_bundle = time.perf_counter() - t3
    total = time.perf_counter() - t0
    # accuracy sanity on the fitted allocation
    import numpy as np

    err = []
    for s in latest:
        w = s.tournament.winner
        if w is None:
            continue
        err.append(float(np.mean(np.abs(w.fit.params.f - true_block(f_true, s.grid.injectors, s.grid.producers)))))
    result = {
        "wells": n_wells,
        "sectors": n_sectors,
        "steps": a.steps,
        "cores": a.cores,
        "variants": a.variants or "full tournament",
        "build_s": round(t_build, 1),
        "engine_s": round(t_engine, 1),
        "optimizer_s": round(t_opt, 1),
        "bundle_s": round(t_bundle, 1),
        "total_s": round(total, 1),
        "sectors_fitted": len(latest),
        "winners": sorted({s.tournament.winner.variant for s in latest if s.tournament.winner}),
        "confidence": run.confidence,
        "mean_abs_f_error": round(float(np.mean(err)), 4) if err else None,
        "bundle_bytes": len(json.dumps(bundle, default=str)),
        "pass_10_min": total < 600.0,
    }
    print(json.dumps(result, indent=1))
    if a.json:
        a.json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0 if result["pass_10_min"] else 1


_INJ_INDEX: dict[str, int] = {}
_PROD_INDEX: dict[str, int] = {}


def true_block(f_true: Any, injectors: list[str], producers: list[str]) -> Any:
    """Sub-matrix of the true allocation for a sector, using the global id ordering of the case."""
    import numpy as np

    global _INJ_INDEX, _PROD_INDEX
    if not _INJ_INDEX:
        n_sectors = f_true.shape[0] // 12
        inj_ids, prod_ids = [], []
        for s in range(n_sectors):
            tag = chr(ord("A") + s) if s < 26 else f"S{s}"
            prod_ids += [f"{tag}P-{k + 1:02d}" for k in range(18)]
            inj_ids += [f"{tag}I-{k + 1:02d}" for k in range(12)]
        _INJ_INDEX = {w: k for k, w in enumerate(inj_ids)}
        _PROD_INDEX = {w: k for k, w in enumerate(prod_ids)}
    ii = [_INJ_INDEX[w.split("@")[0]] for w in injectors]
    jj = [_PROD_INDEX[w.split("@")[0]] for w in producers]
    return np.asarray(f_true)[np.ix_(ii, jj)]


if __name__ == "__main__":
    sys.exit(main())
