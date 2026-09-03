"""Validation record — architecture §16: Tier-1 suite results per solver version, Tier-2 back-tests,
Tier-3 live evaluations; feeds the admin validation dashboard and the quarterly recalibration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from waterflood_app.store.project import ProjectStore
from waterflood_app.workflow.evaluation import EvaluationRecord, hit_rate_by_badge


class ValidationRecord:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def add(self, kind: str, payload: dict[str, Any]) -> None:
        if kind not in ("tier1_synthetic", "tier2_backtest", "tier3_evaluation"):
            raise ValueError(kind)
        with self.store.conn() as c:
            c.execute(
                "INSERT INTO validation (kind, at, payload_json) VALUES (?,?,?)",
                (kind, datetime.now(UTC).isoformat(), json.dumps(payload, default=str)),
            )

    def add_evaluation(self, rec: EvaluationRecord) -> None:
        self.add("tier3_evaluation", rec.to_dict())

    def entries(self, kind: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT id, kind, at, payload_json FROM validation" + (" WHERE kind=?" if kind else "") + " ORDER BY id"
        with self.store.conn() as c:
            rows = c.execute(q, (kind,) if kind else ()).fetchall()
        return [{"id": r[0], "kind": r[1], "at": r[2], "payload": json.loads(r[3])} for r in rows]

    def calibration_table(self) -> dict[str, dict[str, float]]:
        """Hit rate within band per badge over all Tier-3 evaluations (the §16 dashboard table)."""
        recs = []
        for e in self.entries("tier3_evaluation"):
            p = e["payload"]
            from datetime import date

            recs.append(
                EvaluationRecord(
                    p["recommendation_id"],
                    p["months_after"],
                    date.fromisoformat(p["evaluated_on"]),
                    p["forecast_p10"],
                    p["forecast_p50"],
                    p["forecast_p90"],
                    p["realised"],
                    p["outcome"],
                    p["confidence_badge"],
                    p.get("implemented_deviation", {}),
                    p.get("note", ""),
                )
            )
        return hit_rate_by_badge(recs)
