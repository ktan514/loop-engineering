"""V2の各安全componentをGoal完了までboundedに接続する自律Runner。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from .v2_autonomous_runtime import (
    AutonomousDispatch,
    AutonomousRuntimeUnavailable,
    PostgreSQLAutonomousRuntimeStore,
    runtime_identity,
)
from .v2_evidence import EvidenceTarget, V2EvidenceCoordinator, apply_evidence
from .v2_goal_planning import (
    BootstrapResult,
    PlannedWork,
    PlanningProjectionPort,
    ProductDevelopmentRegistration,
    V2GoalBootstrapService,
)
from .v2_supervisor import (
    V2Supervisor,
    V2SupervisorDecision,
    V2SupervisorDisposition,
    V2WorkObservation,
    derive_transition,
    schedule_key,
)
from .v2_work_queue import V2WorkQueueSnapshot


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
    def synchronize(
        self,
        registration: ProductDevelopmentRegistration,
    ) -> V2WorkQueueSnapshot: ...


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
    """採用済みPlanをDBへ固定し、restart後にPlannerを再実行しない。"""

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
    """Work branchに対応するcurrent PR/head/canonical designをlive readbackする。"""

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
        if open_prs is None or len(open_prs) > 1:
            return _LineageSnapshot(replace(work, unresolved_conflict=True), None)
        if open_prs:
            number, head, base = _pr_identity(open_prs[0])
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
        number, head, base = _pr_identity(max(merged, key=_pr_number_sort))
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
        identities: list[str] = []
        for path in paths:
            try:
                raw = self._runner.run(
                    ("gh", "api", f"repos/{repository}/contents/{path}?ref={head}")
                )
                payload = json.loads(raw)
            except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
                return None
            blob = payload.get("sha") if isinstance(payload, dict) else None
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
        work = replace(
            snapshot.observation,
            human_verification_required=planned.human_verification_required,
        )
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
            return self._run(registration, max_iterations)
        except (AutonomousRuntimeUnavailable, ValueError, RuntimeError) as error:
            return AutonomousRunResult(
                AutonomousRunStatus.INTERVENTION_REQUIRED,
                str(error),
                0,
                runtime_identity(registration),
            )

    def _run(
        self,
        registration: ProductDevelopmentRegistration,
        max_iterations: int,
    ) -> AutonomousRunResult:
        state = self._runtime.ensure_runtime(registration)
        if state.status == "COMPLETED":
            return AutonomousRunResult(
                AutonomousRunStatus.GOAL_COMPLETED,
                "GOAL_ALREADY_COMPLETED",
                0,
                state.runtime_identity,
            )
        bootstrap = self._bootstrap.ensure(registration)
        planned = _planned_by_issue(bootstrap)
        progressed = False

        for iteration in range(1, max_iterations + 1):
            queue = self._queue.synchronize(registration)
            works = self._observe_all(registration, queue.works, planned)
            dispatched = self._runtime.dispatched_schedule_keys(state.runtime_identity)
            selectable, suppressed = _without_dispatched(
                registration.goal_revision,
                works,
                dispatched,
            )
            acceptance = self._goal_acceptance.complete(registration, bootstrap, works)
            acceptance = acceptance and not suppressed
            current = _current_if_selectable(queue.current_work_identity, selectable)
            decision = self._supervisor.decide(
                goal_revision=registration.goal_revision,
                works=selectable,
                current_work_identity=current,
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

            terminal = self._terminal_decision(
                state.runtime_identity,
                current,
                decision,
                fingerprint,
                no_progress,
                queue.pending_effect,
                iteration,
            )
            if terminal is not None:
                return terminal

            if (
                decision.work_identity is None
                or decision.transition is None
                or decision.schedule_key is None
            ):
                raise AutonomousRuntimeUnavailable("SUPERVISOR_ACTION_IDENTITY_MISSING")
            work = next(item for item in works if item.work_identity == decision.work_identity)
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
            self._runtime.update_dispatch(
                decision.schedule_key,
                _dispatch_status(outcome.status),
                outcome.detail,
            )
            if outcome.status is TransitionExecutionStatus.INTERVENTION_REQUIRED:
                self._runtime.update_runtime(
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
            if outcome.status is TransitionExecutionStatus.FAILED:
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

        result_status = (
            AutonomousRunStatus.PROGRESSED
            if progressed
            else AutonomousRunStatus.ITERATION_LIMIT
        )
        return AutonomousRunResult(
            result_status,
            "AUTONOMOUS_ITERATION_LIMIT_REACHED",
            max_iterations,
            state.runtime_identity,
            state.current_work_identity,
        )

    def _terminal_decision(
        self,
        runtime_identity_value: str,
        current_work_identity: str | None,
        decision: V2SupervisorDecision,
        fingerprint: str,
        no_progress: int,
        pending_effect: bool,
        iteration: int,
    ) -> AutonomousRunResult | None:
        if decision.disposition is V2SupervisorDisposition.COMPLETE_GOAL:
            self._runtime.update_runtime(
                runtime_identity_value,
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
                runtime_identity_value,
            )
        if decision.disposition is V2SupervisorDisposition.INTERVENTION_REQUIRED:
            self._runtime.update_runtime(
                runtime_identity_value,
                status="INTERVENTION_REQUIRED",
                current_work_identity=current_work_identity,
                schedule_key=None,
                progress_fingerprint=fingerprint,
                no_progress_count=no_progress,
                detail=decision.detail,
            )
            return AutonomousRunResult(
                AutonomousRunStatus.INTERVENTION_REQUIRED,
                decision.detail,
                iteration,
                runtime_identity_value,
                current_work_identity,
            )
        if decision.disposition is not V2SupervisorDisposition.YIELD_EXTERNAL:
            return None
        if no_progress >= self._no_progress_limit and pending_effect:
            detail = "PENDING_EFFECT_RECONCILIATION_REQUIRED"
            status = "INTERVENTION_REQUIRED"
            result_status = AutonomousRunStatus.INTERVENTION_REQUIRED
        else:
            detail = decision.detail
            status = "WAITING"
            result_status = AutonomousRunStatus.WAITING
        self._runtime.update_runtime(
            runtime_identity_value,
            status=status,
            current_work_identity=current_work_identity,
            schedule_key=decision.schedule_key,
            progress_fingerprint=fingerprint,
            no_progress_count=no_progress,
            detail=detail,
        )
        return AutonomousRunResult(
            result_status,
            detail,
            iteration,
            runtime_identity_value,
            current_work_identity,
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
            suppressed_incomplete = suppressed_incomplete or work.lifecycle != "COMPLETED"
            continue
        kept.append(work)
    return tuple(kept), suppressed_incomplete


def _current_if_selectable(
    current_work_identity: str | None,
    works: tuple[V2WorkObservation, ...],
) -> str | None:
    if current_work_identity is None:
        return None
    return (
        current_work_identity
        if any(work.work_identity == current_work_identity for work in works)
        else None
    )


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
                "selected_transition": work.selected_transition,
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
    return "progress:" + hashlib.sha256(raw.encode()).hexdigest()


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
    invalid = (
        not branch
        or branch.startswith("/")
        or branch.endswith("/")
        or ".." in branch
        or any(char in branch for char in " ~^:?*[\\")
    )
    if invalid:
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
