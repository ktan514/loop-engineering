"""V2の各安全componentをGoal完了までboundedに接続する自律Runner。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from .v2_autonomous_runtime import (
    AutonomousDispatch,
    AutonomousRuntimeState,
    AutonomousRuntimeUnavailable,
    PostgreSQLAutonomousRuntimeStore,
    runtime_identity,
)
from .v2_evidence import EvidenceTarget, V2EvidenceCoordinator, apply_evidence
from .v2_goal_planning import (
    BootstrapResult,
    PlanningProjectionPort,
    PlannedWork,
    ProductDevelopmentRegistration,
    V2GoalBootstrapService,
)
from .v2_supervisor import (
    V2Supervisor,
    V2SupervisorDecision,
    V2SupervisorDisposition,
    V2Transition,
    V2WorkObservation,
    derive_transition,
    schedule_key,
)
from .v2_work_queue import V2WorkQueueSnapshot

_PR_ID_RE = re.compile(r"pr:(\d+)")


class AutonomousRunStatus(str, Enum):
    GOAL_COMPLETED = "GOAL_COMPLETED"
    PROGRESSED = "PROGRESSED"
    WAITING = "WAITING"
    INTERVENTION_REQUIRED = "INTERVENTION_REQUIRED"
    ITERATION_LIMIT = "ITERATION_LIMIT"


class TransitionExecutionStatus(str, Enum):
    PROGRESSED = "PROGRESSED"
    WAITING = "WAITING"
    FAILED = "FAILED"
    INTERVENTION_REQUIRED = "INTERVENTION_REQUIRED"


@dataclass(frozen=True, slots=True)
class TransitionExecutionResult:
    status: TransitionExecutionStatus
    detail: str


@dataclass(frozen=True, slots=True)
class AutonomousRunResult:
    status: AutonomousRunStatus
    detail: str
    iterations: int
    runtime_identity: str
    current_work_identity: str | None = None


class WorkQueuePort(Protocol):
    def synchronize(self, registration: ProductDevelopmentRegistration) -> V2WorkQueueSnapshot: ...


class TransitionExecutorPort(Protocol):
    def execute(
        self,
        registration: ProductDevelopmentRegistration,
        bootstrap: BootstrapResult,
        work: V2WorkObservation,
        planned_work: PlannedWork,
        decision: V2SupervisorDecision,
    ) -> TransitionExecutionResult: ...


class GoalAcceptancePort(Protocol):
    def complete(
        self,
        registration: ProductDevelopmentRegistration,
        bootstrap: BootstrapResult,
        works: tuple[V2WorkObservation, ...],
    ) -> bool: ...


class LineageCommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str: ...


@dataclass(frozen=True, slots=True)
class _LineageSnapshot:
    observation: V2WorkObservation
    pr_number: int | None


class DurableGoalBootstrap:
    """最初に採用したPlanをDBへ固定し、restart後は同じPlanだけを再projectionする。"""

    def __init__(
        self,
        runtime: PostgreSQLAutonomousRuntimeStore,
        bootstrap: V2GoalBootstrapService,
        projection: PlanningProjectionPort,
    ) -> None:
        self._runtime = runtime
        self._bootstrap = bootstrap
        self._projection = projection

    def ensure(self, registration: ProductDevelopmentRegistration) -> BootstrapResult:
        identity = runtime_identity(registration)
        stored = self._runtime.plan(identity)
        if stored is not None:
            projection = self._projection.ensure_plan(registration, stored.proposal)
            if projection != stored.projection:
                raise AutonomousRuntimeUnavailable("DURABLE_PLAN_PROJECTION_CONFLICT")
            return stored
        result = self._bootstrap.bootstrap(registration)
        self._runtime.save_plan(identity, result)
        return result


class GitHubAutonomousLineageObserver:
    """Work branchに対応するcurrent PR/headとcanonical design blobをlive readbackする。"""

    def __init__(self, runner: LineageCommandRunner) -> None:
        self._runner = runner

    def observe(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        planned: PlannedWork,
    ) -> _LineageSnapshot:
        branch = _work_branch(registration.work_branch_template, work.issue_number)
        open_prs = self._pr_list(registration.repository_identity, branch, "open")
        if open_prs is None:
            return _LineageSnapshot(replace(work, unresolved_conflict=True), None)
        if len(open_prs) > 1:
            return _LineageSnapshot(replace(work, unresolved_conflict=True), None)
        if len(open_prs) == 1:
            pr = open_prs[0]
            number, head, base = _pr_identity(pr)
            if number is None or head is None or base != registration.trunk_branch:
                return _LineageSnapshot(replace(work, unresolved_conflict=True), None)
            designs = self._design_identities(
                registration.repository_identity,
                head,
                planned.canonical_design_targets,
            )
            if designs is None:
                return _LineageSnapshot(replace(work, unresolved_conflict=True), number)
            return _LineageSnapshot(
                replace(
                    work,
                    active_lineage_identity=f"pr:{number}",
                    exact_head_sha=head,
                    canonical_design_identities=designs,
                    merged=False,
                ),
                number,
            )

        merged = self._pr_list(registration.repository_identity, branch, "merged")
        if merged is None:
            return _LineageSnapshot(replace(work, unresolved_conflict=True), None)
        if not merged:
            return _LineageSnapshot(work, None)
        latest = max(merged, key=_pr_number_sort)
        number, head, base = _pr_identity(latest)
        if number is None or head is None or base != registration.trunk_branch:
            return _LineageSnapshot(replace(work, unresolved_conflict=True), None)
        return _LineageSnapshot(
            replace(
                work,
                active_lineage_identity=f"pr:{number}",
                exact_head_sha=head,
                merged=True,
            ),
            number,
        )

    def _pr_list(
        self,
        repository: str,
        branch: str,
        state: str,
    ) -> list[dict[str, object]] | None:
        try:
            raw = self._runner.run(
                (
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repository,
                    "--head",
                    branch,
                    "--state",
                    state,
                    "--limit",
                    "20",
                    "--json",
                    "number,url,headRefOid,headRefName,baseRefName,isDraft",
                )
            )
            payload = json.loads(raw)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            return None
        return [item for item in payload if isinstance(item, dict)]

    def _design_identities(
        self,
        repository: str,
        head: str,
        paths: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        if not paths:
            return ()
        identities: list[str] = []
        for path in paths:
            try:
                raw = self._runner.run(
                    (
                        "gh",
                        "api",
                        f"repos/{repository}/contents/{path}?ref={head}",
                    )
                )
                payload = json.loads(raw)
            except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
                return None
            if not isinstance(payload, dict):
                return None
            blob = payload.get("sha")
            if not isinstance(blob, str) or not blob:
                return None
            identities.append(f"design:{path}:{blob}")
        return tuple(identities)


class EvidenceEnricher:
    def __init__(self, evidence: V2EvidenceCoordinator) -> None:
        self._evidence = evidence

    def enrich(
        self,
        registration: ProductDevelopmentRegistration,
        snapshot: _LineageSnapshot,
        planned: PlannedWork,
    ) -> V2WorkObservation:
        work = snapshot.observation
        if snapshot.pr_number is None or work.exact_head_sha is None or work.merged:
            return work
        target = EvidenceTarget(
            repository=registration.repository_identity,
            work_identity=work.work_identity,
            issue_number=work.issue_number,
            pr_number=snapshot.pr_number,
            head_sha=work.exact_head_sha,
            base_branch=registration.trunk_branch,
            canonical_design_identities=work.canonical_design_identities,
            acceptance_digest=work.acceptance_digest or "",
        )
        bundle = self._evidence.observe(target, planned.human_verification_required)
        return apply_evidence(work, bundle)


class AllWorkGoalAcceptance:
    def complete(
        self,
        registration: ProductDevelopmentRegistration,
        bootstrap: BootstrapResult,
        works: tuple[V2WorkObservation, ...],
    ) -> bool:
        del registration, bootstrap
        return bool(works) and all(work.lifecycle == "COMPLETED" for work in works)


class V2AutonomousRunner:
    def __init__(
        self,
        runtime: PostgreSQLAutonomousRuntimeStore,
        bootstrap: DurableGoalBootstrap,
        queue: WorkQueuePort,
        lineage: GitHubAutonomousLineageObserver,
        evidence: EvidenceEnricher,
        supervisor: V2Supervisor,
        transitions: TransitionExecutorPort,
        goal_acceptance: GoalAcceptancePort | None = None,
        *,
        no_progress_limit: int = 3,
    ) -> None:
        if not 1 <= no_progress_limit <= 20:
            raise ValueError("NO_PROGRESS_LIMIT_INVALID")
        self._runtime = runtime
        self._bootstrap = bootstrap
        self._queue = queue
        self._lineage = lineage
        self._evidence = evidence
        self._supervisor = supervisor
        self._transitions = transitions
        self._goal_acceptance = goal_acceptance or AllWorkGoalAcceptance()
        self._no_progress_limit = no_progress_limit

    def run(
        self,
        registration: ProductDevelopmentRegistration,
        *,
        max_iterations: int = 20,
    ) -> AutonomousRunResult:
        if not 1 <= max_iterations <= 1000:
            raise ValueError("AUTONOMOUS_ITERATION_LIMIT_INVALID")
        try:
            state = self._runtime.ensure_runtime(registration)
            if state.status == "COMPLETED":
                return AutonomousRunResult(
                    AutonomousRunStatus.GOAL_COMPLETED,
                    "GOAL_ALREADY_COMPLETED",
                    0,
                    state.runtime_identity,
                    None,
                )
            bootstrap = self._bootstrap.ensure(registration)
            planned = _planned_by_issue(bootstrap)
            progressed = False
            for iteration in range(1, max_iterations + 1):
                queue = self._queue.synchronize(registration)
                works = self._observe_all(registration, queue.works, planned)
                dispatched = self._runtime.dispatched_schedule_keys(state.runtime_identity)
                selectable, suppressed_incomplete = _without_dispatched(
                    registration.goal_revision,
                    works,
                    dispatched,
                )
                acceptance = self._goal_acceptance.complete(registration, bootstrap, works)
                if suppressed_incomplete:
                    acceptance = False
                current = queue.current_work_identity
                if current is not None and all(
                    work.work_identity != current for work in selectable
                ):
                    current = None
                decision = self._supervisor.decide(
                    goal_revision=registration.goal_revision,
                    works=selectable,
                    current_work_identity=current,
                    dispatched_schedule_keys=frozenset(),
                    pending_effect=queue.pending_effect,
                    goal_acceptance_complete=acceptance,
                )
                fingerprint = _progress_fingerprint(
                    registration.goal_revision,
                    works,
                    queue.pending_effect,
                    decision,
                )
                no_progress = (
                    state.no_progress_count + 1
                    if fingerprint == state.last_progress_fingerprint
                    else 0
                )

                if decision.disposition is V2SupervisorDisposition.COMPLETE_GOAL:
                    state = self._runtime.update_runtime(
                        state.runtime_identity,
                        status="COMPLETED",
                        current_work_identity=None,
                        schedule_key=None,
                        progress_fingerprint=fingerprint,
                        no_progress_count=0,
                        detail=decision.detail,
                    )
                    return AutonomousRunResult(
                        AutonomousRunStatus.GOAL_COMPLETED,
                        decision.detail,
                        iteration,
                        state.runtime_identity,
                        None,
                    )
                if decision.disposition is V2SupervisorDisposition.INTERVENTION_REQUIRED:
                    state = self._runtime.update_runtime(
                        state.runtime_identity,
                        status="INTERVENTION_REQUIRED",
                        current_work_identity=current,
                        schedule_key=None,
                        progress_fingerprint=fingerprint,
                        no_progress_count=no_progress,
                        detail=decision.detail,
                    )
                    return AutonomousRunResult(
                        AutonomousRunStatus.INTERVENTION_REQUIRED,
                        decision.detail,
                        iteration,
                        state.runtime_identity,
                        current,
                    )
                if decision.disposition is V2SupervisorDisposition.YIELD_EXTERNAL:
                    detail = decision.detail
                    if no_progress >= self._no_progress_limit and queue.pending_effect:
                        detail = "PENDING_EFFECT_RECONCILIATION_REQUIRED"
                        status = "INTERVENTION_REQUIRED"
                        result_status = AutonomousRunStatus.INTERVENTION_REQUIRED
                    else:
                        status = "WAITING"
                        result_status = AutonomousRunStatus.WAITING
                    state = self._runtime.update_runtime(
                        state.runtime_identity,
                        status=status,
                        current_work_identity=current,
                        schedule_key=decision.schedule_key,
                        progress_fingerprint=fingerprint,
                        no_progress_count=no_progress,
                        detail=detail,
                    )
                    return AutonomousRunResult(
                        result_status,
                        detail,
                        iteration,
                        state.runtime_identity,
                        current,
                    )

                if (
                    decision.work_identity is None
                    or decision.transition is None
                    or decision.schedule_key is None
                ):
                    raise AutonomousRuntimeUnavailable("SUPERVISOR_ACTION_IDENTITY_MISSING")
                work = next(
                    item for item in works if item.work_identity == decision.work_identity
                )
                planned_work = planned.get(work.issue_number)
                if planned_work is None:
                    raise AutonomousRuntimeUnavailable("PLANNED_WORK_MAPPING_MISSING")
                dispatch = AutonomousDispatch(
                    decision.schedule_key,
                    state.runtime_identity,
                    work.work_identity,
                    decision.transition.value,
                    "DISPATCHED",
                    "SUPERVISOR_DISPATCH",
                )
                if not self._runtime.dispatch(dispatch):
                    state = self._runtime.update_runtime(
                        state.runtime_identity,
                        status="WAITING",
                        current_work_identity=work.work_identity,
                        schedule_key=decision.schedule_key,
                        progress_fingerprint=fingerprint,
                        no_progress_count=no_progress,
                        detail="DUPLICATE_DISPATCH_SUPPRESSED",
                    )
                    continue
                outcome = self._transitions.execute(
                    registration,
                    bootstrap,
                    work,
                    planned_work,
                    decision,
                )
                dispatch_status = _dispatch_status(outcome.status)
                self._runtime.update_dispatch(
                    decision.schedule_key,
                    dispatch_status,
                    outcome.detail,
                )
                if outcome.status is TransitionExecutionStatus.INTERVENTION_REQUIRED:
                    state = self._runtime.update_runtime(
                        state.runtime_identity,
                        status="INTERVENTION_REQUIRED",
                        current_work_identity=work.work_identity,
                        schedule_key=decision.schedule_key,
                        progress_fingerprint=fingerprint,
                        no_progress_count=no_progress,
                        detail=outcome.detail,
                    )
                    return AutonomousRunResult(
                        AutonomousRunStatus.INTERVENTION_REQUIRED,
                        outcome.detail,
                        iteration,
                        state.runtime_identity,
                        work.work_identity,
                    )
                if outcome.status is TransitionExecutionStatus.WAITING:
                    state = self._runtime.update_runtime(
                        state.runtime_identity,
                        status="ACTIVE",
                        current_work_identity=work.work_identity,
                        schedule_key=decision.schedule_key,
                        progress_fingerprint=fingerprint,
                        no_progress_count=no_progress,
                        detail=outcome.detail,
                    )
                    continue
                if outcome.status is TransitionExecutionStatus.FAILED:
                    if no_progress >= self._no_progress_limit:
                        state = self._runtime.update_runtime(
                            state.runtime_identity,
                            status="WAITING",
                            current_work_identity=work.work_identity,
                            schedule_key=decision.schedule_key,
                            progress_fingerprint=fingerprint,
                            no_progress_count=no_progress,
                            detail="REPAIRABLE_FAILURE_RETRY_BOUNDED",
                        )
                        return AutonomousRunResult(
                            AutonomousRunStatus.WAITING,
                            "REPAIRABLE_FAILURE_RETRY_BOUNDED",
                            iteration,
                            state.runtime_identity,
                            work.work_identity,
                        )
                    state = self._runtime.update_runtime(
                        state.runtime_identity,
                        status="ACTIVE",
                        current_work_identity=work.work_identity,
                        schedule_key=decision.schedule_key,
                        progress_fingerprint=fingerprint,
                        no_progress_count=no_progress,
                        detail=outcome.detail,
                    )
                    continue

                progressed = True
                state = self._runtime.update_runtime(
                    state.runtime_identity,
                    status="ACTIVE",
                    current_work_identity=work.work_identity,
                    schedule_key=decision.schedule_key,
                    progress_fingerprint=None,
                    no_progress_count=0,
                    detail=outcome.detail,
                )
            return AutonomousRunResult(
                AutonomousRunStatus.PROGRESSED if progressed else AutonomousRunStatus.ITERATION_LIMIT,
                "AUTONOMOUS_ITERATION_LIMIT_REACHED",
                max_iterations,
                state.runtime_identity,
                state.current_work_identity,
            )
        except (AutonomousRuntimeUnavailable, ValueError) as error:
            identity = runtime_identity(registration)
            return AutonomousRunResult(
                AutonomousRunStatus.INTERVENTION_REQUIRED,
                str(error),
                0,
                identity,
                None,
            )

    def _observe_all(
        self,
        registration: ProductDevelopmentRegistration,
        works: tuple[V2WorkObservation, ...],
        planned: dict[int, PlannedWork],
    ) -> tuple[V2WorkObservation, ...]:
        observed: list[V2WorkObservation] = []
        for work in works:
            planned_work = planned.get(work.issue_number)
            if planned_work is None:
                observed.append(replace(work, unresolved_conflict=True))
                continue
            lineage = self._lineage.observe(registration, work, planned_work)
            observed.append(self._evidence.enrich(registration, lineage, planned_work))
        return tuple(observed)


def _planned_by_issue(bootstrap: BootstrapResult) -> dict[int, PlannedWork]:
    proposal = {work.logical_key: work for work in bootstrap.proposal.works}
    result: dict[int, PlannedWork] = {}
    for projected in bootstrap.projection.works:
        planned = proposal.get(projected.logical_key)
        if planned is None or projected.issue_number in result:
            raise AutonomousRuntimeUnavailable("PLANNED_WORK_MAPPING_CONFLICT")
        result[projected.issue_number] = planned
    if len(result) != len(proposal):
        raise AutonomousRuntimeUnavailable("PLANNED_WORK_MAPPING_CONFLICT")
    return result


def _without_dispatched(
    goal_revision: str,
    works: tuple[V2WorkObservation, ...],
    dispatched: frozenset[str],
) -> tuple[tuple[V2WorkObservation, ...], bool]:
    kept: list[V2WorkObservation] = []
    suppressed_incomplete = False
    for work in works:
        transition = derive_transition(work)
        if transition is None:
            kept.append(work)
            continue
        key = schedule_key(goal_revision, work, transition)
        if key in dispatched:
            if work.lifecycle != "COMPLETED":
                suppressed_incomplete = True
            continue
        kept.append(work)
    return tuple(kept), suppressed_incomplete


def _progress_fingerprint(
    goal_revision: str,
    works: tuple[V2WorkObservation, ...],
    pending_effect: bool,
    decision: V2SupervisorDecision,
) -> str:
    payload = {
        "goal_revision": goal_revision,
        "pending_effect": pending_effect,
        "decision": (
            decision.disposition.value,
            decision.work_identity,
            decision.transition.value if decision.transition is not None else None,
            decision.schedule_key,
            decision.detail,
        ),
        "works": [
            {
                "id": work.work_identity,
                "revision": work.issue_revision,
                "lifecycle": work.lifecycle,
                "head": work.exact_head_sha,
                "lineage": work.active_lineage_identity,
                "ci": (work.verification_state.value, work.verification_identity),
                "review": (work.review_state.value, work.review_identity),
                "human": (
                    work.human_verification_state.value,
                    work.human_verification_identity,
                ),
                "merged": work.merged,
                "conflict": work.unresolved_conflict,
                "packet": work.latest_packet_identity,
                "checkpoint": work.latest_checkpoint_identity,
            }
            for work in works
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "progress:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dispatch_status(status: TransitionExecutionStatus) -> str:
    if status is TransitionExecutionStatus.PROGRESSED:
        return "COMPLETED"
    if status is TransitionExecutionStatus.WAITING:
        return "WAITING"
    return "FAILED"


def _work_branch(template: str, issue_number: int) -> str:
    if template.count("{issue}") != 1 or issue_number < 1:
        raise AutonomousRuntimeUnavailable("WORK_BRANCH_TEMPLATE_INVALID")
    branch = template.replace("{issue}", str(issue_number))
    if (
        not branch
        or branch.startswith("/")
        or branch.endswith("/")
        or ".." in branch
        or " " in branch
        or "~" in branch
        or "^" in branch
        or ":" in branch
        or "?" in branch
        or "*" in branch
        or "[" in branch
        or "\\" in branch
    ):
        raise AutonomousRuntimeUnavailable("WORK_BRANCH_TEMPLATE_INVALID")
    return branch


def _pr_identity(item: Mapping[str, object]) -> tuple[int | None, str | None, str | None]:
    number = item.get("number")
    head = item.get("headRefOid")
    base = item.get("baseRefName")
    return (
        number if isinstance(number, int) and number > 0 else None,
        head if isinstance(head, str) and head else None,
        base if isinstance(base, str) and base else None,
    )


def _pr_number_sort(item: Mapping[str, object]) -> int:
    number = item.get("number")
    return number if isinstance(number, int) else -1
