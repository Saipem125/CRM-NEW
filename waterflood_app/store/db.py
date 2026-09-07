"""Database shim — architecture §19 "Postgres (SQLite offline)".

The store code is written against the SQLite DB-API (``?`` placeholders, ``INSERT OR REPLACE``,
``executescript``, ``cursor.lastrowid``). ``Database`` keeps SQLite untouched (the connection is
returned as is) and, for a PostgreSQL URL (``WFO_DB_URL=postgresql://user:pw@host/db``), wraps a
psycopg connection that translates those few SQLite idioms:

* ``?`` → ``%s``
* ``INSERT OR REPLACE INTO t VALUES (…)`` → ``INSERT … ON CONFLICT (pk) DO UPDATE SET …``
* ``INSERT OR IGNORE INTO t …`` → ``INSERT … ON CONFLICT DO NOTHING``
* ``INSERT INTO <serial table>`` gets ``RETURNING <pk>`` so ``lastrowid`` works
* ``executescript`` splits the schema and maps ``INTEGER PRIMARY KEY AUTOINCREMENT`` → ``BIGSERIAL``,
  ``INTEGER`` → ``BIGINT``

Connections are reused per thread and committed on context exit (rolled back on error), which is
the SQLite ``with conn:`` contract the callers rely on.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from types import TracebackType
from typing import Any

_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);", re.S)


def parse_schema(schema: str) -> dict[str, tuple[list[str], list[str], str | None]]:
    """table → (columns, primary-key columns, serial column) from the SQLite DDL."""
    out: dict[str, tuple[list[str], list[str], str | None]] = {}
    for name, body in _TABLE_RE.findall(schema):
        cols: list[str] = []
        pk: list[str] = []
        serial: str | None = None
        depth = 0
        parts: list[str] = []
        cur = ""
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        for part in parts:
            p = part.strip()
            if not p:
                continue
            up = p.upper()
            if up.startswith("PRIMARY KEY"):
                pk = [c.strip() for c in p[p.index("(") + 1 : p.rindex(")")].split(",")]
                continue
            col = p.split()[0]
            cols.append(col)
            if "PRIMARY KEY" in up:
                pk = [col]
                if "AUTOINCREMENT" in up:
                    serial = col
        out[name] = (cols, pk, serial)
    return out


class PgCursor:
    def __init__(self, cur: Any, lastrowid: int | None = None) -> None:
        self._cur = cur
        self.lastrowid = lastrowid
        self.rowcount = getattr(cur, "rowcount", -1)

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> list[Any]:
        return list(self._cur.fetchall())


class PgConnection:
    """psycopg connection behind the SQLite-style API used by the store."""

    def __init__(self, conn: Any, tables: dict[str, tuple[list[str], list[str], str | None]]) -> None:
        self._conn = conn
        self._tables = tables

    # ---- SQL translation ------------------------------------------------------------------
    def translate(self, sql: str) -> tuple[str, str | None]:
        """Return the PostgreSQL statement and, for serial-key inserts, the column to RETURN."""
        s = sql.strip()
        s = s.replace("?", "%s")
        up = s.upper()
        returning: str | None = None
        m = re.match(r"INSERT OR (REPLACE|IGNORE) INTO (\w+)", s, re.I)
        if m:
            mode, table = m.group(1).upper(), m.group(2)
            cols, pk, _ = self._tables[table]
            s = re.sub(r"^INSERT OR (REPLACE|IGNORE) INTO", "INSERT INTO", s, flags=re.I)
            if mode == "IGNORE" or not pk:
                s += " ON CONFLICT DO NOTHING"
            else:
                updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in pk) or f"{pk[0]}=EXCLUDED.{pk[0]}"
                s += f" ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}"
            return s, None
        m = re.match(r"INSERT INTO (\w+)", s, re.I)
        if m and up.startswith("INSERT"):
            table = m.group(1)
            info = self._tables.get(table)
            if info and info[2]:
                returning = info[2]
                s += f" RETURNING {returning}"
        return s, returning

    def execute(self, sql: str, params: Any = ()) -> PgCursor:
        stmt, returning = self.translate(sql)
        cur = self._conn.cursor()
        cur.execute(stmt, tuple(params))
        lastrowid = None
        if returning:
            row = cur.fetchone()
            lastrowid = int(row[0]) if row else None
        return PgCursor(cur, lastrowid)

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            st = stmt.strip()
            if not st:
                continue
            st = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", st, flags=re.I)
            st = re.sub(r"\bREAL\b", "DOUBLE PRECISION", st)
            st = re.sub(r"\bINTEGER\b", "BIGINT", st)  # SQLite integers are 64-bit; seeds are 32-bit unsigned
            self._conn.cursor().execute(st)
        self._conn.commit()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PgConnection:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()


class Database:
    """SQLite file or PostgreSQL URL; ``connect()`` returns a connection usable as ``with db.connect() as c``."""

    def __init__(self, target: str | Path, schema: str) -> None:
        self.target = str(target)
        self.schema = schema
        self.kind = "postgresql" if self.target.startswith(("postgresql://", "postgres://")) else "sqlite"
        self.tables = parse_schema(schema)
        self._local = threading.local()

    @property
    def is_postgres(self) -> bool:
        return self.kind == "postgresql"

    def describe(self) -> str:
        if self.kind == "sqlite":
            return self.target
        return re.sub(r"://([^:/@]+)(:[^@]*)?@", r"://\1:***@", self.target)

    def connect(self) -> Any:
        if self.kind == "sqlite":
            return sqlite3.connect(self.target)
        import psycopg

        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            conn = psycopg.connect(self.target, autocommit=False)
            self._local.conn = conn
        else:
            try:
                conn.rollback()  # clean slate for the next unit of work
            except Exception:
                conn = psycopg.connect(self.target, autocommit=False)
                self._local.conn = conn
        return PgConnection(conn, self.tables)

    def create_schema(self) -> None:
        with self.connect() as c:
            c.executescript(self.schema)

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
