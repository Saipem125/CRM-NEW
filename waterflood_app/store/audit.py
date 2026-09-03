"""Audit log — architecture §3.1, §18: every load, run, override, approval and implementation entry is
logged with user (ID only, PII-free), acting role, time, data hash and config hash."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from waterflood_app.store.project import ProjectStore


class AuditLog:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def record(
        self,
        actor: str,
        acting_role: str,
        action: str,
        object_type: str,
        object_id: str,
        data_hash: str = "",
        config_hash: str = "",
        **detail: Any,
    ) -> int:
        with self.store.conn() as c:
            cur = c.execute(
                "INSERT INTO audit (at, actor, acting_role, action, object_type, object_id, data_hash, config_hash, detail_json) VALUES (?,?,?,?,?,?,?,?,?)",  # noqa: E501
                (
                    datetime.now(UTC).isoformat(),
                    actor,
                    acting_role,
                    action,
                    object_type,
                    object_id,
                    data_hash,
                    config_hash,
                    json.dumps(detail, default=str),
                ),
            )
            return int(cur.lastrowid or 0)

    def entries(self, object_id: str | None = None, actor: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        q = "SELECT seq, at, actor, acting_role, action, object_type, object_id, data_hash, config_hash, detail_json FROM audit"  # noqa: E501
        cond: list[str] = []
        args: list[Any] = []
        if object_id:
            cond.append("object_id=?")
            args.append(object_id)
        if actor:
            cond.append("actor=?")
            args.append(actor)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        with self.store.conn() as c:
            rows = c.execute(q, tuple(args)).fetchall()
        return [
            {
                "seq": r[0],
                "at": r[1],
                "actor": r[2],
                "acting_role": r[3],
                "action": r[4],
                "object_type": r[5],
                "object_id": r[6],
                "data_hash": r[7],
                "config_hash": r[8],
                "detail": json.loads(r[9]),
            }
            for r in rows
        ]
