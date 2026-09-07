"""Backup and restore of a Waterflood Optimizer store (architecture §18 retention; prompt M5).

The store is ``store.sqlite`` (projects, runs, recommendations, audit, users …) plus immutable
Parquet snapshots under ``snapshots/<data hash>/`` and, optionally, the secrets file.

    python scripts/backup.py backup  --store ./wfo_store --out ./backups [--retain 14] [--include-secrets]
    python scripts/backup.py restore --archive ./backups/wfo_<stamp>.tar.gz --store ./wfo_store_restored
    python scripts/backup.py verify  --archive ./backups/wfo_<stamp>.tar.gz

* The SQLite file is copied with the online backup API (consistent even while the API is running);
  a PostgreSQL store (``WFO_DB_URL`` / ``--db-url``) is dumped with ``pg_dump --format=custom``.
* Every file is listed in ``manifest.json`` with its SHA-256; ``verify`` re-hashes the archive.
* Snapshots are immutable, so an incremental scheme is unnecessary: each archive is self-contained
  and ``--retain N`` keeps only the newest N archives.
* Secrets (``secrets.json``, ``jwt_secret``) are excluded unless ``--include-secrets`` is given —
  keep them in the deployment's secret store, not in routine backups (§18).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

SECRET_FILES = {"secrets.json", "jwt_secret", ".jwt_secret"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pg_url(db_url: str | None) -> str | None:
    url = db_url or os.environ.get("WFO_DB_URL") or ""
    return url if url.startswith(("postgresql://", "postgres://")) else None


def backup(store: Path, out: Path, retain: int | None, include_secrets: bool, db_url: str | None = None) -> Path:
    store = store.resolve()
    pg = _pg_url(db_url)
    if pg is None and not (store / "store.sqlite").exists():
        raise SystemExit(f"no store.sqlite under {store}")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S.%fZ")  # sortable, unique within a second
    archive = out / f"wfo_{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="wfo_backup_") as td:
        stage = Path(td) / "store"
        stage.mkdir()
        if pg is not None:
            # PostgreSQL metadata: custom-format dump (consistent snapshot) next to the Parquet files
            subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", f"--file={stage / 'store.pgdump'}", pg],
                check=True,
            )
        else:
            # consistent SQLite copy via the online backup API
            src = sqlite3.connect(f"file:{store / 'store.sqlite'}?mode=ro", uri=True)
            dst = sqlite3.connect(stage / "store.sqlite")
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        snaps = store / "snapshots"
        if snaps.is_dir():
            shutil.copytree(snaps, stage / "snapshots")
        for name in sorted(p.name for p in store.iterdir() if p.is_file()):
            if name == "store.sqlite" or name.startswith(".") or name.endswith(("-wal", "-shm", "-journal")):
                continue
            if name in SECRET_FILES and not include_secrets:
                continue
            shutil.copy2(store / name, stage / name)
        files = sorted(p for p in stage.rglob("*") if p.is_file())
        manifest = {
            "application": "Waterflood Optimizer",
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "store": str(store),
            "include_secrets": include_secrets,
            "n_files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "files": {p.relative_to(stage).as_posix(): sha256_file(p) for p in files},
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(stage, arcname="store")
    if retain:
        archives = sorted(out.glob("wfo_*.tar.gz"))
        for old in archives[:-retain]:
            old.unlink()
    print(f"backup written: {archive} ({archive.stat().st_size / 1e6:.1f} MB, {manifest['n_files']} files)")
    return archive


def _read_manifest(archive: Path) -> tuple[tarfile.TarFile, dict]:
    tar = tarfile.open(archive, "r:gz")
    member = tar.extractfile("store/manifest.json")
    if member is None:
        raise SystemExit("archive has no manifest.json")
    return tar, json.load(io.TextIOWrapper(member, encoding="utf-8"))


def verify(archive: Path) -> bool:
    tar, manifest = _read_manifest(archive)
    bad = []
    with tar:
        for rel, digest in manifest["files"].items():
            m = tar.extractfile(f"store/{rel}")
            if m is None:
                bad.append(f"missing {rel}")
                continue
            h = hashlib.sha256()
            while chunk := m.read(1 << 20):
                h.update(chunk)
            if h.hexdigest() != digest:
                bad.append(f"hash mismatch {rel}")
    if bad:
        print("VERIFY FAILED:\n  " + "\n  ".join(bad))
        return False
    print(f"verify ok: {archive.name} — {manifest['n_files']} files, created {manifest['created_at']}")
    return True


def restore(archive: Path, store: Path, force: bool, db_url: str | None = None) -> None:
    if store.exists() and any(store.iterdir()) and not force:
        raise SystemExit(f"{store} is not empty; pass --force to overwrite")
    if not verify(archive):
        raise SystemExit("archive failed verification; not restoring")
    store.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            if not m.name.startswith("store/") or ".." in Path(m.name).parts:
                continue
            m.name = m.name[len("store/") :]
            if m.name:
                tar.extract(m, store, filter="data")
    dump = store / "store.pgdump"
    pg = _pg_url(db_url)
    if dump.exists():
        if pg is None:
            raise SystemExit("archive holds a PostgreSQL dump: pass --db-url (or WFO_DB_URL) of the target database")
        subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", f"--dbname={pg}", str(dump)], check=True)
        print(f"restored PostgreSQL metadata from {dump.name} into {pg.split('@')[-1]}")
        return
    # sanity: the database opens and lists its projects
    n = sqlite3.connect(store / "store.sqlite").execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    print(f"restored to {store}: {n} project(s)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--store", type=Path, default=Path("./wfo_store"))
    b.add_argument("--out", type=Path, default=Path("./backups"))
    b.add_argument("--retain", type=int, default=None, help="keep only the newest N archives")
    b.add_argument("--include-secrets", action="store_true")
    b.add_argument("--db-url", default=None, help="PostgreSQL URL (default: WFO_DB_URL); omit for SQLite stores")
    v = sub.add_parser("verify")
    v.add_argument("--archive", type=Path, required=True)
    r = sub.add_parser("restore")
    r.add_argument("--archive", type=Path, required=True)
    r.add_argument("--store", type=Path, required=True)
    r.add_argument("--force", action="store_true")
    r.add_argument("--db-url", default=None, help="PostgreSQL URL to restore the metadata dump into")
    a = ap.parse_args(argv)
    if a.cmd == "backup":
        backup(a.store, a.out, a.retain, a.include_secrets, a.db_url)
    elif a.cmd == "verify":
        return 0 if verify(a.archive) else 1
    else:
        restore(a.archive, a.store, a.force, a.db_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
