"""Milestone 5 — backup / verify / restore of the store (Parquet snapshots + SQLite)."""

from __future__ import annotations

import json
import sqlite3
import sys
import tarfile
from pathlib import Path

import polars as pl
import pytest

from waterflood_app.store.project import ProjectStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup as B


@pytest.fixture
def store(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    st = ProjectStore.open(root)
    st.create_project("p1", "ALPHA", {"unit_system": "field"})
    st.save_snapshot("p1", {"rates": pl.DataFrame({"well": ["I-1", "P-1"], "q": [1.0, 2.0]})})
    st.save_snapshot("p1", {"rates": pl.DataFrame({"well": ["I-1", "P-1"], "q": [1.5, 2.0]})})
    (root / "secrets.json").write_text('{"db": "hunter2"}', encoding="utf-8")
    return root


def test_backup_verify_restore_roundtrip(store: Path, tmp_path: Path) -> None:
    out = tmp_path / "backups"
    archive = B.backup(store, out, retain=None, include_secrets=False)
    assert archive.exists() and archive.name.startswith("wfo_") and archive.suffix == ".gz"
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "store/store.sqlite" in names and "store/manifest.json" in names
    assert sum(1 for n in names if n.endswith("rates.parquet")) == 2, names
    assert not any(n.endswith("secrets.json") for n in names)  # excluded by default (§18)
    assert B.verify(archive)
    restored = tmp_path / "restored"
    B.restore(archive, restored, force=False)
    assert (restored / "store.sqlite").exists()
    st = ProjectStore.open(restored)
    assert [p["id"] for p in st.list_projects()] == ["p1"]
    assert len(st.snapshots("p1")) == 2
    for snap in st.snapshots("p1"):
        assert st.load_snapshot(snap["data_hash"])["rates"].height == 2
    # refuses to overwrite a non-empty target unless forced
    with pytest.raises(SystemExit):
        B.restore(archive, restored, force=False)
    B.restore(archive, restored, force=True)
    # secrets can be included explicitly
    archive2 = B.backup(store, out, retain=None, include_secrets=True)
    with tarfile.open(archive2) as tar:
        assert "store/secrets.json" in tar.getnames()


def test_backup_is_consistent_while_open_and_retention(store: Path, tmp_path: Path) -> None:
    out = tmp_path / "backups"
    conn = sqlite3.connect(store / "store.sqlite")
    conn.execute("BEGIN")
    conn.execute("INSERT INTO settings VALUES ('k', '1', 'now')")  # uncommitted write in another connection
    archives = [B.backup(store, out, retain=2, include_secrets=False) for _ in range(3)]
    conn.rollback()
    conn.close()
    kept = sorted(out.glob("wfo_*.tar.gz"))
    assert len(kept) == 2 and kept[-1] == archives[-1]
    manifest = json.load(tarfile.open(kept[-1]).extractfile("store/manifest.json"))  # type: ignore[arg-type]
    assert manifest["n_files"] >= 3 and "store.sqlite" in manifest["files"]


def test_verify_detects_corruption(store: Path, tmp_path: Path) -> None:
    archive = B.backup(store, tmp_path / "b", retain=None, include_secrets=False)
    # rebuild the archive with a tampered parquet file
    work = tmp_path / "work"
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter="data")
    victim = next((work / "store" / "snapshots").rglob("rates.parquet"))
    victim.write_bytes(b"garbage")
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(work / "store", arcname="store")
    assert not B.verify(bad)
    with pytest.raises(SystemExit):
        B.restore(bad, tmp_path / "never", force=False)


def test_cli(store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert B.main(["backup", "--store", str(store), "--out", str(tmp_path / "cli")]) == 0
    archive = next((tmp_path / "cli").glob("wfo_*.tar.gz"))
    assert B.main(["verify", "--archive", str(archive)]) == 0
    assert B.main(["restore", "--archive", str(archive), "--store", str(tmp_path / "cli_restored")]) == 0
    assert "restored to" in capsys.readouterr().out
