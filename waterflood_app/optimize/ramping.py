"""Ramping — architecture §14 ("what to change this week").

* ramp rate ≤ 15 % of the current rate per week per injector at HIGH confidence, ≤ 8 % at
  MEDIUM/LOW (configurable per facility);
* changes below 5 % or below meter resolution are suppressed to avoid churn.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config

FArray = npt.NDArray[np.float64]


@dataclass
class RampPlan:
    current: FArray
    target: FArray
    weekly: list[FArray]  # rates after week 1, 2, ... until every injector reaches its target
    suppressed: npt.NDArray[np.bool_]  # changes too small to act on
    step_frac: float

    @property
    def weeks_to_target(self) -> int:
        return len(self.weekly)

    @property
    def first_week(self) -> FArray:
        return self.weekly[0] if self.weekly else self.current.copy()


def ramp(
    current: FArray,
    target: FArray,
    confidence: str,
    cfg: Config,
    facility_step_frac: dict[int, float] | None = None,
    meter_resolution: float | None = None,
) -> RampPlan:
    r = cfg.section("ramping")
    step = float(r["step_frac_per_week"].get(confidence, r["step_frac_per_week"]["LOW"]))
    min_frac = float(r["min_change_frac"])
    res = float(r["meter_resolution_bbl_d"]) if meter_resolution is None else meter_resolution
    cur = np.asarray(current, dtype=np.float64)
    tgt = np.asarray(target, dtype=np.float64).copy()
    delta = tgt - cur
    small = (np.abs(delta) < min_frac * np.maximum(cur, 1e-9)) | (np.abs(delta) < res)
    tgt[small] = cur[small]
    weekly: list[FArray] = []
    x = cur.copy()
    for _ in range(520):  # hard cap: ten years of weeks
        if np.allclose(x, tgt, atol=1e-9):
            break
        nxt = x.copy()
        for k in range(len(x)):
            frac = step if facility_step_frac is None else facility_step_frac.get(k, step)
            # a well starting from zero ramps against the target instead of 0 × step
            cap = frac * max(x[k], min_frac * max(tgt[k], 1e-9))
            move = np.clip(tgt[k] - x[k], -cap, cap)
            nxt[k] = x[k] + move
        weekly.append(nxt)
        x = nxt
    return RampPlan(current=cur, target=tgt, weekly=weekly, suppressed=small, step_frac=step)
