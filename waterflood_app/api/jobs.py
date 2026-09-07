"""Job runner — architecture §19 ("job queue (Celery/RQ) · scheduler"); §18 offline mode: single-node worker.

Phase-1 implementation: a persistent job table in the project store plus an in-process thread
pool. The same ``JobRunner`` interface (submit / status / result) is what a Celery or RQ backend
would implement later; ``WFO_JOBS_SYNC=1`` runs jobs inline (tests, single-shot CLI).
"""

from __future__ import annotations

import json
import os
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from waterflood_app.store.project import ProjectStore

JobFn = Callable[["JobHandle"], dict[str, Any]]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobHandle:
    """Given to the job function to report progress."""

    def __init__(self, runner: JobRunner, job_id: str) -> None:
        self.runner = runner
        self.id = job_id

    def progress(self, fraction: float, message: str = "") -> None:
        self.runner._update(self.id, progress=fraction, message=message)


class JobRunner:
    """Modes: ``inline`` (WFO_JOBS_SYNC=1 / sync=True), ``pool`` (thread pool in the API process, default)
    and ``external`` (WFO_JOBS_MODE=external: the API only queues rows; ``waterflood_app.api.worker``
    processes claim and run them — the prod profile's worker service)."""

    def __init__(
        self, store: ProjectStore, workers: int = 2, sync: bool | None = None, mode: str | None = None
    ) -> None:
        self.store = store
        self.sync = bool(int(os.environ.get("WFO_JOBS_SYNC", "0"))) if sync is None else sync
        self.mode = "inline" if self.sync else str(mode or os.environ.get("WFO_JOBS_MODE", "pool"))
        self.pool = (
            ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wfo-job") if self.mode == "pool" else None
        )
        self._lock = threading.Lock()
        self._results: dict[str, dict[str, Any]] = {}

    # ---- persistence -----------------------------------------------------------------
    def _insert(self, job_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self.store.conn() as c:
            c.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, kind, "queued", 0.0, "", _now(), _now(), None, None, json.dumps(payload, default=str)),
            )

    def _update(self, job_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self.store.conn() as c:
            c.execute(f"UPDATE jobs SET {cols}, updated_at=? WHERE id=?", (*fields.values(), _now(), job_id))

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.store.conn() as c:
            r = c.execute(
                "SELECT id, kind, status, progress, message, created_at, updated_at, result_id, error, payload_json "
                "FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
        if r is None:
            return None
        return {
            "id": r[0],
            "kind": r[1],
            "status": r[2],
            "progress": float(r[3]),
            "message": r[4],
            "created_at": r[5],
            "updated_at": r[6],
            "result_id": r[7],
            "error": r[8],
            "payload": json.loads(r[9]),
        }

    def list(self, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = "SELECT id FROM jobs" + (" WHERE kind=?" if kind else "") + " ORDER BY created_at DESC LIMIT ?"
        with self.store.conn() as c:
            ids = [r[0] for r in c.execute(q, ((kind, limit) if kind else (limit,))).fetchall()]
        return [j for j in (self.get(i) for i in ids) if j is not None]

    def result(self, job_id: str) -> dict[str, Any] | None:
        return self._results.get(job_id)

    # ---- execution -------------------------------------------------------------------
    def submit(self, kind: str, fn: JobFn, payload: dict[str, Any] | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        self._insert(job_id, kind, payload or {})
        if self.mode == "external":
            return job_id  # a worker process claims it (payload carries everything it needs)
        if self.pool is None:
            self._run(job_id, fn)
        else:
            self.pool.submit(self._run, job_id, fn)
        return job_id

    # ---- external workers ------------------------------------------------------------------
    def claim_next(self, kinds: tuple[str, ...] = ("run",)) -> dict[str, Any] | None:
        """Atomically move the oldest queued job of the given kinds to running; None when the queue is empty."""
        marks = ",".join("?" for _ in kinds)
        with self._lock, self.store.conn() as c:
            row = c.execute(
                f"SELECT id FROM jobs WHERE status='queued' AND kind IN ({marks}) ORDER BY created_at LIMIT 1",
                tuple(kinds),
            ).fetchone()
            if row is None:
                return None
            cur = c.execute(
                "UPDATE jobs SET status='running', message='claimed', updated_at=? WHERE id=? AND status='queued'",
                (_now(), row[0]),
            )
            if getattr(cur, "rowcount", 1) == 0:  # another worker won the race
                return None
        return self.get(str(row[0]))

    def run_claimed(self, job_id: str, fn: JobFn) -> None:
        """Execute a claimed job in this process (used by ``waterflood_app.api.worker``)."""
        self._run(job_id, fn)

    def queued(self) -> int:
        with self.store.conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0])

    def _run(self, job_id: str, fn: JobFn) -> None:
        self._update(job_id, status="running", message="started")
        try:
            out = fn(JobHandle(self, job_id))
            self._results[job_id] = out
            self._update(
                job_id, status="done", progress=1.0, message="finished", result_id=str(out.get("result_id", "")) or None
            )
        except Exception as exc:
            self._update(
                job_id, status="failed", error=f"{type(exc).__name__}: {exc}", message=traceback.format_exc()[-2000:]
            )

    def shutdown(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True)
