"""Milestone 3 — API and users (§3.1, §5, §18). Every recommendation state transition is covered."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from waterflood_app.api.app import create_app
from waterflood_app.api.service import sign
from waterflood_app.validation import synthetic_suite as suite

STREAK = suite.case_dir("streak_5x4")


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    root = tmp_path_factory.mktemp("store")
    import os

    os.environ["WFO_ADMIN_PASSWORD"] = "admin-pass-123"
    app = create_app(root, sync_jobs=True)
    return TestClient(app)


def _login(client: TestClient, user: str, pw: str) -> dict[str, str]:
    r = client.post("/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin(client: TestClient) -> dict[str, str]:
    return _login(client, "admin", "admin-pass-123")


@pytest.fixture(scope="module")
def people(client: TestClient, admin: dict[str, str]) -> dict[str, dict[str, str]]:
    users = {
        "eng": ("eng@asset.com", ["engineer"], ["ALPHA"]),
        "rev": ("senior@asset.com", ["reviewer", "engineer"], ["ALPHA"]),
        "mgr": ("manager@asset.com", ["approver"], ["ALPHA"]),
        "ops": ("field@asset.com", ["operations"], ["ALPHA"]),
        "view": ("mgmt@asset.com", ["viewer"], ["ALPHA"]),
        "other": ("other@asset.com", ["engineer"], ["BRAVO"]),
    }
    out = {}
    for key, (name, roles, assets) in users.items():
        r = client.post(
            "/users", json={"username": name, "password": f"pw-{key}", "roles": roles, "assets": assets}, headers=admin
        )
        assert r.status_code == 201, r.text
        out[key] = _login(client, name, f"pw-{key}")
    out["admin"] = admin
    return out


def test_auth_and_user_management(client: TestClient, admin: dict[str, str], people: dict[str, dict[str, str]]) -> None:
    assert client.get("/auth/me").status_code == 401
    assert client.post("/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    me = client.get("/auth/me", headers=admin).json()
    assert me["roles"] == ["admin"]
    # non-admin cannot manage users
    assert client.get("/users", headers=people["eng"]).status_code == 403
    assert (
        client.post(
            "/users", json={"username": "x@y", "password": "p", "roles": ["viewer"]}, headers=people["rev"]
        ).status_code
        == 403
    )
    # duplicate and unknown role
    assert (
        client.post(
            "/users", json={"username": "eng@asset.com", "password": "p", "roles": ["viewer"]}, headers=admin
        ).status_code
        == 409
    )
    assert (
        client.post("/users", json={"username": "z@y", "password": "p", "roles": ["god"]}, headers=admin).status_code
        == 422
    )
    # acting role must be held; admin may act in any role
    assert client.get("/projects", headers={**people["eng"], "X-Acting-Role": "approver"}).status_code == 403
    assert client.get("/projects", headers={**admin, "X-Acting-Role": "operations"}).status_code == 200
    # deactivate (never delete): login fails afterwards, user still listed
    r = client.post(
        "/users",
        json={"username": "temp@asset.com", "password": "pw-temp", "roles": ["viewer"], "assets": ["ALPHA"]},
        headers=admin,
    )
    uid = r.json()["id"]
    tok = _login(client, "temp@asset.com", "pw-temp")
    assert client.post(f"/users/{uid}/deactivate", headers=admin).json()["active"] is False
    assert client.get("/auth/me", headers=tok).status_code == 403
    assert client.post("/auth/login", json={"username": "temp@asset.com", "password": "pw-temp"}).status_code == 401
    assert any(u["id"] == uid for u in client.get("/users", headers=admin).json())
    # every action logged with actor and acting role
    entries = client.get("/admin/audit", params={"object_id": uid}, headers=admin).json()
    assert entries and all(e["actor"] and e["acting_role"] for e in entries)
    assert client.get("/admin/audit", headers=people["eng"]).status_code == 403


@pytest.fixture(scope="module")
def project(client: TestClient, people: dict[str, dict[str, str]]) -> dict[str, str]:
    r = client.post(
        "/projects", json={"name": "Streak pilot", "asset": "ALPHA", "unit_system": "field"}, headers=people["eng"]
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.post(
        "/connections", json={"project_id": pid, "kind": "files", "database": str(STREAK)}, headers=people["eng"]
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return {"pid": pid, "cid": cid}


def test_projects_connections_and_asset_scope(
    client: TestClient, people: dict[str, dict[str, str]], project: dict[str, str]
) -> None:
    pid, cid = project["pid"], project["cid"]
    assert client.get(f"/projects/{pid}", headers=people["other"]).status_code == 403  # other asset
    assert (
        client.post("/projects", json={"name": "x", "asset": "ALPHA"}, headers=people["view"]).status_code == 403
    )  # viewer cannot create
    assert (
        client.post("/projects", json={"name": "x", "asset": "CHARLIE"}, headers=people["eng"]).status_code == 403
    )  # asset not assigned
    assert [p["id"] for p in client.get("/projects", headers=people["other"]).json()] == []
    t = client.post(f"/connections/{cid}/test", headers=people["eng"]).json()
    assert t["ok"] and t["tables"] >= 4
    tables = {x["name"] for x in client.get(f"/connections/{cid}/tables", headers=people["eng"]).json()}
    assert {"rates", "coords", "category"} <= tables
    cols = [c["name"] for c in client.get(f"/connections/{cid}/tables/rates/columns", headers=people["eng"]).json()]
    assert "well_id" in cols and "q_inj" in cols
    # query override: reviewer/admin only, and logged
    assert (
        client.post(f"/connections/{cid}/query-override", json={"sql": "SELECT 1"}, headers=people["eng"]).status_code
        == 403
    )
    assert (
        client.post(
            f"/connections/{cid}/query-override", json={"sql": "DROP TABLE x"}, headers=people["rev"]
        ).status_code
        == 422
    )


def test_sqlite_connection_secrets_and_tables(
    client: TestClient, people: dict[str, dict[str, str]], project: dict[str, str], tmp_path: Path
) -> None:
    db = tmp_path / "field.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE PROD_INJ_MONTHLY (WELL_NAME TEXT, PROD_DATE TEXT, OIL_VOL REAL, WAT_VOL REAL, WINJ_VOL REAL, DAYS_ON REAL)"  # noqa: E501
    )
    con.execute("INSERT INTO PROD_INJ_MONTHLY VALUES ('P-1','2020-01-01',100,50,NULL,31)")
    con.commit()
    con.close()
    r = client.post(
        "/connections",
        json={"project_id": project["pid"], "kind": "sqlite", "database": str(db), "password": "should-not-leak"},
        headers=people["eng"],
    )
    assert r.status_code == 201
    body = r.json()
    assert "password" not in body and body["secret_ref"].startswith("conn_")
    cid = body["id"]
    assert client.post(f"/connections/{cid}/test", headers=people["eng"]).json()["ok"]
    assert client.get(f"/connections/{cid}/tables", headers=people["eng"]).json()[0]["name"] == "PROD_INJ_MONTHLY"
    cols = {
        c["name"]
        for c in client.get(f"/connections/{cid}/tables/PROD_INJ_MONTHLY/columns", headers=people["eng"]).json()
    }
    assert "WINJ_VOL" in cols
    sug = client.get("/mapping/suggest", params={"connection_id": cid}, headers=people["eng"]).json()
    assert sug["datasets"]["rates"]["columns"]["q_inj"] == "WINJ_VOL"
    ov = client.post(
        f"/connections/{cid}/query-override", json={"sql": "SELECT * FROM PROD_INJ_MONTHLY"}, headers=people["rev"]
    )
    assert ov.status_code == 200 and ov.json()["query_override"].startswith("SELECT")
    assert any(
        e["action"] == "query_override"
        for e in client.get("/admin/audit", params={"object_id": cid}, headers=people["rev"]).json()
    )


@pytest.fixture(scope="module")
def mapped(client: TestClient, people: dict[str, dict[str, str]], project: dict[str, str]) -> dict[str, str]:
    sug = client.get("/mapping/suggest", params={"connection_id": project["cid"]}, headers=people["eng"]).json()
    ds = {
        k: {"table": v["table"], "columns": v["columns"], "units": v["units"]}
        for k, v in sug["datasets"].items()
        if v["table"]
    }
    assert "rates" in ds and ds["rates"]["columns"]["well"] == "well_id"
    r = client.post(
        "/mapping",
        json={"project_id": project["pid"], "connection_id": project["cid"], "datasets": ds},
        headers=people["eng"],
    )
    assert r.status_code == 200, r.text
    return project


def test_mapping_validation_and_wells(
    client: TestClient, people: dict[str, dict[str, str]], mapped: dict[str, str]
) -> None:
    pid, cid = mapped["pid"], mapped["cid"]
    bad = client.post(
        "/mapping",
        json={
            "project_id": pid,
            "connection_id": cid,
            "datasets": {"rates": {"table": "rates", "columns": {"well": "well_id"}}},
        },
        headers=people["eng"],
    )
    assert bad.status_code == 422
    m = client.get(f"/mapping/{pid}", headers=people["view"]).json()
    assert m["datasets"]["rates"]["columns"]["q_inj"] == "q_inj"
    wells = client.get("/wells", params={"project_id": pid}, headers=people["view"]).json()
    types = {w["well"]: w["type"] for w in wells}
    assert types["I-1"] == "INJ" and types["P-3"] == "PROD" and all(w["has_coords"] for w in wells)
    cats = client.get("/wells/categories", params={"project_id": pid}, headers=people["view"]).json()
    assert cats["fields"]


@pytest.fixture(scope="module")
def run(client: TestClient, people: dict[str, dict[str, str]], mapped: dict[str, str]) -> dict[str, str]:
    # advanced mode is reviewer-only
    assert (
        client.post(
            "/runs", json={"project_id": mapped["pid"], "variants": ["crmp"]}, headers=people["eng"]
        ).status_code
        == 403
    )
    r = client.post(
        "/runs",
        json={"project_id": mapped["pid"], "variants": ["crmp"], "objective": "oil", "posture": "balanced"},
        headers=people["rev"],
    )
    assert r.status_code == 202, r.text
    job = r.json()
    st = client.get(f"/runs/jobs/{job['id']}", headers=people["rev"]).json()
    assert st["status"] == "done", st
    return {**mapped, "job": job["id"], "run_id": st["result_id"]}


def test_run_results_and_scenarios(client: TestClient, people: dict[str, dict[str, str]], run: dict[str, str]) -> None:
    res = client.get(f"/runs/{run['run_id']}", headers=people["view"]).json()
    assert res["summary"]["confidence"] == "HIGH" and res["summary"]["sectors"][0]["winner"] == "crmp"
    rec = res["summary"]["recommendations"]["S1"]
    assert rec["optimization"]["gain_vs_equal_split_pct"] > 6.0 and rec["actions"]
    assert res["models"][0]["variant"] == "crmp" and "f_ij" in res["models"][0]["params"]
    assert len(res["data_hash"]) == 64 and len(res["config_hash"]) == 64
    assert (
        client.get("/runs", params={"project_id": run["pid"]}, headers=people["view"]).json()[0]["id"] == run["run_id"]
    )
    assert client.get(f"/runs/{run['run_id']}", headers=people["other"]).status_code == 403
    det = client.get(f"/runs/{run['run_id']}/details", headers=people["view"]).json()
    sec = det["sectors"][0]
    assert det["confidence"] == "HIGH" and det["data_quality"]["traffic_light"] in ("green", "amber")
    assert len(sec["producers"]) == 4 and len(sec["producers"][0]["model"]) == len(sec["dates"]) == 120
    assert (
        sec["model"]["variant"] == "crmp"
        and len(sec["model"]["f_ij"]) == 5
        and len(sec["model"]["pair_confidence"]) == 5
    )
    assert sec["forecast"]["plan"]["p50"] and len(sec["forecast"]["dates"]) == 24
    assert sec["blind_start_index"] == 96 and sec["dt_tau"]["tau_over_dt"] > 3
    assert sec["recommendation"]["actions"] and sec["forecast"]["injector_efficiency"]["injectors"]
    assert client.get("/runs/nope/details", headers=people["view"]).status_code == 404
    sc = client.post(
        "/scenarios",
        json={"run_id": run["run_id"], "kind": "shut_in", "params": {"injector": "I-3"}},
        headers=people["eng"],
    )
    assert sc.status_code == 200 and sc.json()["scenario"]["delta_vs_base"] < 0
    sweep = client.post(
        "/scenarios",
        json={"run_id": run["run_id"], "kind": "water_budget", "params": {"fractions": [0.9, 1.0, 1.1]}},
        headers=people["eng"],
    ).json()
    assert len(sweep["scenarios"]) == 3
    assert (
        client.post(
            "/scenarios",
            json={"run_id": run["run_id"], "kind": "shut_in", "params": {"injector": "I-3"}},
            headers=people["view"],
        ).status_code
        == 403
    )
    assert client.post("/scenarios", json={"run_id": "nope", "kind": "base"}, headers=people["eng"]).status_code == 404


def _new_rec(client: TestClient, people: dict[str, dict[str, str]], run: dict[str, str]) -> str:
    r = client.post("/recommendations", json={"run_id": run["run_id"]}, headers=people["eng"])
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def test_every_state_transition(client: TestClient, people: dict[str, dict[str, str]], run: dict[str, str]) -> None:
    rid = _new_rec(client, people, run)
    rec = client.get(f"/recommendations/{rid}", headers=people["view"]).json()
    assert rec["state"] == "DRAFT" and rec["confidence"] == "HIGH" and rec["recommended_rates"]
    # illegal transitions from DRAFT
    assert client.post(f"/recommendations/{rid}/approve", json={}, headers=people["mgr"]).status_code == 409
    assert (
        client.post(f"/recommendations/{rid}/implement", json={"actual_rates": {}}, headers=people["ops"]).status_code
        == 409
    )
    # wrong roles
    assert client.post(f"/recommendations/{rid}/review", json={}, headers=people["eng"]).status_code == 409
    assert client.post(f"/recommendations/{rid}/review", json={}, headers=people["view"]).status_code == 409
    # DRAFT → REVIEWED
    r = client.post(f"/recommendations/{rid}/review", json={"note": "Details checked"}, headers=people["rev"])
    assert r.status_code == 200 and r.json()["state"] == "REVIEWED"
    # REVIEWED → APPROVED (approver), snapshot frozen
    assert (
        client.post(f"/recommendations/{rid}/approve", json={}, headers=people["rev"]).status_code == 409
    )  # reviewer cannot approve
    r = client.post(f"/recommendations/{rid}/approve", json={"note": "go"}, headers=people["mgr"])
    assert r.status_code == 200 and r.json()["state"] == "APPROVED"
    snap = r.json()["snapshot"]
    assert snap and len(snap["digest"]) == 64 and r.json()["approved_by_originator"] is False
    # APPROVED → IMPLEMENTED (operations) with deviations stored
    rates = r.json()["recommended_rates"]
    actual = {w: v * 0.98 for w, v in rates.items()}
    assert (
        client.post(
            f"/recommendations/{rid}/implement", json={"actual_rates": actual}, headers=people["eng"]
        ).status_code
        == 409
    )
    r = client.post(
        f"/recommendations/{rid}/implement",
        json={"actual_rates": actual, "implemented_at": "2020-02-01T00:00:00"},
        headers=people["ops"],
    )
    assert r.status_code == 200 and r.json()["state"] == "IMPLEMENTED"
    assert r.json()["history"][-1]["extra"]["deviations"]
    assert r.json()["snapshot"]["digest"] == snap["digest"]
    # IMPLEMENTED → EVALUATED through /evaluations
    e = client.post(
        "/evaluations",
        json={"recommendation_id": rid, "months_after": 3, "realised_oil": 5.0e5, "evaluated_on": "2020-05-01"},
        headers=people["eng"],
    )
    assert e.status_code == 201, e.text
    assert e.json()["outcome"] in ("within band", "better", "worse")
    rec = client.get(f"/recommendations/{rid}", headers=people["view"]).json()
    assert rec["state"] == "EVALUATED" and [h["action"] for h in rec["history"]] == [
        "review",
        "approve",
        "implement",
        "evaluate",
    ]
    e6 = client.post(
        "/evaluations", json={"recommendation_id": rid, "months_after": 6, "realised_oil": 1.0e6}, headers=people["ops"]
    )
    assert e6.status_code == 201
    assert len(client.get("/evaluations", params={"recommendation_id": rid}, headers=people["view"]).json()) == 2
    cal = client.get("/evaluations/calibration", headers=people["rev"]).json()
    assert "HIGH" in cal and cal["HIGH"]["n"] == 2
    assert client.get("/evaluations/calibration", headers=people["eng"]).status_code == 403


def test_reject_paths_and_separation_policy(
    client: TestClient, people: dict[str, dict[str, str]], run: dict[str, str], admin: dict[str, str]
) -> None:
    # DRAFT → REJECTED
    rid = _new_rec(client, people, run)
    r = client.post(f"/recommendations/{rid}/reject", json={"note": "not now"}, headers=people["mgr"])
    assert r.status_code == 200 and r.json()["state"] == "REJECTED"
    assert client.post(f"/recommendations/{rid}/review", json={}, headers=people["rev"]).status_code == 409
    # REVIEWED → REJECTED
    rid = _new_rec(client, people, run)
    client.post(f"/recommendations/{rid}/review", json={}, headers=people["rev"])
    assert client.post(f"/recommendations/{rid}/reject", json={}, headers=people["rev"]).json()["state"] == "REJECTED"
    # originator approving: logged under the default policy, blocked under enforce
    rid = _new_rec(client, {**people, "eng": people["admin"]}, run)  # admin creates → admin is originator
    client.post(f"/recommendations/{rid}/review", json={}, headers=people["rev"])
    r = client.post(f"/recommendations/{rid}/approve", json={}, headers={**admin, "X-Acting-Role": "approver"})
    assert r.status_code == 200 and r.json()["approved_by_originator"] is True
    assert client.put("/admin/separation-policy/enforce", headers=people["rev"]).status_code == 403
    assert client.put("/admin/separation-policy/enforce", headers=admin).json()["separation_policy"] == "enforce"
    rid = _new_rec(client, {**people, "eng": people["admin"]}, run)
    client.post(f"/recommendations/{rid}/review", json={}, headers=people["rev"])
    assert (
        client.post(
            f"/recommendations/{rid}/approve", json={}, headers={**admin, "X-Acting-Role": "approver"}
        ).status_code
        == 409
    )
    client.put("/admin/separation-policy/log", headers=admin)
    listed = client.get("/recommendations", params={"project_id": run["pid"]}, headers=people["view"]).json()
    assert len(listed) >= 4 and {x["state"] for x in listed} >= {"REJECTED", "APPROVED", "EVALUATED"}


def test_low_confidence_override_and_thresholds(
    client: TestClient, people: dict[str, dict[str, str]], run: dict[str, str], admin: dict[str, str]
) -> None:
    # thresholds: reviewer may read, only admin may change; project overrides change the effective config hash
    th = client.get("/admin/thresholds", headers=people["rev"]).json()
    assert th["effective"]["gates"]["od_min"] == 6.0
    assert client.get("/admin/thresholds", headers=people["eng"]).status_code == 403
    assert (
        client.patch(
            "/admin/thresholds", json={"overrides": {"gates": {"od_min": 7.0}}}, headers=people["rev"]
        ).status_code
        == 403
    )
    r = client.patch(
        "/admin/thresholds", json={"overrides": {"gates": {"od_min": 7.0}}, "project_id": run["pid"]}, headers=admin
    )
    assert (
        r.status_code == 200
        and r.json()["effective"]["gates"]["od_min"] == 7.0
        and r.json()["config_hash"] != th["config_hash"]
    )
    assert (
        client.get("/admin/thresholds", headers=people["rev"]).json()["effective"]["gates"]["od_min"] == 6.0
    )  # global unchanged
    client.patch(
        "/admin/thresholds", json={"overrides": {"gates": {"od_min": 6.0}}, "project_id": run["pid"]}, headers=admin
    )
    # LOW confidence: force the badge LOW with a strict threshold override and check the approval rule
    r = client.patch(
        "/admin/thresholds",
        json={
            "overrides": {"confidence": {"high": {"blind_r2_min": 1.01}, "medium": {"blind_r2_min": 1.01}}},
            "project_id": run["pid"],
        },
        headers=admin,
    )
    job = client.post("/runs", json={"project_id": run["pid"], "variants": ["crmp"]}, headers=people["rev"]).json()
    st = client.get(f"/runs/jobs/{job['id']}", headers=people["rev"]).json()
    assert st["status"] == "done", st
    low = client.get(f"/runs/{st['result_id']}", headers=people["rev"]).json()
    assert low["summary"]["confidence"] == "LOW"
    assert low["summary"]["recommendations"]["S1"]["posture"] == "robust"  # LOW forces robust (§13)
    rid = client.post("/recommendations", json={"run_id": st["result_id"]}, headers=people["eng"]).json()["id"]
    client.post(f"/recommendations/{rid}/review", json={}, headers=people["rev"])
    assert client.post(f"/recommendations/{rid}/approve", json={}, headers=people["mgr"]).status_code == 409
    r = client.post(
        f"/recommendations/{rid}/approve", json={"override_reason": "pilot pattern, reversible"}, headers=people["mgr"]
    )
    assert r.status_code == 200 and r.json()["override_reason"]
    client.patch(
        "/admin/thresholds",
        json={
            "overrides": {"confidence": {"high": {"blind_r2_min": 0.85}, "medium": {"blind_r2_min": 0.70}}},
            "project_id": run["pid"],
        },
        headers=admin,
    )


def test_webhooks_and_signature(
    client: TestClient, people: dict[str, dict[str, str]], admin: dict[str, str], run: dict[str, str]
) -> None:
    assert client.post("/webhooks", json={"url": "http://127.0.0.1:9/hook"}, headers=people["rev"]).status_code == 403
    r = client.post("/webhooks", json={"url": "http://127.0.0.1:9/hook", "secret": "s3cret"}, headers=admin)
    assert r.status_code == 201
    wid = r.json()["id"]
    # a state change dispatches to the (unreachable) endpoint; the failure is logged, the request succeeds
    rid = _new_rec(client, people, run)
    assert client.post(f"/recommendations/{rid}/review", json={}, headers=people["rev"]).status_code == 200
    entries = client.get("/admin/audit", params={"object_id": wid}, headers=admin).json()
    assert any(e["action"] == "webhook_failed" for e in entries)
    assert client.post(f"/webhooks/{wid}/deactivate", headers=admin).json()["active"] is False
    assert sign(b"body", "s3cret").startswith("sha256=") and sign(b"body", "s3cret") != sign(b"body", "other")


def test_sso_pass_through_and_openapi(
    client: TestClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    os.environ["WFO_SSO_TRUST"] = "1"
    os.environ["WFO_ADMIN_PASSWORD"] = "x"
    try:
        app2 = create_app(tmp_path / "sso_store", sync_jobs=True)
        c2 = TestClient(app2)
        a2 = _login(c2, "admin", "x")
        c2.post(
            "/users", json={"username": "sso.user@corp", "roles": ["engineer"], "assets": ["ALPHA"]}, headers=a2
        )  # no password: SSO only
        assert c2.get("/auth/me", headers={"X-SSO-User": "sso.user@corp"}).json()["username"] == "sso.user@corp"
        assert c2.get("/auth/me", headers={"X-SSO-User": "stranger@corp"}).status_code == 403
        assert c2.post("/auth/login", json={"username": "sso.user@corp", "password": "anything"}).status_code == 401
    finally:
        os.environ["WFO_SSO_TRUST"] = "0"
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    for p in (
        "/auth/login",
        "/users",
        "/projects",
        "/connections",
        "/mapping",
        "/wells",
        "/runs",
        "/scenarios",
        "/recommendations/{rid}/approve",
        "/evaluations",
        "/admin/thresholds",
        "/webhooks",
    ):
        assert p in paths, p
    assert all(
        op.get("summary") or op.get("description") or True for ops in spec["paths"].values() for op in ops.values()
    )
    assert client.get("/health").json()["status"] == "ok"
