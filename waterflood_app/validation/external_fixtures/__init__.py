"""External synthetic fixture set (second, independently generated Tier-1 suite).

Copied verbatim from ``New CRM/synthetic_fixtures`` on 2026-09-03 (see its README): surface
volumes with B_o = 1.25 / B_w = 1.02, ESP-style flowing BHP, an OFM-style column variant and an
Excel workbook variant. It exercises the loader (mapping, units, PVT) and the BHP term, which
the M0 suite does not. Frozen: do not regenerate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from waterflood_app.ingest.connectors import LoadedData, load_project
from waterflood_app.ingest.units import PVT

EXT_DIR = Path(__file__).resolve().parent
CASES = (
    "streak_5x4_clean",
    "streak_5x4_noisy",
    "allocated_noisy",
    "aquifer_9x9",
    "converted_wells",
    "sectored_60",
)


def truth(case: str) -> dict[str, Any]:
    return dict(json.loads((EXT_DIR / case / "truth.json").read_text(encoding="utf-8")))


def pvt_for(case: str) -> PVT:
    p = truth(case)["pvt"]
    return PVT(bo=float(p["Bo"]), bw=float(p["Bw"]))


def load_external(case: str, variant: str = "csv") -> LoadedData:
    """variant: 'csv' (canonical names), 'ofm' (OFM-style names, streak_5x4_clean only),
    'xlsx' (allocated_noisy only)."""
    d = EXT_DIR / case
    if variant == "ofm":
        return load_project(d / "ofm_style")
    if variant == "xlsx":
        wb = next(d.glob("*.xlsx"))
        return load_project(wb)
    return load_project(d)
