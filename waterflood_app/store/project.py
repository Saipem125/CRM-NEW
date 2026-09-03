"""Project store — architecture §18 / §19: immutable raw snapshots (Parquet) + a SQLite catalogue.

A project holds the saved mapping / filter / well list (its config, §5), data snapshots keyed
by their hash (raw snapshots are immutable; a re-load with identical content is the same
snapshot), and the runs, recommendations, audit log and validation record kept by the other
store modules in the same SQLite file (Postgres in production, SQLite offline, §18).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, asset TEXT, created_at TEXT, config_json TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    data_hash TEXT PRIMARY KEY, project_id TEXT, created_at TEXT, tables_json TEXT, n_rows INTEGER
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, project_id TEXT, data_hash TEXT, config_hash TEXT, code_version TEXT,
    created_at TEXT, seed INTEGER, summary_json TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS models (
    run_id TEXT, sector TEXT, variant TEXT, params_json TEXT, metrics_json TEXT,
    PRIMARY KEY (run_id, sector, variant)
);
CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY, project_id TEXT, run_id TEXT, state TEXT, payload_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, actor TEXT, acting_role TEXT, action TEXT,
    object_type TEXT, object_id TEXT, data_hash TEXT, config_hash TEXT, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS validation (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, at TEXT, payload_json TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE, display_name TEXT, pw_hash TEXT,
    roles_json TEXT, assets_json TEXT, active INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY, project_id TEXT, spec_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS mappings (
    project_id TEXT PRIMARY KEY, connection_id TEXT, mapping_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT, status TEXT, progress REAL, message TEXT,
    created_at TEXT, updated_at TEXT, result_id TEXT, error TEXT, payload_json TEXT
);
CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY, url TEXT, events_json TEXT, secret TEXT, active INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS bundles (
    run_id TEXT PRIMARY KEY, bundle_json TEXT, created_at TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ProjectStore:
    root: Path
    db_path: Path

    @classmethod
    def open(cls, root: Path | str) -> ProjectStore:
        root = Path(root)
        (root / "snapshots").mkdir(parents=True, exist_ok=True)
        store = cls(root, root / "store.sqlite")
        with store.conn() as c:
            c.executescript(SCHEMA)
        return store

    def conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # ---- projects --------------------------------------------------------------------
    def create_project(self, project_id: str, asset: str, config: dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO projects VALUES (?,?,?,?)",
                (project_id, asset, _now(), json.dumps(config, sort_keys=True, default=str)),
            )

    def update_project_config(self, project_id: str, config: dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE projects SET config_json=? WHERE id=?",
                (json.dumps(config, sort_keys=True, default=str), project_id),
            )

    def list_projects(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT id, asset, created_at, config_json FROM projects ORDER BY created_at").fetchall()
        return [{"id": r[0], "asset": r[1], "created_at": r[2], "config": json.loads(r[3])} for r in rows]

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.conn() as c:
            row = c.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_setting(self, key: str, value: Any) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES (?,?,?)", (key, json.dumps(value, default=str), _now()))

    def project_config(self, project_id: str) -> dict[str, Any]:
        with self.conn() as c:
            row = c.execute("SELECT config_json FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return dict(json.loads(row[0]))

    # ---- snapshots ---------------------------------------------------------------------
    def save_snapshot(self, project_id: str, tables: dict[str, pl.DataFrame]) -> str:
        """Write each table as Parquet under snapshots/<hash>/; returns the data hash. Immutable."""
        h = hashlib.sha256()
        for name in sorted(tables):
            h.update(name.encode())
            h.update(tables[name].sort(tables[name].columns).write_csv().encode("utf-8"))
        digest = h.hexdigest()
        d = self.root / "snapshots" / digest
        if not d.exists():
            d.mkdir(parents=True)
            for name, frame in tables.items():
                frame.write_parquet(d / f"{name}.parquet")
            with self.conn() as c:
                c.execute(
                    "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?)",
                    (
                        digest,
                        project_id,
                        _now(),
                        json.dumps(sorted(tables)),
                        int(sum(t.height for t in tables.values())),
                    ),
                )
        return digest

    def load_snapshot(self, data_hash: str) -> dict[str, pl.DataFrame]:
        d = self.root / "snapshots" / data_hash
        if not d.exists():
            raise KeyError(data_hash)
        return {p.stem: pl.read_parquet(p) for p in sorted(d.glob("*.parquet"))}

    def snapshots(self, project_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT data_hash, project_id, created_at, tables_json, n_rows FROM snapshots" + (
            " WHERE project_id=?" if project_id else ""
        )
        with self.conn() as c:
            rows = c.execute(q, (project_id,) if project_id else ()).fetchall()
        return [
            {"data_hash": r[0], "project_id": r[1], "created_at": r[2], "tables": json.loads(r[3]), "n_rows": r[4]}
            for r in rows
        ]
