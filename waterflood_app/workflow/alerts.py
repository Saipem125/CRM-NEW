"""Alerts — architecture §10 (change alerts) and §15 (surveillance messages), in plain language (§17)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from waterflood_app.messaging.conditions import Condition, ConditionLog
from waterflood_app.models.change_detect import Shift
from waterflood_app.workflow.scheduler import Action


@dataclass
class Alert:
    kind: str  # "connectivity", "data_quality", "revert", "evaluation", "dq_report_changed", "condition"
    message: str
    action: str
    wells: list[str] = field(default_factory=list)
    since: date | None = None
    likely_causes: list[str] = field(default_factory=list)
    link: str = ""  # Details plot anchor
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["since"] = None if self.since is None else self.since.isoformat()
        return d


def from_shifts(shifts: list[Shift]) -> list[Alert]:
    out = []
    for s in shifts:
        if s.kind == "f":
            msg = (
                f"Connection between {s.injector} and {s.producer} has {s.direction} since {s.since.strftime('%B %Y')}."
            )
            wells = [str(s.injector), s.producer]
        else:
            speed = "more slowly" if s.direction == "slowed" else "faster"
            msg = f"{s.producer} responds {speed} to injection since {s.since.strftime('%B %Y')}."
            wells = [s.producer]
        out.append(
            Alert(
                "connectivity",
                msg,
                "Review for breakthrough; plan updated automatically.",
                wells,
                s.since,
                list(s.likely_causes),
                f"details:connectivity:{s.injector or ''}:{s.producer}",
                "info",
            )
        )
    return out


def from_conditions(log: ConditionLog) -> list[Alert]:
    return [
        Alert(
            "condition",
            c.message,
            c.action,
            [str(c.context.get("well"))] if c.context.get("well") else [],
            None,
            [],
            f"details:condition:{c.code.value}",
            c.level.value,
        )
        for c in log
    ]


def from_actions(actions: list[Action]) -> list[Alert]:
    out = []
    for a in actions:
        if a.trigger.startswith("forecast error outside band, data-quality changed"):
            out.append(
                Alert(
                    "data_quality",
                    f"Recent data for {', '.join(a.wells)} looks inconsistent with earlier records "
                    "(possible meter/allocation issue).",
                    "Verify with production accounting before acting.",
                    a.wells,
                    severity="warning",
                )
            )
        elif a.trigger.startswith("implemented plan reaches"):
            out.append(
                Alert(
                    "evaluation",
                    f"The implemented plan has reached {a.detail} months: "
                    "its result is being compared with the forecast.",
                    "None needed; the outcome updates the confidence calibration.",
                    severity="info",
                )
            )
        elif a.trigger == "new event":
            out.append(
                Alert(
                    "condition",
                    f"New field event(s) for {', '.join(a.wells)}: the affected sector is being re-fitted.",
                    "None needed; new wells use prior-based parameters until 12 months of data exist.",
                    a.wells,
                    severity="info",
                )
            )
    return out


def revert_alerts(
    items: list[dict[str, Any]], water_cut_now: dict[str, float], water_cut_at_approval: dict[str, float], jump: float
) -> list[Alert]:
    """A change's 'revert if' condition has been met (§14, §15)."""
    out = []
    for it in items:
        connected = it.get("connected_producers", [])
        hit = [p for p in connected if water_cut_now.get(p, 0.0) - water_cut_at_approval.get(p, 0.0) > jump]
        if hit:
            out.append(
                Alert(
                    "revert",
                    f"Water cut at {', '.join(hit)} rose by more than {jump:.0%} after the change at {it['well']}: "
                    "revert condition met.",
                    f"Return {it['well']} to {it['rate_from']:.0f} and review.",
                    [it["well"], *hit],
                    severity="warning",
                )
            )
    return out


def dq_report_changed(before: dict[str, Any], after: dict[str, Any]) -> Alert | None:
    """A change in the data-quality report between refreshes is itself an alert (§7)."""
    diffs = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    if not diffs:
        return None
    return Alert(
        "dq_report_changed",
        f"The data-quality report changed since the last refresh ({', '.join(sorted(diffs)[:5])}).",
        "Open the data-quality report in Details before acting on the recommendation.",
        severity="warning",
    )


def merge(*groups: list[Alert]) -> list[Alert]:
    seen: set[tuple[str, str]] = set()
    out: list[Alert] = []
    for g in groups:
        for a in g:
            key = (a.kind, a.message)
            if key not in seen:
                seen.add(key)
                out.append(a)
    return out


def _unused(_: Condition) -> None:  # keeps the Condition import meaningful for type checkers
    return None
