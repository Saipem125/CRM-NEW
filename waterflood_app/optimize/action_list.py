"""Operational recommendation — architecture §14: the "change this week" list.

Each item: well, from → to, step this week, target date, setting hint, expected oil gain,
revert condition. Ordering: highest marginal-oil changes first; changes on the same facility
grouped. Every change carries a "revert if" condition (water-cut jump, pressure limit) that
the surveillance loop (§15) monitors.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import numpy.typing as npt

from waterflood_app.config import Config
from waterflood_app.optimize.ramping import RampPlan, ramp
from waterflood_app.optimize.solvers import OptimizationResult

FArray = npt.NDArray[np.float64]


@dataclass
class ActionItem:
    well: str
    facility: str | None
    rate_from: float
    rate_to: float
    step_this_week: float
    weeks_to_target: int
    target_date: date
    setting_hint: str
    expected_oil_gain: float
    revert_if: str
    marginal_value: float
    suppressed: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["target_date"] = self.target_date.isoformat()
        return d


def _setting_hint(well: str, rate_to: float, injectivity: dict[str, float] | None, p_res: float | None) -> str:
    """Target rate → choke/pump hint from the last known injectivity curve (§14); confirmed in the field."""
    if injectivity and well in injectivity and injectivity[well] > 0 and p_res is not None:
        p_inj = p_res + rate_to / injectivity[well]
        return (
            f"target {rate_to:.0f}; injection pressure about {p_inj:.0f} at the last injectivity "
            f"({injectivity[well]:.2f} per pressure unit); field to confirm"
        )
    return f"target {rate_to:.0f}; set choke / pump frequency to reach it, field to confirm the setting"


def build_action_list(
    result: OptimizationResult,
    injectors: list[str],
    confidence: str,
    cfg: Config,
    start: date,
    facilities: dict[str, str] | None = None,
    water_cut_now: dict[str, float] | None = None,
    injectivity: dict[str, float] | None = None,
    p_res: float | None = None,
    pressure_limit: float | None = None,
) -> tuple[list[ActionItem], RampPlan]:
    r = cfg.section("ramping")
    cur = result.base.x[0]
    tgt = result.plan.x[0]
    rp = ramp(cur, tgt, confidence, cfg)
    first = rp.first_week
    # weeks each injector needs to reach its target
    weeks = np.zeros(len(injectors), dtype=int)
    for k in range(len(injectors)):
        for wk, x in enumerate(rp.weekly, start=1):
            if abs(x[k] - rp.target[k]) < 1e-9:
                weeks[k] = wk
                break
        else:
            weeks[k] = len(rp.weekly)
    total_gain = max(result.value_plan - result.value_base, 0.0)
    delta = rp.target - cur
    share = np.abs(delta) * np.maximum(result.marginal_value, 0.0) if len(result.marginal_value) else np.abs(delta)
    share = share / share.sum() if share.sum() > 0 else np.zeros_like(share)
    wc_jump = float(r["revert_water_cut_jump"])
    items: list[ActionItem] = []
    for k, w in enumerate(injectors):
        if rp.suppressed[k] or abs(delta[k]) < 1e-9:
            continue
        wc = (water_cut_now or {}).get(w)
        revert = [
            f"water cut of any connected producer rises by more than {wc_jump:.0%}"
            + (f" above its current {wc:.0%}" if wc is not None else "")
        ]
        if pressure_limit is not None:
            revert.append(
                f"injection pressure exceeds {pressure_limit * (1 - float(r['revert_pressure_margin_frac'])):.0f}"
            )
        items.append(
            ActionItem(
                well=w,
                facility=(facilities or {}).get(w),
                rate_from=float(cur[k]),
                rate_to=float(rp.target[k]),
                step_this_week=float(first[k] - cur[k]),
                weeks_to_target=int(weeks[k]),
                target_date=start + timedelta(weeks=int(weeks[k])),
                setting_hint=_setting_hint(w, float(rp.target[k]), injectivity, p_res),
                expected_oil_gain=float(total_gain * share[k]),
                revert_if="; ".join(revert),
                marginal_value=float(result.marginal_value[k]) if len(result.marginal_value) else 0.0,
            )
        )
    # order: highest expected gain first, then group by facility keeping that order for the first member
    items.sort(key=lambda it: -it.expected_oil_gain)
    if facilities:
        order: dict[str | None, int] = {}
        for it in items:
            order.setdefault(it.facility, len(order))
        items.sort(key=lambda it: (order[it.facility], -it.expected_oil_gain))
    return items, rp
