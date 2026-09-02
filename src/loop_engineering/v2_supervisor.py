"""V2のtyped observationからcurrent Workと次transitionを決定する。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

_PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


class EvidenceState(str, Enum):
    NOT_RUN = "NOT_RUN"
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    NOT_REQUIRED = "NOT_REQUIRED"


class V2Transition(str, Enum):
    DESIGN = "DESIGN"
    IMPLEMENT = "IMPLEMENT"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    HUMAN_VERIFY = "HUMAN_VERIFY"
    REPAIR = "REPAIR"
    INTEGRATE = "INTEGRATE"
    COMPLETE_WORK = "COMPLETE_WORK"


class V2SupervisorDisposition(str, Enum):
    CONTINUE = "CONTINUE"
    YIELD_EXTERNAL = "YIELD_EXTERNAL"
    INTERVENTION_REQUIRED = "INTERVENTION_REQUIRED"
    COMPLETE_GOAL = "COMPLETE_GOAL"


@dataclass(frozen=True, slots=True)
class V2WorkObservation:
    work_identity: str
    issue_number: int
    issue_revision: str
    issue_state: str
    lifecycle: str
    project_status: str | None
    priority: str | None
    dependency_states: tuple[str, ...]
    acceptance_digest: str | None
    canonical_design_identities: tuple[str, ...] = ()
    active_lineage_identity: str | None = None
    exact_head_sha: str | None = None
    verification_state: EvidenceState = EvidenceState.NOT_RUN
    verification_identity: str | None = None
    review_state: EvidenceState = EvidenceState.NOT_RUN
    review_identity: str | None = None
    human_verification_required: bool = False
    human_verification_state: EvidenceState = EvidenceState.NOT_REQUIRED
    human_verification_identity: str | None = None
    merged: bool = False
    unresolved_conflict: bool = False
    latest_packet_identity: str | None = None
    latest_checkpoint_identity: str | None = None

    @property
    def dependency_ready(self) -> bool:
        return all(state == "CLOSED" for state in self.dependency_states)

    @property
    def terminal(self) -> bool:
        return self.lifecycle == "COMPLETED" and self.issue_state == "CLOSED"


@dataclass(frozen=True, slots=True)
class V2SupervisorDecision:
    disposition: V2SupervisorDisposition
    work_identity: str | None
    transition: V2Transition | None
    schedule_key: str | None
    detail: str


class V2Supervisor:
    """provider通信を持たないV2 selection / transition decision。"""

    def decide(
        self,
        *,
        goal_revision: str,
        works: tuple[V2WorkObservation, ...],
        current_work_identity: str | None = None,
        dispatched_schedule_keys: frozenset[str] = frozenset(),
        pending_effect: bool = False,
        goal_acceptance_complete: bool = False,
    ) -> V2SupervisorDecision:
        if not goal_revision:
            return V2SupervisorDecision(
                V2SupervisorDisposition.INTERVENTION_REQUIRED,
                None,
                None,
                None,
                "GOAL_REVISION_INVALID",
            )
        if len({work.work_identity for work in works}) != len(works):
            return V2SupervisorDecision(
                V2SupervisorDisposition.INTERVENTION_REQUIRED,
                None,
                None,
                None,
                "WORK_IDENTITY_CONFLICT",
            )

        decisions = {work.work_identity: derive_transition(work) for work in works}
        selected = self._select(works, decisions, current_work_identity)
        if selected is not None:
            transition = decisions[selected.work_identity]
            assert transition is not None
            key = schedule_key(goal_revision, selected, transition)
            if key in dispatched_schedule_keys:
                return V2SupervisorDecision(
                    V2SupervisorDisposition.YIELD_EXTERNAL,
                    selected.work_identity,
                    transition,
                    key,
                    "DUPLICATE_SCHEDULE_SUPPRESSED",
                )
            return V2SupervisorDecision(
                V2SupervisorDisposition.CONTINUE,
                selected.work_identity,
                transition,
                key,
                "ACTIONABLE_WORK_SELECTED",
            )

        conflicts = [work for work in works if work.unresolved_conflict]
        if conflicts:
            return V2SupervisorDecision(
                V2SupervisorDisposition.INTERVENTION_REQUIRED,
                None,
                None,
                None,
                "UNRESOLVED_WORK_CONFLICT",
            )
        if (
            works
            and all(work.terminal for work in works)
            and not pending_effect
            and goal_acceptance_complete
        ):
            return V2SupervisorDecision(
                V2SupervisorDisposition.COMPLETE_GOAL,
                None,
                None,
                None,
                "GOAL_COMPLETION_EVIDENCE_COMPLETE",
            )
        return V2SupervisorDecision(
            V2SupervisorDisposition.YIELD_EXTERNAL,
            None,
            None,
            None,
            "NO_ACTIONABLE_WORK",
        )

    def _select(
        self,
        works: tuple[V2WorkObservation, ...],
        decisions: dict[str, V2Transition | None],
        current_work_identity: str | None,
    ) -> V2WorkObservation | None:
        current = next(
            (work for work in works if work.work_identity == current_work_identity),
            None,
        )
        if current is not None and decisions[current.work_identity] is not None:
            return current
        candidates = [
            work
            for work in works
            if decisions[work.work_identity] is not None and not work.unresolved_conflict
        ]
        if not candidates:
            return None
        return min(candidates, key=_selection_key)


def derive_transition(work: V2WorkObservation) -> V2Transition | None:
    """1 Workのtyped stateから次actionable transitionだけを導出する。"""
    if work.unresolved_conflict or not work.dependency_ready:
        return None
    if work.terminal:
        return None
    if work.merged:
        return V2Transition.COMPLETE_WORK
    if work.lifecycle == "COMPLETED" or work.issue_state == "CLOSED":
        return None
    if not work.acceptance_digest:
        return None
    if not work.canonical_design_identities:
        return V2Transition.DESIGN
    if work.exact_head_sha is None:
        return V2Transition.IMPLEMENT
    if work.verification_state is EvidenceState.FAIL:
        return V2Transition.REPAIR
    if work.verification_state is EvidenceState.NOT_RUN:
        return V2Transition.VERIFY
    if work.verification_state is EvidenceState.PENDING:
        return None
    if work.verification_state is not EvidenceState.PASS:
        return V2Transition.REPAIR
    if work.review_state in {EvidenceState.FAIL, EvidenceState.REQUEST_CHANGES}:
        return V2Transition.REPAIR
    if work.review_state is EvidenceState.NOT_RUN:
        return V2Transition.REVIEW
    if work.review_state is EvidenceState.PENDING:
        return None
    if work.review_state is not EvidenceState.PASS:
        return V2Transition.REPAIR
    if work.human_verification_required:
        if work.human_verification_state in {
            EvidenceState.NOT_RUN,
            EvidenceState.NOT_REQUIRED,
        }:
            return V2Transition.HUMAN_VERIFY
        if work.human_verification_state is EvidenceState.PENDING:
            return None
        if work.human_verification_state is not EvidenceState.PASS:
            return V2Transition.REPAIR
    return V2Transition.INTEGRATE


def schedule_key(
    goal_revision: str,
    work: V2WorkObservation,
    transition: V2Transition,
) -> str:
    state = {
        "goal_revision": goal_revision,
        "work_identity": work.work_identity,
        "issue_revision": work.issue_revision,
        "issue_state": work.issue_state,
        "lifecycle": work.lifecycle,
        "project_status": work.project_status,
        "priority": work.priority,
        "dependency_states": work.dependency_states,
        "acceptance_digest": work.acceptance_digest,
        "canonical_design_identities": work.canonical_design_identities,
        "active_lineage_identity": work.active_lineage_identity,
        "exact_head_sha": work.exact_head_sha,
        "verification": (work.verification_state.value, work.verification_identity),
        "review": (work.review_state.value, work.review_identity),
        "human_verification": (
            work.human_verification_state.value,
            work.human_verification_identity,
        ),
        "latest_packet_identity": work.latest_packet_identity,
        "latest_checkpoint_identity": work.latest_checkpoint_identity,
        "transition": transition.value,
    }
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "schedule:" + hashlib.sha256(encoded.encode()).hexdigest()


def _selection_key(work: V2WorkObservation) -> tuple[int, int, int]:
    priority = _PRIORITY.get(work.priority or "", 4)
    in_progress = 0 if work.project_status == "In progress" else 1
    return priority, in_progress, work.issue_number
