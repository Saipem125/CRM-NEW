"""Development API server: local store, fixed bootstrap admin password, async jobs.

    python scripts/dev_api.py          # http://127.0.0.1:8000  (OpenAPI at /docs)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("WFO_STORE", str(Path(__file__).resolve().parents[1] / "wfo_store_dev"))
os.environ.setdefault("WFO_ADMIN_PASSWORD", "admin-pass-123")

if __name__ == "__main__":
    import uvicorn

    from waterflood_app.api.app import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("WFO_PORT", "8000")), log_level="warning")
