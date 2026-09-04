"""Produce the sample report set from the frozen ``streak_5x4`` fixture (deliverables checklist §6).

    python scripts/sample_report.py [--out docs/samples] [--objective npv]

Runs the engine and optimizer on the fixture through the same service path the API uses (temporary
store, synchronous jobs), then writes ``streak_5x4_report.pdf`` / ``.docx`` / ``.html``, the XLSX and
CSV tables and the model JSON. The PDF needs WeasyPrint or a Chromium/Edge browser on the host.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "samples")
    ap.add_argument("--objective", default="npv", choices=["oil", "npv", "min_water"])
    ap.add_argument("--fixture", default="streak_5x4")
    a = ap.parse_args()
    from fastapi.testclient import TestClient

    from waterflood_app.api.app import create_app
    from waterflood_app.validation import synthetic_suite as suite

    os.environ["WFO_ADMIN_PASSWORD"] = "sample-pass"
    a.out.mkdir(parents=True, exist_ok=True)
    td = tempfile.mkdtemp(prefix="wfo_sample_")  # removed best-effort: SQLite handles may linger on Windows
    try:
        c = TestClient(create_app(td, sync_jobs=True))
        tok = c.post("/auth/login", json={"username": "admin", "password": "sample-pass"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        pid = c.post(
            "/projects", json={"name": f"{a.fixture} sample", "asset": "SAMPLE", "unit_system": "field"}, headers=h
        ).json()["id"]
        cid = c.post(
            "/connections",
            json={"project_id": pid, "kind": "files", "database": str(suite.case_dir(a.fixture))},
            headers=h,
        ).json()["id"]
        sug = c.get("/mapping/suggest", params={"connection_id": cid}, headers=h).json()
        ds = {
            k: {"table": v["table"], "columns": v["columns"], "units": v["units"]}
            for k, v in sug["datasets"].items()
            if v["table"]
        }
        c.post("/mapping", json={"project_id": pid, "connection_id": cid, "datasets": ds}, headers=h)
        job = c.post(
            "/runs", json={"project_id": pid, "objective": a.objective, "posture": "balanced"}, headers=h
        ).json()
        st = c.get(f"/runs/jobs/{job['id']}", headers=h).json()
        if st["status"] != "done":
            print("run failed:", st)
            return 1
        run_id = st["result_id"]
        rid = c.post("/recommendations", json={"run_id": run_id}, headers=h).json()["id"]
        c.post(
            f"/recommendations/{rid}/review", json={"note": "sample review"}, headers={**h, "X-Acting-Role": "reviewer"}
        )
        c.post(
            f"/recommendations/{rid}/approve",
            json={"note": "sample approval"},
            headers={**h, "X-Acting-Role": "approver"},
        )
        for path, name in (
            (f"/recommendations/{rid}/report.pdf", f"{a.fixture}_report.pdf"),
            (f"/recommendations/{rid}/report.docx", f"{a.fixture}_report.docx"),
            (f"/recommendations/{rid}/report.html", f"{a.fixture}_report.html"),
            (f"/recommendations/{rid}/export.xlsx", f"{a.fixture}_tables.xlsx"),
            (f"/recommendations/{rid}/export.csv", f"{a.fixture}_tables_csv.zip"),
            (f"/runs/{run_id}/export.json", f"{a.fixture}_model.json"),
        ):
            r = c.get(path, headers=h)
            if r.status_code != 200:
                print(f"{name}: {r.status_code} {r.text[:120]}")
                continue
            (a.out / name).write_bytes(r.content)
            print(f"{name}: {len(r.content) / 1e3:.0f} kB")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
