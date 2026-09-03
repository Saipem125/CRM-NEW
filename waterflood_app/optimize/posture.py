"""Risk posture — architecture §13 (uncertainty → decision).

| posture    | objective actually optimised across the calibrated ensemble          |
|------------|-----------------------------------------------------------------------|
| aggressive | P50 / expected value                                                   |
| balanced   | expected value − 0.5 × standard deviation (default)                    |
| robust     | P10 (worst case among realizations); rejects plans that lose oil in   |
|            | any realization                                                        |

A LOW-confidence model forces the robust posture and reduces the allowed step size (§14).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config

FArray = npt.NDArray[np.float64]
POSTURES = ("aggressive", "balanced", "robust")


def effective_posture(requested: str | None, confidence: str, cfg: Config) -> tuple[str, str | None]:
    """Posture actually used and the reason when it differs from the request."""
    p = (requested or str(cfg["optimize.posture_default"])).lower()
    if p not in POSTURES:
        raise ValueError(f"unknown posture {requested!r}")
    if confidence == "LOW" and bool(cfg["optimize.low_confidence_forces_robust"]) and p != "robust":
        return "robust", "LOW confidence forces the robust posture (§13)"
    return p, None


def aggregate(values: FArray, posture: str, cfg: Config) -> float:
    """Collapse per-member objective values to the number the optimizer maximises."""
    v = np.asarray(values, dtype=np.float64)
    spec = cfg.section("optimize")["postures"][posture]
    stat = spec["statistic"]
    if stat == "mean":
        return float(v.mean())
    if stat == "mean_minus_k_std":
        return float(v.mean() - float(spec.get("k", 0.5)) * v.std())
    if stat == "p10":
        return float(np.percentile(v, 10)) if len(v) > 1 else float(v[0])
    raise ValueError(stat)


def rejects_loss(posture: str, cfg: Config) -> bool:
    spec = cfg.section("optimize")["postures"][posture]
    return bool(spec.get("reject_any_loss", False))
