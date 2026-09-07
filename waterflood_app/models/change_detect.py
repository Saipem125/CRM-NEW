"""Change alerts — architecture §10 (CUSUM on f_ij(t) and τ(t) relative to the multi-start spread).

Per pair: a two-sided CUSUM on the window series; a shift larger than ``cusum.shift_multiple_of_spread``
× the spread that persists for ``cusum.persist_windows`` windows raises CUSUM_SHIFT
("connectivity between I-3 and P-7 has strengthened since March") with the likely-cause list
(breakthrough, new well, fault, meter). Per producer, forecast-error monitoring is in §15.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config
from waterflood_app.messaging.conditions import ConditionCode, ConditionLog
from waterflood_app.models.rolling import RollingResult

FArray = npt.NDArray[np.float64]
LIKELY_CAUSES = [
    "water breakthrough along the pair",
    "a new or converted well nearby",
    "a fault or baffle becoming active",
    "a meter or allocation change",
]


@dataclass
class Shift:
    kind: str  # "f" | "tau"
    injector: str | None
    producer: str
    direction: str  # "strengthened" | "weakened" | "slowed" | "quickened"
    since: date
    magnitude: float  # relative change vs the reference level
    likely_causes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "injector": self.injector,
            "producer": self.producer,
            "direction": self.direction,
            "since": self.since.isoformat(),
            "magnitude": self.magnitude,
            "likely_causes": self.likely_causes,
        }


def cusum(series: FArray, scale: float, k: float = 0.5) -> tuple[FArray, FArray]:
    """Two-sided CUSUM (in units of ``scale``) against the series' first value as reference."""
    x = (series - series[0]) / max(scale, 1e-12)
    up = np.zeros(len(x))
    dn = np.zeros(len(x))
    for t in range(1, len(x)):
        up[t] = max(0.0, up[t - 1] + x[t] - k)
        dn[t] = max(0.0, dn[t - 1] - x[t] - k)
    return up, dn


def detect_shifts(rolling: RollingResult, cfg: Config, log: ConditionLog | None = None) -> list[Shift]:
    c = cfg.section("cusum")
    mult, persist, floor = float(c["shift_multiple_of_spread"]), int(c["persist_windows"]), float(c["min_spread_rel"])
    shifts: list[Shift] = []
    if len(rolling.windows) < persist + 1:
        return shifts
    spread_f = max(float(np.median([w.f_spread for w in rolling.windows])), floor)
    spread_tau = max(float(np.median([w.tau_spread for w in rolling.windows])), floor)
    dates = [w.mid_date for w in rolling.windows]
    for i in rolling.injectors:
        for j in rolling.producers:
            _, f = rolling.f_series(i, j)
            # pairs that are essentially unconnected in most windows cannot "shift" (field data: many
            # zero-allocation pairs whose fitted f flickers around the floor produced a flood of alerts)
            if float(np.max(f)) < 0.05 or float(np.mean(f > 0.02)) < 0.5:
                continue
            ref = max(float(f[0]), 0.02)
            up, dn = cusum(f / ref, spread_f)
            for sign, arr in (("strengthened", up), ("weakened", dn)):
                hit = arr > mult
                magnitude = float(f[-1] / ref - 1.0)
                consistent = magnitude >= 0.25 if sign == "strengthened" else magnitude <= -0.25
                if hit[-persist:].all() and consistent:
                    since = dates[int(np.argmax(hit))]
                    shifts.append(Shift("f", i, j, sign, since, magnitude, LIKELY_CAUSES))
                    if log is not None:
                        log.emit(
                            ConditionCode.CUSUM_SHIFT,
                            scope=f"pair:{i}|{j}",
                            injector=i,
                            producer=j,
                            direction=sign,
                            since=since.strftime("%B %Y"),
                        )
                    break
    for j in rolling.producers:
        _, tau = rolling.tau_series(j)
        ref = max(float(tau[0]), 1e-9)
        up, dn = cusum(tau / ref, spread_tau)
        for sign, arr in (("slowed", up), ("quickened", dn)):
            hit = arr > mult
            magnitude = float(tau[-1] / ref - 1.0)
            consistent = magnitude >= 0.25 if sign == "slowed" else magnitude <= -0.25
            if hit[-persist:].all() and consistent:
                since = dates[int(np.argmax(hit))]
                shifts.append(Shift("tau", None, j, sign, since, magnitude, LIKELY_CAUSES))
                break
    return shifts
