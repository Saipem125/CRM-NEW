"""Post-M5 — PostgreSQL store backend (architecture §19 "Postgres (SQLite offline)").

The SQL translation is unit-tested without a server. The integration part runs the real store,
registry, audit, users, jobs and a full API flow against PostgreSQL when ``WFO_TEST_PG_URL`` points
to a database the test may create tables in (CI provides one through a postgres service; locally a
portable PostgreSQL works: ``initdb`` + ``pg_ctl start`` + ``WFO_TEST_PG_URL=postgresql://wfo@127.0.0.1:55432/wfo``).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from waterflood_app.store.db import Database, PgConnection, parse_schema
from waterflood_app.store.project import SCHEMA

PG_URL = os.environ.get("WFO_TEST_PG_URL")


def test_schema_parse_and_translation() -> None:
    tables = parse_schema(SCHEMA)
    assert tables["models"][1] == ["run_id", "sector", "variant"]
    assert tables["audit"][2] == "seq" and tables["validation"][2] == "id"
    assert tables["projects"][0] == ["id", "asset", "created_at", "config_json"]
    conn = PgConnection(None, tables)
    s, ret = conn.translate("INSERT OR REPLACE INTO projects VALUES (?,?,?,?)")
    assert s == (
        "INSERT INTO projects VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
        "asset=EXCLUDED.asset, created_at=EXCLUDED.created_at, config_json=EXCLUDED.config_json"
    )
    assert ret is None
    s, _ = conn.translate("INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?)")
    assert s.endswith("ON CONFLICT DO NOTHING")
    s, ret = conn.translate("INSERT INTO audit (at, actor) VALUES (?,?)")
    assert s.endswith("RETURNING seq") and ret == "seq"
    s, ret = conn.translate("SELECT id FROM jobs WHERE kind=? ORDER BY created_at DESC LIMIT ?")
    assert s == "SELECT id FROM jobs WHERE kind=%s ORDER BY created_at DESC LIMIT %s" and ret is None
    s, _ = conn.translate("INSERT OR REPLACE INTO models VALUES (?,?,?,?,?)")
    assert "ON CONFLICT (run_id, sector, variant) DO UPDATE SET params_json=EXCLUDED.params_json" in s
    db = Database("postgresql://wfo:secret@db:5432/wfo", SCHEMA)
    assert db.is_postgres and "secret" not in db.describe()
    assert Database(Path("x") / "store.sqlite", SCHEMA).kind == "sqlite"


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    if not PG_URL:
        pytest.skip("WFO_TEST_PG_URL not set")
    import psycopg

    # a throw-away database per test module so tables never collide with another run
    name = f"wfo_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {name}")
    base = PG_URL.rsplit("/", 1)[0]
    url = f"{base}/{name}"
    yield url
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f"DROP DATABASE {name} WITH (FORCE)")


def test_store_registry_audit_on_postgres(pg_url: str, tmp_path: Path) -> None:
    import polars as pl

    from waterflood_app.store.audit import AuditLog
    from waterflood_app.store.project import ProjectStore
    from waterflood_app.store.registry import RunRegistry

    st = ProjectStore.open(tmp_path / "store", db_url=pg_url)
    assert st.db.is_postgres
    st.create_project("p1", "ALPHA", {"unit_system": "field"})
    st.create_project("p1", "ALPHA", {"unit_system": "metric"})  # upsert
    assert st.project_config("p1")["unit_system"] == "metric"
    h1 = st.save_snapshot("p1", {"rates": pl.DataFrame({"well": ["I-1"], "q": [1.0]})})
    h2 = st.save_snapshot("p1", {"rates": pl.DataFrame({"well": ["I-1"], "q": [1.0]})})
    assert h1 == h2 and len(st.snapshots("p1")) == 1
    assert st.load_snapshot(h1)["rates"].height == 1
    st.set_setting("separation_policy", "enforce")
    assert st.get_setting("separation_policy") == "enforce"
    st.set_setting("separation_policy", "log")  # the API test below approves as the originator
    reg = RunRegistry(st)
    reg.register("r1", "p1", h1, "cfg", 7, {"confidence": "HIGH"}, version="abc")
    reg.save_model("r1", "S1", "crmp", {"f": [[0.5]]}, {"blind_r2": 0.99})
    reg.save_model("r1", "S1", "crmp", {"f": [[0.6]]}, {"blind_r2": 0.98})  # upsert on (run, sector, variant)
    row = reg.get("r1")
    assert row is not None and row["models"][0]["params"]["f"] == [[0.6]] and row["seed"] == 7
    found = reg.find(h1, "cfg", "abc")
    assert found is not None and found["id"] == "r1"
    reg.save_bundle("r1", {"a": 1})
    assert reg.bundle("r1") == {"a": 1}
    audit = AuditLog(st)
    seq = audit.record("eng", "engineer", "run", "run", "r1", h1, "cfg", note="x")
    seq2 = audit.record("eng", "engineer", "run", "run", "r1", h1, "cfg", note="y")
    assert isinstance(seq, int) and seq2 == seq + 1
    rows = audit.entries(object_id="r1")
    assert [r["detail"]["note"] for r in rows] == ["y", "x"]


def test_api_flow_on_postgres(pg_url: str, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from waterflood_app.api.app import create_app
    from waterflood_app.validation import synthetic_suite as suite

    os.environ["WFO_ADMIN_PASSWORD"] = "admin-pass-123"
    app = create_app(tmp_path / "store", sync_jobs=True, db_url=pg_url)
    c = TestClient(app)
    tok = c.post("/auth/login", json={"username": "admin", "password": "admin-pass-123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert (
        c.post(
            "/users", json={"username": "e@a", "password": "pw", "roles": ["engineer"], "assets": ["A"]}, headers=h
        ).status_code
        == 201
    )
    assert (
        c.post("/users", json={"username": "e@a", "password": "pw", "roles": ["engineer"]}, headers=h).status_code
        == 409
    )
    pid = c.post("/projects", json={"name": "pg", "asset": "A", "unit_system": "field"}, headers=h).json()["id"]
    cid = c.post(
        "/connections",
        json={"project_id": pid, "kind": "files", "database": str(suite.case_dir("streak_5x4"))},
        headers=h,
    ).json()["id"]
    sug = c.get("/mapping/suggest", params={"connection_id": cid}, headers=h).json()
    ds = {
        k: {"table": v["table"], "columns": v["columns"], "units": v["units"]}
        for k, v in sug["datasets"].items()
        if v["table"]
    }
    assert (
        c.post("/mapping", json={"project_id": pid, "connection_id": cid, "datasets": ds}, headers=h).status_code == 200
    )
    job = c.post("/runs", json={"project_id": pid, "variants": ["crmp"]}, headers=h).json()
    st = c.get(f"/runs/jobs/{job['id']}", headers=h).json()
    assert st["status"] == "done", st
    run_id = st["result_id"]
    assert c.get(f"/runs/{run_id}/details", headers=h).json()["confidence"] == "HIGH"
    rid = c.post("/recommendations", json={"run_id": run_id}, headers=h).json()["id"]
    assert (
        c.post(f"/recommendations/{rid}/review", json={}, headers={**h, "X-Acting-Role": "reviewer"}).status_code == 200
    )
    assert (
        c.post(f"/recommendations/{rid}/approve", json={}, headers={**h, "X-Acting-Role": "approver"}).status_code
        == 200
    )
    rec = c.get(f"/recommendations/{rid}", headers=h).json()
    assert rec["state"] == "APPROVED" and rec["snapshot"]
    assert c.get(f"/runs/{run_id}/export.json", headers=h).status_code == 200
    ready = c.get("/health/ready").json()
    assert ready["ok"] and ready["checks"]["store"]["kind"] == "postgresql"
    audit = c.get("/admin/audit", params={"object_id": rid}, headers=h).json()
    assert [a["action"] for a in audit][:2] == ["approve", "review"]
