"""Post-M5 — external job worker and persisted run artefacts (architecture §19 job queue, §18 deployment)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from waterflood_app.api import worker
from waterflood_app.api.app import create_app
from waterflood_app.api.service import Service
from waterflood_app.validation import synthetic_suite as suite


@pytest.fixture
def external_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    monkeypatch.setenv("WFO_ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.setenv("WFO_JOBS_MODE", "external")
    monkeypatch.delenv("WFO_JOBS_SYNC", raising=False)
    root = tmp_path / "store"
    return TestClient(create_app(root, sync_jobs=False)), root


def _prepare(client: TestClient) -> tuple[dict[str, str], str]:
    tok = client.post("/auth/login", json={"username": "admin", "password": "admin-pass-123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    pid = client.post("/projects", json={"name": "w", "asset": "A", "unit_system": "field"}, headers=h).json()["id"]
    cid = client.post(
        "/connections",
        json={"project_id": pid, "kind": "files", "database": str(suite.case_dir("streak_5x4"))},
        headers=h,
    ).json()["id"]
    sug = client.get("/mapping/suggest", params={"connection_id": cid}, headers=h).json()
    ds = {
        k: {"table": v["table"], "columns": v["columns"], "units": v["units"]}
        for k, v in sug["datasets"].items()
        if v["table"]
    }
    assert (
        client.post("/mapping", json={"project_id": pid, "connection_id": cid, "datasets": ds}, headers=h).status_code
        == 200
    )
    return h, pid


def test_external_worker_runs_queued_jobs_and_artefacts_persist(external_app: tuple[TestClient, Path]) -> None:
    client, root = external_app
    h, pid = _prepare(client)
    job = client.post("/runs", json={"project_id": pid, "variants": ["crmp"], "objective": "oil"}, headers=h).json()
    st = client.get(f"/runs/jobs/{job['id']}", headers=h).json()
    assert st["status"] == "queued"  # the API only queued it
    ready = client.get("/health/ready").json()
    assert ready["checks"]["jobs"] == {"ok": True, "mode": "external", "queued": 1}
    # a worker process (here: the worker's main loop in-process, --once) claims and runs it
    os.environ.pop("WFO_JOBS_MODE", None)
    assert worker.main(["--store", str(root), "--once"]) == 0
    st = client.get(f"/runs/jobs/{job['id']}", headers=h).json()
    assert st["status"] == "done" and st["result_id"], st
    run_id = st["result_id"]
    # the API process never had the run in memory: results come from the registry, artefacts from disk
    assert (root / "artifacts" / f"{run_id}.joblib").exists()
    det = client.get(f"/runs/{run_id}/details", headers=h)
    assert det.status_code == 200 and det.json()["confidence"] == "HIGH"
    sc = client.post("/scenarios", json={"run_id": run_id, "kind": "shut_in", "params": {"injector": "I-3"}}, headers=h)
    assert sc.status_code == 200, sc.text
    rec = client.post("/recommendations", json={"run_id": run_id}, headers=h)
    assert rec.status_code == 201, rec.text
    # audit shows the submitting user and acting role, not the worker
    audit = client.get("/admin/audit", params={"object_id": run_id}, headers=h).json()
    assert any(a["action"] == "run" and a["actor"] == "admin" for a in audit)
    # a second worker pass finds nothing to do
    assert worker.main(["--store", str(root), "--once"]) == 0
    assert client.get("/health/ready").json()["checks"]["jobs"]["queued"] == 0


def test_claim_is_atomic_and_fresh_service_reloads_artefacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WFO_ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.setenv("WFO_JOBS_MODE", "external")
    monkeypatch.delenv("WFO_JOBS_SYNC", raising=False)
    root = tmp_path / "store"
    client = TestClient(create_app(root, sync_jobs=False))
    h, pid = _prepare(client)
    j1 = client.post("/runs", json={"project_id": pid, "variants": ["crmp"]}, headers=h).json()["id"]
    j2 = client.post("/runs", json={"project_id": pid, "variants": ["crmp"], "seed": 3}, headers=h).json()["id"]
    svc = Service(root, sync_jobs=True)
    first = svc.jobs.claim_next(("run",))
    second = svc.jobs.claim_next(("run",))
    assert first is not None and second is not None and {first["id"], second["id"]} == {j1, j2}
    assert first["status"] == "running" and svc.jobs.claim_next(("run",)) is None
    svc.jobs.run_claimed(first["id"], lambda hd: svc.run_from_payload(hd, first["payload"]))
    run_id = svc.jobs.get(first["id"])["result_id"]  # type: ignore[index]
    fresh = Service(root, sync_jobs=True)  # no in-memory artefacts
    assert run_id not in fresh._artifacts
    from waterflood_app.api.auth import Principal

    admin = fresh.users.get_by_username("admin")
    assert admin is not None
    art = fresh._artifact(Principal(admin, "admin"), run_id)
    assert art.run.confidence == "HIGH" and run_id in fresh._artifacts
