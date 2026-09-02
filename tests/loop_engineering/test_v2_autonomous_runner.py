from dataclasses import replace
from pathlib import Path

from loop_engineering.v2_autonomous_runner import (
    AutonomousRunStatus,
    DurableGoalBootstrap,
    EvidenceEnricher,
    GitHubAutonomousLineageObserver,
    TransitionExecutionResult,
    TransitionExecutionStatus,
    V2AutonomousRunner,
    _LineageSnapshot,
)
from loop_engineering.v2_autonomous_runtime import (
    AutonomousDispatch,
    AutonomousRuntimeState,
    PostgreSQLAutonomousRuntimeStore,
    runtime_identity,
)
from loop_engineering.v2_goal_planning import (
    BootstrapResult,
    PlannedWork,
    ProductDevelopmentRegistration,
    ProjectedPlan,
    ProjectedWork,
    WorkPlanProposal,
    proposal_identity,
)
from loop_engineering.v2_supervisor import EvidenceState, V2Supervisor, V2WorkObservation
from loop_engineering.v2_work_queue import V2WorkQueueSnapshot


class MemoryRuntime(PostgreSQLAutonomousRuntimeStore):
    def __init__(self) -> None:
        self.state: AutonomousRuntimeState | None = None
        self.dispatches: dict[str, AutonomousDispatch] = {}
        self.saved_plan: BootstrapResult | None = None

    def ensure_runtime(self, registration: ProductDevelopmentRegistration) -> AutonomousRuntimeState:
        if self.state is None:
            self.state = AutonomousRuntimeState(
                runtime_identity(registration),
                registration.product_key,
                registration.repository_identity,
                registration.goal_revision,
                "ACTIVE",
                None,
                None,
                None,
                0,
                "",
            )
        return self.state

    def update_runtime(
        self,
        runtime_identity_value: str,
        *,
        status: str,
        current_work_identity: str | None,
        schedule_key: str | None,
        progress_fingerprint: str | None,
        no_progress_count: int,
        detail: str,
    ) -> AutonomousRuntimeState:
        assert self.state is not None and self.state.runtime_identity == runtime_identity_value
        self.state = AutonomousRuntimeState(
            self.state.runtime_identity,
            self.state.product_key,
            self.state.repository,
            self.state.goal_revision,
            status,
            current_work_identity,
            schedule_key,
            progress_fingerprint,
            no_progress_count,
            detail,
        )
        return self.state

    def plan(self, runtime_identity_value: str) -> BootstrapResult | None:
        del runtime_identity_value
        return self.saved_plan

    def save_plan(self, runtime_identity_value: str, result: BootstrapResult) -> None:
        del runtime_identity_value
        self.saved_plan = result

    def dispatch(self, item: AutonomousDispatch) -> bool:
        self.dispatches.setdefault(item.schedule_key, item)
        return self.dispatches[item.schedule_key] == item

    def update_dispatch(self, schedule_key: str, status: str, detail: str) -> None:
        current = self.dispatches[schedule_key]
        self.dispatches[schedule_key] = replace(current, status=status, detail=detail)

    def dispatched_schedule_keys(self, runtime_identity_value: str) -> frozenset[str]:
        del runtime_identity_value
        return frozenset(
            key
            for key, item in self.dispatches.items()
            if item.status in {"DISPATCHED", "COMPLETED", "WAITING"}
        )


class FixedBootstrap(DurableGoalBootstrap):
    def __init__(self, result: BootstrapResult) -> None:
        self.result = result

    def ensure(self, registration: ProductDevelopmentRegistration) -> BootstrapResult:
        del registration
        return self.result


class MutableQueue:
    def __init__(self, works: tuple[V2WorkObservation, ...], current: str | None = None) -> None:
        self.works = works
        self.current = current
        self.pending_effect = False

    def synchronize(self, registration: ProductDevelopmentRegistration) -> V2WorkQueueSnapshot:
        del registration
        return V2WorkQueueSnapshot(self.works, self.current, self.pending_effect)


class PassLineage(GitHubAutonomousLineageObserver):
    def __init__(self) -> None:
        pass

    def observe(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        planned: PlannedWork,
    ) -> _LineageSnapshot:
        del registration, planned
        return _LineageSnapshot(work, _pr_number(work.active_lineage_identity))


class PassEvidence(EvidenceEnricher):
    def __init__(self) -> None:
        pass

    def enrich(
        self,
        registration: ProductDevelopmentRegistration,
        snapshot: _LineageSnapshot,
        planned: PlannedWork,
    ) -> V2WorkObservation:
        del registration, planned
        return snapshot.observation


class RecordingTransitions:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, registration, bootstrap, work, planned_work, decision):
        del registration, bootstrap, planned_work, decision
        self.calls.append(work.work_identity)
        return TransitionExecutionResult(TransitionExecutionStatus.PROGRESSED, "ok")


def registration() -> ProductDevelopmentRegistration:
    return ProductDevelopmentRegistration(
        product_key="sample",
        workspace_canonical_path=Path("/tmp/sample"),
        repository_identity="owner/sample",
        project_owner="owner",
        project_number=10,
        trunk_branch="main",
        goal_definition_identity="goal:sample",
        goal_revision="rev-1",
        goal_text="sample",
        acceptance_criteria=("done",),
        work_branch_template="feature/work-{issue}",
        ci_workflow_name="CI",
        initial_project_status="Backlog",
    )


def bootstrap() -> BootstrapResult:
    reg = registration()
    works = (
        PlannedWork("first", "First", "p1", ("done1",), canonical_design_targets=("docs/a.md",)),
        PlannedWork("second", "Second", "p2", ("done2",), canonical_design_targets=("docs/b.md",)),
    )
    proposal = WorkPlanProposal(
        proposal_identity(reg, works),
        reg.goal_revision,
        works,
        ("done",),
    )
    return BootstrapResult(
        proposal,
        ProjectedPlan(
            1,
            "goal-url",
            "goal-item",
            (
                ProjectedWork("first", 2, "u2", "i2", "d2", ()),
                ProjectedWork("second", 3, "u3", "i3", "d3", ()),
            ),
        ),
    )


def observation(issue: int, **changes) -> V2WorkObservation:
    base = V2WorkObservation(
        work_identity=f"work:owner/sample:{issue}",
        issue_number=issue,
        issue_revision=f"r{issue}",
        issue_state="OPEN",
        lifecycle="PLANNED",
        project_status="Backlog",
        priority="P1",
        dependency_states=(),
        acceptance_digest=f"d{issue}",
    )
    return replace(base, **changes)


def runner(
    runtime: MemoryRuntime,
    queue: MutableQueue,
    transitions: RecordingTransitions,
) -> V2AutonomousRunner:
    return V2AutonomousRunner(
        runtime,
        FixedBootstrap(bootstrap()),
        queue,
        PassLineage(),
        PassEvidence(),
        V2Supervisor(),
        transitions,
    )


def test_waiting_current_work_does_not_block_second_work() -> None:
    waiting = observation(
        2,
        lifecycle="RUNNING",
        canonical_design_identities=("design:a",),
        exact_head_sha="a" * 40,
        active_lineage_identity="pr:7",
        verification_state=EvidenceState.PENDING,
    )
    second = observation(3)
    runtime = MemoryRuntime()
    transitions = RecordingTransitions()

    result = runner(runtime, MutableQueue((waiting, second), waiting.work_identity), transitions).run(
        registration(), max_iterations=1
    )

    assert result.status is AutonomousRunStatus.PROGRESSED
    assert transitions.calls == [second.work_identity]


def test_restart_suppresses_same_schedule_and_selects_other_work() -> None:
    first = observation(2)
    second = observation(3, priority="P2")
    runtime = MemoryRuntime()
    state = runtime.ensure_runtime(registration())
    from loop_engineering.v2_supervisor import V2Transition, schedule_key

    key = schedule_key(registration().goal_revision, first, V2Transition.DESIGN)
    runtime.dispatches[key] = AutonomousDispatch(
        key,
        state.runtime_identity,
        first.work_identity,
        "DESIGN",
        "WAITING",
        "restart",
    )
    transitions = RecordingTransitions()

    runner(runtime, MutableQueue((first, second)), transitions).run(registration(), max_iterations=1)

    assert transitions.calls == [second.work_identity]


def test_all_completed_works_complete_goal() -> None:
    completed1 = observation(2, issue_state="CLOSED", lifecycle="COMPLETED")
    completed2 = observation(3, issue_state="CLOSED", lifecycle="COMPLETED")
    runtime = MemoryRuntime()
    transitions = RecordingTransitions()

    result = runner(runtime, MutableQueue((completed1, completed2)), transitions).run(registration())

    assert result.status is AutonomousRunStatus.GOAL_COMPLETED
    assert transitions.calls == []
    assert runtime.state is not None and runtime.state.status == "COMPLETED"


def test_pending_effect_escalates_only_after_repeated_same_state() -> None:
    blocked = observation(2, dependency_states=("OPEN",))
    runtime = MemoryRuntime()
    queue = MutableQueue((blocked,))
    queue.pending_effect = True
    transitions = RecordingTransitions()
    app = runner(runtime, queue, transitions)

    first = app.run(registration())
    second = app.run(registration())
    third = app.run(registration())
    fourth = app.run(registration())

    assert first.status is AutonomousRunStatus.WAITING
    assert second.status is AutonomousRunStatus.WAITING
    assert third.status is AutonomousRunStatus.WAITING
    assert fourth.status is AutonomousRunStatus.INTERVENTION_REQUIRED


def _pr_number(identity: str | None) -> int | None:
    if identity is None or not identity.startswith("pr:"):
        return None
    value = identity[3:]
    return int(value) if value.isdigit() else None
