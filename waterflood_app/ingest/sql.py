"""Database connectors — architecture §5 (sources) and §18 (governance).

* SQLite and PostgreSQL first, then SQL Server and Oracle through SQLAlchemy dialects;
* read-only credentials live in a secrets store (file backend under the project root with
  owner-only permissions, or environment variables), referenced by name — never in the
  project config, never returned by the API;
* the browser never opens a database socket: the backend connects, lists tables/columns and
  reads whole tables; an optional query override is restricted to reviewer/admin and logged.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

SUPPORTED = ("sqlite", "postgresql", "mssql", "oracle", "mysql")
_DIALECT_PREFIX = {
    "sqlite": "sqlite",
    "postgresql": "postgresql+psycopg2",
    "mssql": "mssql+pyodbc",
    "oracle": "oracle+oracledb",
    "mysql": "mysql+pymysql",
}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_\.\$]*$")


class SecretsStore:
    """Named secrets. File backend (``<root>/secrets.json``, 0600) with environment override ``WFO_SECRET_<NAME>``."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "secrets.json"

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return dict(json.loads(self.path.read_text(encoding="utf-8")))

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # Windows ACLs
            pass

    def get(self, name: str) -> str | None:
        env = os.environ.get(f"WFO_SECRET_{name.upper()}")
        if env is not None:
            return env
        return self._load().get(name)

    def names(self) -> list[str]:
        return sorted(self._load())

    def delete(self, name: str) -> None:
        data = self._load()
        data.pop(name, None)
        self.path.write_text(json.dumps(data), encoding="utf-8")


@dataclass
class ConnectionSpec:
    """Everything needed to build a SQLAlchemy URL except the password (a secret name)."""

    kind: str  # sqlite | postgresql | mssql | oracle | mysql | files
    host: str = ""
    port: int | None = None
    database: str = ""  # or the SQLite / folder path for sqlite / files
    user: str = ""
    secret_ref: str = ""  # name in the secrets store
    schema: str = ""
    options: dict[str, str] = field(default_factory=dict)
    query_override: str | None = None  # reviewer/admin only (§18)

    def url(self, secrets: SecretsStore | None) -> str:
        if self.kind not in SUPPORTED:
            raise ValueError(f"unsupported database kind {self.kind!r}")
        if self.kind == "sqlite":
            return f"sqlite:///{self.database}"
        pw = (secrets.get(self.secret_ref) if secrets and self.secret_ref else None) or ""
        auth = f"{self.user}:{pw}@" if self.user else ""
        port = f":{self.port}" if self.port else ""
        url_opts = {k: v for k, v in self.options.items() if not k.startswith("wfo_")}  # wfo_* are app flags
        opts = ("?" + "&".join(f"{k}={v}" for k, v in url_opts.items())) if url_opts else ""
        return f"{_DIALECT_PREFIX[self.kind]}://{auth}{self.host}{port}/{self.database}{opts}"

    def public(self) -> dict[str, Any]:
        """Safe to return from the API: no password, only the secret's name."""
        d = dict(self.__dict__)
        d["options"] = dict(self.options)
        return d


class SqlSource:
    """Read-only access to a database through SQLAlchemy (reflection + whole-table reads)."""

    def __init__(self, spec: ConnectionSpec, secrets: SecretsStore | None = None) -> None:
        from sqlalchemy import create_engine

        self.spec = spec
        self.engine = create_engine(spec.url(secrets), pool_pre_ping=True, future=True)

    def test(self) -> dict[str, Any]:
        from sqlalchemy import text

        with self.engine.connect() as c:
            c.execute(text("SELECT 1"))
        return {"ok": True, "kind": self.spec.kind, "tables": len(self.tables())}

    def tables(self) -> list[str]:
        from sqlalchemy import inspect

        insp = inspect(self.engine)
        names = insp.get_table_names(schema=self.spec.schema or None)
        names += insp.get_view_names(schema=self.spec.schema or None)
        return sorted(names)

    def columns(self, table: str) -> list[dict[str, str]]:
        from sqlalchemy import inspect

        insp = inspect(self.engine)
        return [
            {"name": c["name"], "type": str(c["type"])}
            for c in insp.get_columns(table, schema=self.spec.schema or None)
        ]

    def read_table(self, table: str, limit: int | None = None) -> pl.DataFrame:
        if not _IDENT.match(table):
            raise ValueError(f"invalid table name {table!r}")
        qualified = f"{self.spec.schema}.{table}" if self.spec.schema else table
        sql = f"SELECT * FROM {qualified}" + (
            f" LIMIT {int(limit)}" if limit and self.spec.kind in ("sqlite", "postgresql", "mysql") else ""
        )
        return self.read_query(sql)

    def read_query(self, sql: str) -> pl.DataFrame:
        """Free SQL — only reachable through the reviewer/admin-gated, audited override (§18)."""
        import pandas as pd
        from sqlalchemy import text

        if not sql.strip().lower().startswith(("select", "with")):
            raise ValueError("only SELECT statements are allowed")
        with self.engine.connect() as c:
            df = pd.read_sql(text(sql), c)
        return pl.from_pandas(df)

    def read_tables(self, names: list[str]) -> dict[str, pl.DataFrame]:
        return {n: self.read_table(n) for n in names}


def read_source(
    spec: ConnectionSpec, secrets: SecretsStore | None, tables: list[str] | None = None
) -> dict[str, pl.DataFrame]:
    """Uniform entry: files folder / workbook, or a database (all tables or the named ones)."""
    from waterflood_app.ingest.connectors import read_tables as read_files

    if spec.kind == "files":
        out = read_files(Path(spec.database))
        return {k: v for k, v in out.items() if tables is None or k in tables}
    src = SqlSource(spec, secrets)
    if spec.query_override:
        return {"query": src.read_query(spec.query_override)}
    return src.read_tables(tables or src.tables())
