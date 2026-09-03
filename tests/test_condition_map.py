"""§17: every Condition code emitted anywhere in the code base has a messaging-map entry, and vice versa."""

from __future__ import annotations

import re
from pathlib import Path

from waterflood_app.messaging.conditions import (
    Condition,
    ConditionCode,
    Severity,
    load_condition_map,
)

PKG = Path(__file__).resolve().parents[1] / "waterflood_app"


def test_every_enum_member_has_a_map_entry() -> None:
    cmap = load_condition_map()
    missing = [c.value for c in ConditionCode if c.value not in cmap]
    assert not missing, f"codes without a message: {missing}"
    for code, entry in cmap.items():
        assert {"technical", "severity", "message", "action"} <= set(entry), code
        assert entry["message"].strip() and entry["action"].strip(), code
        Severity(entry["severity"])


def test_every_map_entry_is_a_known_code() -> None:
    known = {c.value for c in ConditionCode}
    unknown = [k for k in load_condition_map() if k not in known]
    assert not unknown, f"map entries with no ConditionCode: {unknown}"


def test_every_code_emitted_in_source_exists() -> None:
    pattern = re.compile(r"ConditionCode\.([A-Z0-9_]+)")
    used: set[str] = set()
    for p in PKG.rglob("*.py"):
        used |= set(pattern.findall(p.read_text(encoding="utf-8")))
    known = {c.value for c in ConditionCode}
    assert used <= known, used - known
    # the §17 table rows must all be present
    for row in (
        "OD_LOW",
        "CRMIP_DATA_LOW",
        "INJ_CV_LOW",
        "TAU_DT_LOW",
        "SUM_F_HIGH",
        "NO_BHP_EVENTS_HIGH",
        "SIMULTANEOUS_PI",
        "GOR_ABOVE_RS",
        "MULTISTART_SPREAD_HIGH",
        "FORECAST_OUT_OF_BAND_DQ_CHANGED",
        "CUSUM_SHIFT",
        "CONFIDENCE_LOW",
    ):
        assert row in known


def test_message_rendering_fills_context() -> None:
    c = Condition(ConditionCode.SIMULTANEOUS_PI, {"well": "P-7"})
    assert "P-7" in c.message
    assert c.level == Severity.WARNING
    d = c.to_dict()
    assert d["code"] == "SIMULTANEOUS_PI" and d["action"]
