from loop_engineering.v2_supervisor import (
    EvidenceState,
    V2Supervisor,
    V2SupervisorDisposition,
    V2Transition,
    V2WorkObservation,
    derive_transition,
    schedule_key,
)


def work(
    issue: int,
    *,
    issue_state: str = "OPEN",
    lifecycle: str = "PLANNED",
    project_status: str | None = "Backlog",
    priority: str | None = "P1",
    dependency_states: tuple[str, ...] = (),
    canonical_design_identities: tuple[str, ...] = (),
    exact_head_sha: str | None = None,
    verification_state: EvidenceState = EvidenceState.NOT_RUN,
    review_state: EvidenceState = EvidenceState.NOT_RUN,
    human_verification_required: bool = False,
    human_verification_state: EvidenceState = EvidenceState.NOT_REQUIRED,
    unresolved_conflict: bool = False,
) -> V2WorkObservation:
    return V2WorkObservation(
        work_identity=f"work:owner/repo:{issue}",
        issue_number=issue,
        issue_revision=f"definition:{issue}",
        issue_state=issue_state,
        lifecycle=lifecycle,
        project_status=project_status,
        priority=priority,
        dependency_states=dependency_states,
        acceptance_digest=f"digest:{issue}",
        canonical_design_identities=canonical_design_identities,
        exact_head_sha=exact_head_sha,
        verification_state=verification_state,
        review_state=review_state,
        human_verification_required=human_verification_required,
        human_verification_state=human_verification_state,
        unresolved_conflict=unresolved_conflict,
    )


def test_missing_design_selects_design_transition() -> None:
    target = work(1)

    decision = V2Supervisor().decide(goal_revision="rev-1", works=(target,))

    assert decision.disposition is V2SupervisorDisposition.CONTINUE
    assert decision.work_identity == target.work_identity
    assert decision.transition is V2Transition.DESIGN


def test_wait_only_current_work_does_not_block_other_actionable_work() -> None:
    waiting = work(
        1,
        canonical_design_identities=("design:a",),
        exact_head_sha="a" * 40,
        verification_state=EvidenceState.PENDING,
        project_status="In progress",
        priority="P0",
        lifecycle="RUNNING",
    )
    actionable = work(2, priority="P2")

    decision = V2Supervisor().decide(
        goal_revision="rev-1",
        works=(waiting, actionable),
        current_work_identity=waiting.work_identity,
    )

    assert decision.work_identity == actionable.work_identity
    assert decision.transition is V2Transition.DESIGN


def test_dependency_pending_is_wait_only() -> None:
    target = work(1, dependency_states=("OPEN",))

    decision = V2Supervisor().decide(goal_revision="rev-1", works=(target,))

    assert decision.disposition is V2SupervisorDisposition.YIELD_EXTERNAL
    assert decision.work_identity is None


def test_review_request_changes_returns_same_work_to_repair() -> None:
    target = work(
        1,
        canonical_design_identities=("design:a",),
        exact_head_sha="a" * 40,
        verification_state=EvidenceState.PASS,
        review_state=EvidenceState.REQUEST_CHANGES,
    )

    assert derive_transition(target) is V2Transition.REPAIR


def test_human_verification_is_required_before_integrate() -> None:
    target = work(
        1,
        canonical_design_identities=("design:a",),
        exact_head_sha="a" * 40,
        verification_state=EvidenceState.PASS,
        review_state=EvidenceState.PASS,
        human_verification_required=True,
        human_verification_state=EvidenceState.NOT_RUN,
    )

    assert derive_transition(target) is V2Transition.HUMAN_VERIFY


def test_duplicate_schedule_is_suppressed() -> None:
    target = work(1)
    key = schedule_key("rev-1", target, V2Transition.DESIGN)

    decision = V2Supervisor().decide(
        goal_revision="rev-1",
        works=(target,),
        dispatched_schedule_keys=frozenset({key}),
    )

    assert decision.disposition is V2SupervisorDisposition.YIELD_EXTERNAL
    assert decision.detail == "DUPLICATE_SCHEDULE_SUPPRESSED"


def test_unresolved_conflict_requires_intervention_when_nothing_else_is_actionable() -> None:
    target = work(1, unresolved_conflict=True)

    decision = V2Supervisor().decide(goal_revision="rev-1", works=(target,))

    assert decision.disposition is V2SupervisorDisposition.INTERVENTION_REQUIRED
    assert decision.detail == "UNRESOLVED_WORK_CONFLICT"


def test_goal_completion_requires_terminal_work_and_acceptance_evidence() -> None:
    target = work(1, issue_state="CLOSED", lifecycle="COMPLETED")

    incomplete = V2Supervisor().decide(
        goal_revision="rev-1",
        works=(target,),
        goal_acceptance_complete=False,
    )
    complete = V2Supervisor().decide(
        goal_revision="rev-1",
        works=(target,),
        goal_acceptance_complete=True,
    )

    assert incomplete.disposition is V2SupervisorDisposition.YIELD_EXTERNAL
    assert complete.disposition is V2SupervisorDisposition.COMPLETE_GOAL
