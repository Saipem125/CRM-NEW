"""Conditions — the single channel from engine to user (architecture §17).

Every gate, QC step and model check emits a :class:`Condition` keyed by :class:`ConditionCode`.
The user-facing sentence and suggested action come from ``condition_map.yaml``; code never
carries prose. ``tests/test_condition_map.py`` asserts every code has a map entry.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

MAP_PATH = Path(__file__).resolve().parent / "condition_map.yaml"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "error": 2}[self.value]


class ConditionCode(StrEnum):
    """Technical conditions. The value is the key in ``condition_map.yaml``."""

    # §17 table rows
    OD_LOW = "OD_LOW"
    CRMIP_DATA_LOW = "CRMIP_DATA_LOW"
    INJ_CV_LOW = "INJ_CV_LOW"
    TAU_DT_LOW = "TAU_DT_LOW"
    SUM_F_HIGH = "SUM_F_HIGH"
    NO_BHP_EVENTS_HIGH = "NO_BHP_EVENTS_HIGH"
    SIMULTANEOUS_PI = "SIMULTANEOUS_PI"
    GOR_ABOVE_RS = "GOR_ABOVE_RS"
    MULTISTART_SPREAD_HIGH = "MULTISTART_SPREAD_HIGH"
    FORECAST_OUT_OF_BAND_DQ_CHANGED = "FORECAST_OUT_OF_BAND_DQ_CHANGED"
    CUSUM_SHIFT = "CUSUM_SHIFT"
    CONFIDENCE_LOW = "CONFIDENCE_LOW"
    # L1 loading (§5, §6)
    UNMATCHED_WELL_IDS = "UNMATCHED_WELL_IDS"
    UNMAPPED_REQUIRED_COLUMN = "UNMAPPED_REQUIRED_COLUMN"
    UNPARSEABLE_DATES = "UNPARSEABLE_DATES"
    PVT_CONSTANTS_ASSUMED = "PVT_CONSTANTS_ASSUMED"
    COMMINGLED_EQUAL_SPLIT = "COMMINGLED_EQUAL_SPLIT"
    DATUM_DEPTH_MISSING = "DATUM_DEPTH_MISSING"
    MISSING_COORDINATES = "MISSING_COORDINATES"
    CONVERSION_DETECTED = "CONVERSION_DETECTED"
    # L2 processing (§7, §8)
    PRESSURE_WHP_ONLY = "PRESSURE_WHP_ONLY"
    PRESSURE_STATIC_ONLY = "PRESSURE_STATIC_ONLY"
    NO_PRESSURE = "NO_PRESSURE"
    OUTLIERS_REMOVED = "OUTLIERS_REMOVED"
    NEGATIVE_RATES_CAPPED = "NEGATIVE_RATES_CAPPED"
    HISTORY_SHORT = "HISTORY_SHORT"
    HISTORY_TOO_SHORT = "HISTORY_TOO_SHORT"
    POINTS_PER_PARAM_LOW = "POINTS_PER_PARAM_LOW"
    LONG_SHUTIN = "LONG_SHUTIN"
    # L3 models (§9)
    TAU_AT_BOUND = "TAU_AT_BOUND"
    VARIANT_NOT_AVAILABLE = "VARIANT_NOT_AVAILABLE"
    RESIDUAL_AUTOCORRELATED = "RESIDUAL_AUTOCORRELATED"
    BLIND_FIT_POOR = "BLIND_FIT_POOR"
    SUM_F_CONSTRAINT_ACTIVE = "SUM_F_CONSTRAINT_ACTIVE"


@lru_cache(maxsize=1)
def load_condition_map() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}


@dataclass(frozen=True)
class Condition:
    """One emitted technical condition with the context needed to render its message."""

    code: ConditionCode
    context: dict[str, Any] = field(default_factory=dict)
    severity: Severity | None = None  # None → default from the map
    scope: str = "field"  # "field", "sector:<id>", "well:<id>", "pair:<i>|<j>"

    @property
    def entry(self) -> dict[str, Any]:
        return load_condition_map()[self.code.value]

    @property
    def level(self) -> Severity:
        return self.severity or Severity(self.entry["severity"])

    @property
    def message(self) -> str:
        return _fill(self.entry["message"], self.context)

    @property
    def action(self) -> str:
        return _fill(self.entry["action"], self.context)

    @property
    def technical(self) -> str:
        return str(self.entry["technical"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.level.value,
            "scope": self.scope,
            "message": self.message,
            "action": self.action,
            "technical": self.technical,
            "context": dict(self.context),
        }


def _fill(template: str, ctx: dict[str, Any]) -> str:
    try:
        return template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return template


class ConditionLog:
    """Ordered, de-duplicated collection of conditions for one run / window / sector."""

    def __init__(self) -> None:
        self._items: list[Condition] = []

    def emit(self, code: ConditionCode, scope: str = "field", **context: Any) -> Condition:
        cond = Condition(code=code, context=context, scope=scope)
        if cond not in self._items:
            self._items.append(cond)
        return cond

    def extend(self, other: ConditionLog) -> None:
        for c in other._items:
            if c not in self._items:
                self._items.append(c)

    def __iter__(self) -> Iterator[Condition]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @property
    def items(self) -> list[Condition]:
        return list(self._items)

    def codes(self) -> set[ConditionCode]:
        return {c.code for c in self._items}

    def has(self, code: ConditionCode) -> bool:
        return any(c.code == code for c in self._items)

    def warnings(self) -> list[Condition]:
        return [c for c in self._items if c.level.rank >= Severity.WARNING.rank]

    def worst(self) -> Severity:
        return max((c.level for c in self._items), key=lambda s: s.rank, default=Severity.INFO)

    def to_list(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self._items]
