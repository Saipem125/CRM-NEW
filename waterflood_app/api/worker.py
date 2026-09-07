"""Job worker — architecture §19 "job queue" / §18 deployment: a separate process that runs queued jobs.

    WFO_STORE=/data/store [WFO_DB_URL=postgresql://…] python -m waterflood_app.api.worker [--poll 2] [--once]

The API (started with ``WFO_JOBS_MODE=external``) only inserts job rows; workers claim them
atomically from the shared store (SQLite file or PostgreSQL), run the engine + optimizer and write
the registry, bundle and artefacts exactly as the in-process pool would. Several workers may run
against one PostgreSQL store; with SQLite keep one worker per store file.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from waterflood_app.api.jobs import JobHandle
from waterflood_app.api.service import Service

_STOP = False


def _stop(*_: Any) -> None:
    global _STOP
    _STOP = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=os.environ.get("WFO_STORE", "./wfo_store"))
    ap.add_argument("--poll", type=float, default=float(os.environ.get("WFO_WORKER_POLL_S", "2")))
    ap.add_argument("--once", action="store_true", help="process the queue once and exit (tests, cron)")
    a = ap.parse_args(argv)
    svc = Service(Path(a.store), sync_jobs=True)  # the worker runs jobs inline in its own process
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print(f"[wfo-worker] store={svc.store.db.describe()} poll={a.poll}s", flush=True)
    n = 0
    while not _STOP:
        job = svc.jobs.claim_next(("run",))
        if job is None:
            if a.once:
                break
            time.sleep(a.poll)
            continue
        t0 = time.time()
        payload = job["payload"]

        def fn(h: JobHandle, payload: dict[str, Any] = payload) -> dict[str, Any]:
            return svc.run_from_payload(h, payload)

        svc.jobs.run_claimed(str(job["id"]), fn)
        done = svc.jobs.get(str(job["id"])) or {}
        n += 1
        print(f"[wfo-worker] job {job['id']} {done.get('status')} in {time.time() - t0:.1f}s", flush=True)
    print(f"[wfo-worker] exiting after {n} job(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
