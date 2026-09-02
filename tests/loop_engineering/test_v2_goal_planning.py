from pathlib import Path

import pytest

from loop_engineering.v2_goal_planning import (
    PlannedWork,
    PlanningValidationError,
    ProductDevelopmentRegistration,
    SingleWorkGoalPlanner,
    WorkPlanProposal,
    acceptance_digest,
    proposal_identity,
    validate_work_plan,
)


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
        goal_text="文字統計CLIを完成させる",
        acceptance_criteria=("ファイル入力", "JSON出力"),
        work_branch_template="feature/work-{issue}",
        ci_workflow_name="Deterministic CI",
        initial_project_status="Backlog",
    )


def test_single_work_goal_planner_is_deterministic() -> None:
    target = registration()
    planner = SingleWorkGoalPlanner()

    first = planner.plan(target)
    second = planner.plan(target)

    assert first == second
    assert first.proposal_identity.startswith("plan:")
    assert first.works[0].logical_key == "goal-implementation"
    assert first.works[0].acceptance_criteria == target.acceptance_criteria


def test_validation_rejects_dependency_cycle() -> None:
    target = registration()
    works = (
        PlannedWork("a", "A", "A", ("A",), dependencies=("b",)),
        PlannedWork("b", "B", "B", ("B",), dependencies=("a",)),
    )
    proposal = WorkPlanProposal(
        proposal_identity=proposal_identity(target, works),
        goal_revision=target.goal_revision,
        works=works,
        completion_conditions=("done",),
    )

    with pytest.raises(PlanningValidationError, match="PLAN_DEPENDENCY_CYCLE"):
        validate_work_plan(target, proposal)


def test_validation_rejects_unknown_dependency() -> None:
    target = registration()
    works = (PlannedWork("a", "A", "A", ("A",), dependencies=("missing",)),)
    proposal = WorkPlanProposal(
        proposal_identity=proposal_identity(target, works),
        goal_revision=target.goal_revision,
        works=works,
        completion_conditions=("done",),
    )

    with pytest.raises(PlanningValidationError, match="PLAN_DEPENDENCY_INVALID"):
        validate_work_plan(target, proposal)


def test_validation_rejects_duplicate_logical_key() -> None:
    target = registration()
    works = (
        PlannedWork("same", "A", "A", ("A",)),
        PlannedWork("same", "B", "B", ("B",)),
    )
    proposal = WorkPlanProposal(
        proposal_identity=proposal_identity(target, works),
        goal_revision=target.goal_revision,
        works=works,
        completion_conditions=("done",),
    )

    with pytest.raises(PlanningValidationError, match="PLAN_LOGICAL_KEY_INVALID"):
        validate_work_plan(target, proposal)


def test_acceptance_digest_is_stable_and_order_sensitive() -> None:
    assert acceptance_digest(("a", "b")) == acceptance_digest(("a", "b"))
    assert acceptance_digest(("a", "b")) != acceptance_digest(("b", "a"))
