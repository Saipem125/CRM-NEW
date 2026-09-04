"""Writeback of injection targets — architecture §18 Integration ("Write (optional, approver-gated):
injection targets to the surveillance system").

A writeback appends one row per injector to a target table (SQL) or CSV file (file connections);
each row carries the recommendation, run and data-hash identifiers plus who approved it, so the
surveillance system can trace every target back to an approved, snapshotted recommendation.
The connection used must be flagged for writing (``options.wfo_writeback = "1"``) because the
read connections hold read-only credentials (§18).
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waterflood_app.ingest.sql import ConnectionSpec, SecretsStore


class WritebackError(Exception):
    """User-facing reason a writeback could not be done."""


@dataclass(frozen=True)
class TargetRow:
    well: str
    target_rate: float
    rate_unit: str
    step_this_week: float | None
    target_date: str | None
    effective_date: str
    recommendation_id: str
    run_id: str
    data_hash: str
    config_hash: str
    confidence: str
    approved_by: str
    written_by: str
    written_at: str
    note: str = ""


COLUMNS = [f.name for f in fields(TargetRow)]


def build_rows(
    rec: dict[str, Any],
    rate_unit: str,
    effective_date: str,
    written_by: str,
    note: str = "",
) -> list[TargetRow]:
    """Rows from a recommendation record in display units (the API converts before calling)."""
    approved_by = next((h["actor"] for h in reversed(rec.get("history", [])) if h.get("action") == "approve"), "")
    actions = {a["well"]: a for a in (rec.get("recommendation") or {}).get("actions", [])}
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for well, rate in (rec.get("recommended_rates") or {}).items():
        a = actions.get(well, {})
        rows.append(
            TargetRow(
                well=well,
                target_rate=round(float(rate), 3),
                rate_unit=rate_unit,
                step_this_week=round(float(a["step_this_week"]), 3) if a.get("step_this_week") is not None else None,
                target_date=a.get("target_date"),
                effective_date=effective_date,
                recommendation_id=str(rec.get("id", "")),
                run_id=str(rec.get("run_id", "")),
                data_hash=str(rec.get("data_hash", "")),
                config_hash=str(rec.get("config_hash", "")),
                confidence=str(rec.get("confidence", "")),
                approved_by=approved_by,
                written_by=written_by,
                written_at=now,
                note=note,
            )
        )
    return rows


def is_writable(spec: ConnectionSpec) -> bool:
    return str(spec.options.get("wfo_writeback", "0")).lower() in ("1", "true", "yes")


def write_targets(
    rows: list[TargetRow], spec: ConnectionSpec, secrets: SecretsStore | None, table: str
) -> dict[str, Any]:
    """Append the rows to ``table`` on the connection; returns a description of what was written."""
    if not rows:
        raise WritebackError("The recommendation has no injector targets to write.")
    if not is_writable(spec):
        raise WritebackError(
            "This connection is not enabled for writeback. An admin must create a connection with the "
            "option wfo_writeback=1 (and write credentials) for the surveillance system."
        )
    if not table.replace("_", "").isalnum():
        raise WritebackError("The target table name may contain letters, digits and underscores only.")
    if spec.kind == "files":
        path = Path(spec.database) / f"{table}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            if new:
                w.writeheader()
            for r in rows:
                w.writerow(asdict(r))
        return {"target": str(path), "kind": "csv", "n_rows": len(rows), "created": new}
    import sqlalchemy as sa

    engine = sa.create_engine(spec.url(secrets))
    md = sa.MetaData(schema=spec.schema or None)
    t = sa.Table(
        table,
        md,
        sa.Column("well", sa.String(64), nullable=False),
        sa.Column("target_rate", sa.Float, nullable=False),
        sa.Column("rate_unit", sa.String(16)),
        sa.Column("step_this_week", sa.Float),
        sa.Column("target_date", sa.String(10)),
        sa.Column("effective_date", sa.String(10)),
        sa.Column("recommendation_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32)),
        sa.Column("data_hash", sa.String(64)),
        sa.Column("config_hash", sa.String(64)),
        sa.Column("confidence", sa.String(8)),
        sa.Column("approved_by", sa.String(128)),
        sa.Column("written_by", sa.String(128)),
        sa.Column("written_at", sa.String(32)),
        sa.Column("note", sa.String(500)),
    )
    with engine.begin() as conn:
        created = not sa.inspect(conn).has_table(table, schema=spec.schema or None)
        md.create_all(conn)
        conn.execute(t.insert(), [asdict(r) for r in rows])
    engine.dispose()
    return {"target": f"{spec.kind}:{spec.database}/{table}", "kind": "sql", "n_rows": len(rows), "created": created}
