"""Surveillance loop — the §15 rules table, implemented literally.

| Trigger                                            | Action                                                   |
|----------------------------------------------------|----------------------------------------------------------|
| New month of data                                  | re-run L1–L2 with saved config; diff the DQ report;       |
|                                                    | forecast error for the past month per producer           |
| Forecast error within P10–P90                      | no re-fit; parameters kept; plan continues               |
| Forecast error outside band, DQ unchanged          | reservoir-change hypothesis → rolling re-fit (§10) →     |
|                                                    | if parameters shift, re-optimize and open a new DRAFT;   |
|                                                    | check the running plan's "revert if" conditions          |
| Forecast error outside band, DQ changed            | data-problem hypothesis → message names the well/meter;  |
|                                                    | no re-fit until resolved                                 |
| New event (workover, conversion, new well)         | window break; affected sector re-fit; prior-based        |
|                                                    | parameters for new wells (§11)                           |
| Quarterly                                          | full tournament re-run; confidence recalibrated (§16)    |
| Implemented plan reaches 3 / 6 months              | evaluation record created (§3)                           |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.workflow.evaluation import due_evaluations


@dataclass
class MonthlyInputs:
    today: date
    producers: list[str]
    forecast_p10: np.ndarray  # (Np,) last-month forecast band per producer
    forecast_p90: np.ndarray
    actual: np.ndarray  # (Np,) realised last-month rate
    dq_changed: dict[str, bool]  # per producer: data-quality report changed since last run
    new_events: list[dict[str, Any]] = field(default_factory=list)  # {well, date, type}
    implemented_at: date | None = None
    evaluations_done: set[int] = field(default_factory=set)
    last_full_tournament: date | None = None
    parameters_shifted: bool = False  # result of the rolling re-fit, when it ran


@dataclass
class Action:
    trigger: str
    action: str
    wells: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


def rules(inputs: MonthlyInputs, cfg: Config, log: ConditionLog | None = None) -> list[Action]:
    """Apply the §15 table to one month of new data; returns the ordered action list."""
    log = log if log is not None else ConditionLog()
    out: list[Action] = [
        Action(
            "new month of data",
            "re-run L1–L2 with saved config; diff data-quality report; compute forecast error per producer",
        )
    ]
    outside = (inputs.actual < inputs.forecast_p10) | (inputs.actual > inputs.forecast_p90)
    within = [w for w, o in zip(inputs.producers, outside, strict=True) if not o]
    dq_bad = [w for w, o in zip(inputs.producers, outside, strict=True) if o and inputs.dq_changed.get(w, False)]
    res_change = [
        w for w, o in zip(inputs.producers, outside, strict=True) if o and not inputs.dq_changed.get(w, False)
    ]
    if within and not (dq_bad or res_change):
        out.append(Action("forecast error within P10–P90", "no re-fit; parameters kept; plan continues", within))
    if dq_bad:
        for w in dq_bad:
            log.emit(ConditionCode.FORECAST_OUT_OF_BAND_DQ_CHANGED, scope=f"well:{w}", well=w)
        out.append(
            Action(
                "forecast error outside band, data-quality changed",
                "data-problem hypothesis: message names the well/meter; no re-fit until resolved",
                dq_bad,
            )
        )
    if res_change:
        detail = "reservoir-change hypothesis: rolling re-fit (§10)"
        if inputs.parameters_shifted:
            detail += (
                "; parameters shifted → re-optimize and open a new DRAFT; check the running plan's revert-if conditions"
            )
        out.append(Action("forecast error outside band, data-quality unchanged", detail, res_change))
    if inputs.new_events:
        wells = sorted({str(e.get("well")) for e in inputs.new_events})
        out.append(
            Action(
                "new event",
                "window break; affected sector re-fit; prior-based parameters for new wells (§11)",
                wells,
                ", ".join(sorted({str(e.get("type")) for e in inputs.new_events})),
            )
        )
    if inputs.last_full_tournament is None or (inputs.today - inputs.last_full_tournament).days >= 90:
        out.append(Action("quarterly", "full tournament re-run; confidence recalibrated from §16 outcomes"))
    if inputs.implemented_at is not None:
        for m in due_evaluations(inputs.implemented_at, inputs.today, cfg, inputs.evaluations_done):
            out.append(
                Action(
                    f"implemented plan reaches {m} months",
                    "evaluation record created (§3); outcome feeds validation",
                    detail=str(m),
                )
            )
    return out
