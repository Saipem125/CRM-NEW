"""Evaluation records — architecture §3 ("Evaluation trigger") and §16 Tier 3.

Automatic at 3 and 6 months after implementation: realised incremental oil vs. the forecast
P10–P90 band → outcome class (within band / better / worse) → the validation record that
re-calibrates confidence (HIGH must land within band ≥ 80 % of the time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from waterflood_app.config import Config


@dataclass
class EvaluationRecord:
    recommendation_id: str
    months_after: int
    evaluated_on: date
    forecast_p10: float
    forecast_p50: float
    forecast_p90: float
    realised: float
    outcome: str  # "within band" | "better" | "worse"
    confidence_badge: str
    implemented_deviation: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["evaluated_on"] = self.evaluated_on.isoformat()
        return d


def outcome_class(realised: float, p10: float, p90: float) -> str:
    if realised > p90:
        return "better"
    if realised < p10:
        return "worse"
    return "within band"


def evaluate(
    recommendation_id: str,
    months_after: int,
    evaluated_on: date,
    forecast_oil_members: np.ndarray,
    realised_oil: float,
    confidence_badge: str,
    cfg: Config,
    implemented_deviation: dict[str, float] | None = None,
) -> EvaluationRecord:
    """forecast_oil_members: cumulative oil over the evaluation period per ensemble member."""
    lo, hi = cfg["workflow.outcome_band"]
    p10, p50, p90 = (float(np.percentile(forecast_oil_members, q)) for q in (lo, 50, hi))
    return EvaluationRecord(
        recommendation_id,
        months_after,
        evaluated_on,
        p10,
        p50,
        p90,
        realised_oil,
        outcome_class(realised_oil, p10, p90),
        confidence_badge,
        dict(implemented_deviation or {}),
    )


def due_evaluations(implemented_at: date, today: date, cfg: Config, done: set[int] | None = None) -> list[int]:
    """Which of the configured evaluation months (3, 6) are due and not yet done."""
    months = [int(m) for m in cfg["workflow.evaluation_months"]]
    out = []
    for m in months:
        y, mo = implemented_at.year, implemented_at.month + m
        while mo > 12:
            mo -= 12
            y += 1
        due = date(y, mo, min(implemented_at.day, 28))
        if today >= due and m not in (done or set()):
            out.append(m)
    return out


def hit_rate_by_badge(records: list[EvaluationRecord]) -> dict[str, dict[str, float]]:
    """Share of evaluations within band per badge — the Tier-3 calibration table (§16)."""
    out: dict[str, dict[str, float]] = {}
    for badge in ("HIGH", "MEDIUM", "LOW"):
        rs = [r for r in records if r.confidence_badge == badge]
        if not rs:
            continue
        within = sum(1 for r in rs if r.outcome == "within band")
        better = sum(1 for r in rs if r.outcome == "better")
        out[badge] = {
            "n": len(rs),
            "within_band": within / len(rs),
            "better": better / len(rs),
            "worse": 1.0 - (within + better) / len(rs),
        }
    return out


def recalibrated_threshold(
    records: list[EvaluationRecord], current_r2_min: float, target_hit_rate: float = 0.8, step: float = 0.02
) -> float:
    """Quarterly re-tuning rule (§16): raise the HIGH blind-R² floor while HIGH lands within band < 80 %."""
    table = hit_rate_by_badge(records)
    high = table.get("HIGH")
    if high is None or high["n"] < 5:
        return current_r2_min
    if high["within_band"] + high["better"] < target_hit_rate:
        return min(current_r2_min + step, 0.99)
    return current_r2_min
