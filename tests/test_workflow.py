"""Milestone 2 workflow and store: state machine, snapshots, separation rule, evaluation, §15 rules, audit."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from waterflood_app.config import load_config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.store.audit import AuditLog
from waterflood_app.store.project import ProjectStore
from waterflood_app.store.registry import RunRegistry
from waterflood_app.store.validation_record import ValidationRecord
from waterflood_app.workflow import alerts
from waterflood_app.workflow.evaluation import (
    EvaluationRecord,
    due_evaluations,
    evaluate,
    hit_rate_by_badge,
    recalibrated_threshold,
)
from waterflood_app.workflow.scheduler import MonthlyInputs, rules
from waterflood_app.workflow.states import Actor, Recommendation, State, WorkflowError

CFG = load_config()


def _rec(conf: str = "HIGH") -> Recommendation:
    return Recommendation("R1", "ALPHA", "eng1", "d" * 64, "c" * 64, "m1", {"I-1": 1500.0, "I-2": 900.0}, conf)


def test_lifecycle_and_snapshot() -> None:
    r = _rec()
    eng, rev, app, ops = (
        Actor("eng1", "engineer"),
        Actor("sr1", "reviewer"),
        Actor("mgr", "approver"),
        Actor("ops", "operations"),
    )
    with pytest.raises(WorkflowError):
        r.approve(app)  # cannot approve a DRAFT
    with pytest.raises(WorkflowError):
        r.review(eng)  # wrong role
    r.review(rev, "checked Details")
    r.approve(app)
    assert r.state == State.APPROVED and r.snapshot is not None and len(r.snapshot.digest) == 64
    frozen = r.snapshot.digest
    r.implement(ops, {"I-1": 1450.0, "I-2": 900.0})
    assert r.state.value == "IMPLEMENTED" and r.history[-1].extra["deviations"] == {"I-1": -50.0}
    assert r.snapshot.digest == frozen  # immutable
    r.evaluate(eng, {"outcome": "within band"})
    assert r.state.value == "EVALUATED"
    d = r.to_dict()
    assert [e["action"] for e in d["history"]] == ["review", "approve", "implement", "evaluate"]
    assert all(e["actor"] and e["acting_role"] for e in d["history"])
    draft2 = r.new_draft(eng, {"I-1": 1600.0, "I-2": 800.0}, "HIGH", "R2")
    assert draft2.state == State.DRAFT and draft2.snapshot is None and r.state.value == "EVALUATED"


def test_separation_policy_and_low_confidence_override() -> None:
    r = _rec()
    r.review(Actor("sr1", "reviewer"))
    with pytest.raises(WorkflowError):
        r.approve(Actor("eng1", "approver"), separation_policy="enforce")
    r.approve(Actor("eng1", "approver"), separation_policy="log")
    assert r.approved_by_originator
    low = _rec("LOW")
    low.review(Actor("sr1", "reviewer"))
    with pytest.raises(WorkflowError):
        low.approve(Actor("mgr", "approver"))
    low.approve(Actor("mgr", "approver"), override_reason="pilot area, reversible change")
    assert low.override_reason and low.state == State.APPROVED
    with pytest.raises(WorkflowError):
        Actor("x", "engineer", roles=("viewer",))


def test_evaluation_records_and_calibration() -> None:
    members = np.array([900.0, 1000.0, 1100.0, 1050.0, 950.0])
    rec = evaluate("R1", 3, date(2021, 4, 1), members, 1000.0, "HIGH", CFG)
    assert rec.outcome == "within band" and rec.forecast_p10 < 1000.0 < rec.forecast_p90
    assert evaluate("R1", 6, date(2021, 7, 1), members, 1300.0, "HIGH", CFG).outcome == "better"
    assert evaluate("R1", 6, date(2021, 7, 1), members, 800.0, "MEDIUM", CFG).outcome == "worse"
    assert due_evaluations(date(2021, 1, 15), date(2021, 4, 20), CFG) == [3]
    assert due_evaluations(date(2021, 1, 15), date(2021, 8, 1), CFG, done={3}) == [6]
    recs = [
        EvaluationRecord("r", 3, date(2021, 1, 1), 1, 2, 3, v, o, "HIGH")
        for v, o in [
            (2, "within band"),
            (2, "within band"),
            (0, "worse"),
            (4, "better"),
            (0, "worse"),
            (2, "within band"),
        ]
    ]
    table = hit_rate_by_badge(recs)
    assert table["HIGH"]["n"] == 6 and table["HIGH"]["within_band"] == pytest.approx(0.5)
    assert recalibrated_threshold(recs, 0.85) == pytest.approx(
        0.87
    )  # HIGH landed within/better 4/6 < 80 % → raise floor


def test_surveillance_rules_table() -> None:
    log = ConditionLog()
    inp = MonthlyInputs(
        today=date(2021, 5, 1),
        producers=["P-1", "P-2", "P-3"],
        forecast_p10=np.array([90.0, 90.0, 90.0]),
        forecast_p90=np.array([110.0, 110.0, 110.0]),
        actual=np.array([100.0, 130.0, 60.0]),
        dq_changed={"P-2": True, "P-3": False},
        new_events=[{"well": "I-4", "date": "2021-04-10", "type": "WORKOVER"}],
        implemented_at=date(2021, 1, 10),
        last_full_tournament=date(2020, 12, 1),
        parameters_shifted=True,
    )
    acts = rules(inp, CFG, log)
    triggers = [a.trigger for a in acts]
    assert triggers[0] == "new month of data"
    assert "forecast error outside band, data-quality changed" in triggers and log.has(
        ConditionCode.FORECAST_OUT_OF_BAND_DQ_CHANGED
    )
    res = next(a for a in acts if a.trigger.startswith("forecast error outside band, data-quality unchanged"))
    assert res.wells == ["P-3"] and "new DRAFT" in res.action
    assert next(a for a in acts if a.trigger == "new event").wells == ["I-4"]
    assert "quarterly" in triggers
    assert any(a.trigger == "implemented plan reaches 3 months" for a in acts)
    al = alerts.merge(alerts.from_actions(acts), alerts.from_conditions(log))
    assert any(a.kind == "data_quality" and "P-2" in a.wells for a in al)
    rv = alerts.revert_alerts(
        [{"well": "I-1", "rate_from": 1000.0, "connected_producers": ["P-1"]}], {"P-1": 0.8}, {"P-1": 0.7}, 0.05
    )
    assert rv and rv[0].kind == "revert"
    assert (
        alerts.dq_report_changed({"outliers": 3}, {"outliers": 5}) is not None
        and alerts.dq_report_changed({"a": 1}, {"a": 1}) is None
    )


def test_store_registry_audit_validation(tmp_path: Path) -> None:
    store = ProjectStore.open(tmp_path / "proj")
    store.create_project("P1", "ALPHA", {"mapping": {"rates": {"table": "rates"}}, "wells": ["I-1"]})
    assert store.project_config("P1")["wells"] == ["I-1"]
    tables = {"rates": pl.DataFrame({"well": ["I-1"], "date": [date(2020, 1, 1)], "q_inj": [100.0]})}
    h1 = store.save_snapshot("P1", tables)
    h2 = store.save_snapshot("P1", tables)
    assert h1 == h2 and len(h1) == 64 and (tmp_path / "proj" / "snapshots" / h1 / "rates.parquet").exists()
    assert store.load_snapshot(h1)["rates"].height == 1 and len(store.snapshots("P1")) == 1
    reg = RunRegistry(store)
    reg.register("run1", "P1", h1, "c" * 64, 7, {"confidence": "HIGH"}, version="abc123")
    reg.save_model("run1", "S1", "crmp", {"f_ij": {}}, {"blind_r2": 0.95})
    assert reg.find(h1, "c" * 64)["id"] == "run1"  # type: ignore[index]
    got = reg.get("run1")
    assert got is not None and got["models"][0]["variant"] == "crmp" and got["seed"] == 7
    reg.save_recommendation("R1", "P1", "run1", "DRAFT", {"rates": {"I-1": 100.0}})
    assert reg.recommendation("R1")["state"] == "DRAFT"  # type: ignore[index]
    audit = AuditLog(store)
    audit.record("eng1", "engineer", "run", "run", "run1", h1, "c" * 64, seed=7)
    audit.record("mgr", "approver", "approve", "recommendation", "R1")
    entries = audit.entries()
    assert len(entries) == 2 and entries[0]["action"] == "approve" and entries[1]["detail"] == {"seed": 7}
    assert audit.entries(actor="mgr")[0]["acting_role"] == "approver"
    vr = ValidationRecord(store)
    vr.add("tier1_synthetic", {"case": "streak_5x4", "pass": True})
    vr.add_evaluation(EvaluationRecord("R1", 3, date(2021, 4, 1), 900.0, 1000.0, 1100.0, 1000.0, "within band", "HIGH"))
    assert vr.calibration_table()["HIGH"]["within_band"] == 1.0
    with pytest.raises(ValueError):
        vr.add("bogus", {})
