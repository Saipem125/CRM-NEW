"""Run registry — architecture §1, §9, §18: every run stores data hash, config hash, code version, seed;
calibrated model objects and recommendations are kept with their run. Re-running the same triple
yields identical results (fixed seeds), so the triple is the cache key (§5 engineering rules)."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waterflood_app.store.project import ProjectStore


def code_version() -> str:
    """Short git hash of the checked-out code; ``WFO_CODE_VERSION`` (set at image build) when there is no .git."""
    env = os.environ.get("WFO_CODE_VERSION")
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[2],
        ).strip()
    except Exception:
        return "unknown"


class RunRegistry:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def register(
        self,
        run_id: str,
        project_id: str,
        data_hash: str,
        config_hash: str,
        seed: int,
        summary: dict[str, Any],
        status: str = "done",
        version: str | None = None,
    ) -> None:
        with self.store.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    project_id,
                    data_hash,
                    config_hash,
                    version or code_version(),
                    datetime.now(UTC).isoformat(),
                    int(seed),
                    json.dumps(summary, default=str),
                    status,
                ),
            )

    def save_model(
        self, run_id: str, sector: str, variant: str, params: dict[str, Any], metrics: dict[str, Any]
    ) -> None:
        with self.store.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO models VALUES (?,?,?,?,?)",
                (run_id, sector, variant, json.dumps(params, default=str), json.dumps(metrics, default=str)),
            )

    def find(self, data_hash: str, config_hash: str, version: str | None = None) -> dict[str, Any] | None:
        """Cache lookup by the reproducibility triple."""
        q = "SELECT id, code_version, created_at, seed, summary_json, status FROM runs WHERE data_hash=? AND config_hash=?"  # noqa: E501
        args: tuple[Any, ...] = (data_hash, config_hash)
        if version:
            q += " AND code_version=?"
            args += (version,)
        with self.store.conn() as c:
            row = c.execute(q + " ORDER BY created_at DESC LIMIT 1", args).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "code_version": row[1],
            "created_at": row[2],
            "seed": row[3],
            "summary": json.loads(row[4]),
            "status": row[5],
        }

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.store.conn() as c:
            row = c.execute(
                "SELECT id, project_id, data_hash, config_hash, code_version, created_at, seed, summary_json, status FROM runs WHERE id=?",  # noqa: E501
                (run_id,),
            ).fetchone()
            models = c.execute(
                "SELECT sector, variant, params_json, metrics_json FROM models WHERE run_id=?", (run_id,)
            ).fetchall()
        if row is None:
            return None
        return {
            "id": row[0],
            "project_id": row[1],
            "data_hash": row[2],
            "config_hash": row[3],
            "code_version": row[4],
            "created_at": row[5],
            "seed": row[6],
            "summary": json.loads(row[7]),
            "status": row[8],
            "models": [
                {"sector": m[0], "variant": m[1], "params": json.loads(m[2]), "metrics": json.loads(m[3])}
                for m in models
            ],
        }

    def list_runs(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.conn() as c:
            rows = c.execute(
                "SELECT id, data_hash, config_hash, code_version, created_at, status FROM runs WHERE project_id=? ORDER BY created_at DESC",  # noqa: E501
                (project_id,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "data_hash": r[1],
                "config_hash": r[2],
                "code_version": r[3],
                "created_at": r[4],
                "status": r[5],
            }
            for r in rows
        ]

    # ---- result bundles (what the UI draws) --------------------------------------------
    def save_bundle(self, run_id: str, bundle: dict[str, Any]) -> None:
        with self.store.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO bundles VALUES (?,?,?)",
                (run_id, json.dumps(bundle, default=str), datetime.now(UTC).isoformat()),
            )

    def bundle(self, run_id: str) -> dict[str, Any] | None:
        with self.store.conn() as c:
            row = c.execute("SELECT bundle_json FROM bundles WHERE run_id=?", (run_id,)).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    # ---- recommendations ---------------------------------------------------------------
    def save_recommendation(
        self, rec_id: str, project_id: str, run_id: str, state: str, payload: dict[str, Any]
    ) -> None:
        with self.store.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO recommendations VALUES (?,?,?,?,?,?)",
                (rec_id, project_id, run_id, state, json.dumps(payload, default=str), datetime.now(UTC).isoformat()),
            )

    def recommendation(self, rec_id: str) -> dict[str, Any] | None:
        with self.store.conn() as c:
            row = c.execute(
                "SELECT id, project_id, run_id, state, payload_json, updated_at FROM recommendations WHERE id=?",
                (rec_id,),
            ).fetchone()
        return (
            None
            if row is None
            else {
                "id": row[0],
                "project_id": row[1],
                "run_id": row[2],
                "state": row[3],
                "payload": json.loads(row[4]),
                "updated_at": row[5],
            }
        )
