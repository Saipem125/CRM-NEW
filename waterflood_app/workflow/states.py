"""Recommendation lifecycle — architecture §3.

DRAFT (engineer runs) → REVIEWED (senior RE checks Details) → APPROVED (asset manager signs)
→ IMPLEMENTED (operations logs actual rates) → EVALUATED (actual vs forecast after 3–6 months).

Rules: the person who runs cannot approve (separation; policy ``log`` records
``approved_by_originator``, ``enforce`` blocks); approving freezes data hash, config hash, model
version and the recommended rates (immutable snapshot) — later edits create a new draft; LOW
confidence cannot be approved without an approver override with a stated reason (§1, §9).
Every action logs actor and acting_role (§3.1).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

ROLES = ("viewer", "engineer", "reviewer", "approver", "operations", "admin")


class State(StrEnum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    IMPLEMENTED = "IMPLEMENTED"
    EVALUATED = "EVALUATED"
    REJECTED = "REJECTED"


TRANSITIONS: dict[tuple[State, str], State] = {
    (State.DRAFT, "review"): State.REVIEWED,
    (State.REVIEWED, "approve"): State.APPROVED,
    (State.REVIEWED, "reject"): State.REJECTED,
    (State.DRAFT, "reject"): State.REJECTED,
    (State.APPROVED, "implement"): State.IMPLEMENTED,
    (State.IMPLEMENTED, "evaluate"): State.EVALUATED,
}
REQUIRED_ROLE: dict[str, tuple[str, ...]] = {
    "review": ("reviewer", "admin"),
    "approve": ("approver", "admin"),
    "reject": ("reviewer", "approver", "admin"),
    "implement": ("operations", "admin"),
    "evaluate": ("engineer", "reviewer", "admin"),
}


class WorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class Actor:
    user: str
    acting_role: str
    roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.acting_role not in ROLES:
            raise WorkflowError(f"unknown role {self.acting_role!r}")
        if self.roles and self.acting_role not in self.roles and "admin" not in self.roles:
            raise WorkflowError(f"{self.user} does not hold the role {self.acting_role!r}")


@dataclass
class Event:
    at: str
    action: str
    actor: str
    acting_role: str
    from_state: str
    to_state: str
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    """What approval freezes (§3 "Immutable snapshot")."""

    data_hash: str
    config_hash: str
    model_version: str
    recommended_rates: dict[str, float]
    confidence: str
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.digest:
            blob = json.dumps(
                {
                    "d": self.data_hash,
                    "c": self.config_hash,
                    "m": self.model_version,
                    "r": self.recommended_rates,
                    "k": self.confidence,
                },
                sort_keys=True,
            )
            self.digest = hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Recommendation:
    id: str
    asset: str
    originator: str
    data_hash: str
    config_hash: str
    model_version: str
    recommended_rates: dict[str, float]
    confidence: str
    state: State = State.DRAFT
    history: list[Event] = field(default_factory=list)
    snapshot: Snapshot | None = None
    implemented_rates: dict[str, float] = field(default_factory=dict)
    implemented_at: str | None = None
    approved_by_originator: bool = False
    override_reason: str | None = None
    advanced_overrides: dict[str, Any] = field(default_factory=dict)  # shown in the approval view (§3)
    evaluations: list[dict[str, Any]] = field(default_factory=list)

    def _log(self, action: str, actor: Actor, to_state: State, note: str = "", **extra: Any) -> None:
        self.history.append(
            Event(
                datetime.now(UTC).isoformat(),
                action,
                actor.user,
                actor.acting_role,
                self.state.value,
                to_state.value,
                note,
                extra,
            )
        )
        self.state = to_state

    def _check(self, action: str, actor: Actor) -> State:
        nxt = TRANSITIONS.get((self.state, action))
        if nxt is None:
            raise WorkflowError(f"cannot {action} from {self.state.value}")
        if actor.acting_role not in REQUIRED_ROLE[action]:
            raise WorkflowError(f"{action} requires one of {REQUIRED_ROLE[action]}, not {actor.acting_role}")
        return nxt

    def review(self, actor: Actor, note: str = "") -> None:
        self._log("review", actor, self._check("review", actor), note)

    def approve(
        self, actor: Actor, separation_policy: str = "log", override_reason: str | None = None, note: str = ""
    ) -> None:
        nxt = self._check("approve", actor)
        if actor.user == self.originator:
            if separation_policy == "enforce":
                raise WorkflowError("the person who runs cannot approve (separation enforced)")
            self.approved_by_originator = True
        if self.confidence == "LOW":
            if not override_reason:
                raise WorkflowError(
                    "LOW confidence: screening only — approval needs an approver override with a reason"
                )
            self.override_reason = override_reason
        self.snapshot = Snapshot(
            self.data_hash, self.config_hash, self.model_version, dict(self.recommended_rates), self.confidence
        )
        self._log(
            "approve",
            actor,
            nxt,
            note,
            snapshot=self.snapshot.digest,
            approved_by_originator=self.approved_by_originator,
            override_reason=override_reason,
        )

    def reject(self, actor: Actor, note: str = "") -> None:
        self._log("reject", actor, self._check("reject", actor), note)

    def implement(self, actor: Actor, actual_rates: dict[str, float], at: str | None = None, note: str = "") -> None:
        """Operations enters what was actually set; deviations from the plan are stored (§3)."""
        nxt = self._check("implement", actor)
        self.implemented_rates = dict(actual_rates)
        self.implemented_at = at or datetime.now(UTC).isoformat()
        deviations = {
            w: actual_rates.get(w, 0.0) - r
            for w, r in self.recommended_rates.items()
            if abs(actual_rates.get(w, 0.0) - r) > 1e-9
        }
        self._log("implement", actor, nxt, note, deviations=deviations)

    def evaluate(self, actor: Actor, record: dict[str, Any], note: str = "") -> None:
        nxt = self._check("evaluate", actor)
        self.evaluations.append(record)
        self._log("evaluate", actor, nxt, note, outcome=record.get("outcome"))

    def new_draft(
        self,
        actor: Actor,
        recommended_rates: dict[str, float],
        confidence: str,
        new_id: str,
        data_hash: str | None = None,
        config_hash: str | None = None,
    ) -> Recommendation:
        """Edits after approval never touch the snapshot: they create a new DRAFT (§3)."""
        return Recommendation(
            new_id,
            self.asset,
            actor.user,
            data_hash or self.data_hash,
            config_hash or self.config_hash,
            self.model_version,
            dict(recommended_rates),
            confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "asset": self.asset,
            "originator": self.originator,
            "state": self.state.value,
            "confidence": self.confidence,
            "data_hash": self.data_hash,
            "config_hash": self.config_hash,
            "model_version": self.model_version,
            "recommended_rates": self.recommended_rates,
            "implemented_rates": self.implemented_rates,
            "implemented_at": self.implemented_at,
            "approved_by_originator": self.approved_by_originator,
            "override_reason": self.override_reason,
            "advanced_overrides": self.advanced_overrides,
            "snapshot": None if self.snapshot is None else self.snapshot.__dict__,
            "history": [e.__dict__ for e in self.history],
            "evaluations": self.evaluations,
        }
