import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from loop_engineering.v2_bootstrap_state import BootstrapEffect
from loop_engineering.v2_goal_completion import (
    GitHubGoalCompletion,
    GoalCompletionStatus,
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


class MemoryState:
    def __init__(self) -> None:
        self.effects: dict[str, BootstrapEffect] = {}

    def ensure_intent(self, effect: BootstrapEffect) -> BootstrapEffect:
        self.effects.setdefault(effect.idempotency_key, effect)
        return self.effects[effect.idempotency_key]

    def get(self, idempotency_key: str) -> BootstrapEffect | None:
        return self.effects.get(idempotency_key)

    def record_outcome(self, idempotency_key: str, status: str) -> BootstrapEffect:
        current = self.effects[idempotency_key]
        updated = replace(current, status=status)
        self.effects[idempotency_key] = updated
        return updated


class FakeRunner:
    def __init__(self) -> None:
        self.issue_states = {1: "OPEN", 2: "CLOSED"}
        self.project_states = {"goal-item": "Backlog", "work-item": "Done"}
        self.mutations: list[tuple[str, ...]] = []

    def run(self, args: Sequence[str]) -> str:
        values = tuple(args)
        if values[:3] == ("gh", "issue", "view"):
            issue = int(values[3])
            return json.dumps({"number": issue, "state": self.issue_states[issue]})
        if values[:3] == ("gh", "issue", "close"):
            issue = int(values[3])
            self.issue_states[issue] = "CLOSED"
            self.mutations.append(values)
            return ""
        if values[:3] == ("gh", "project", "view"):
            return json.dumps({"id": "project-id"})
        if values[:3] == ("gh", "project", "item-edit"):
            item_id = values[values.index("--id") + 1]
            self.project_states[item_id] = "Done"
            self.mutations.append(values)
            return ""
        if values[:3] == ("gh", "api", "graphql"):
            query = values[values.index("-f") + 1]
            id_arg = next(item for item in values if item.startswith("id="))
            identity = id_arg.removeprefix("id=")
            if "fields(first:100)" in query:
                return json.dumps(
                    {
                        "data": {
                            "node": {
                                "fields": {
                                    "nodes": [
                                        {
                                            "id": "status-field",
                                            "name": "Status",
                                            "options": [
                                                {"id": "done-option", "name": "Done"}
                                            ],
                                        }
                                    ],
                                    "pageInfo": {"hasNextPage": False},
                                }
                            }
                        }
                    }
                )
            return json.dumps(
                {
                    "data": {
                        "node": {
                            "fieldValues": {
                                "nodes": [
                                    {
                                        "field": {"name": "Status"},
                                        "name": self.project_states[identity],
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            )
        raise AssertionError(values)


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
        goal_text="sample goal",
        acceptance_criteria=("done",),
        work_branch_template="feature/work-{issue}",
        ci_workflow_name="CI",
        initial_project_status="Backlog",
    )


def bootstrap() -> BootstrapResult:
    reg = registration()
    work = PlannedWork(
        "work",
        "Work",
        "do it",
        ("done",),
        canonical_design_targets=("docs/design.md",),
    )
    proposal = WorkPlanProposal(
        proposal_identity(reg, (work,)),
        reg.goal_revision,
        (work,),
        reg.acceptance_criteria,
    )
    return BootstrapResult(
        proposal,
        ProjectedPlan(
            1,
            "goal-url",
            "goal-item",
            (
                ProjectedWork(
                    "work",
                    2,
                    "work-url",
                    "work-item",
                    "digest",
                    (),
                ),
            ),
        ),
    )


def test_goal_completion_closes_goal_and_marks_project_done() -> None:
    runner = FakeRunner()
    state = MemoryState()

    result = GitHubGoalCompletion(runner, state).finalize(
        registration(),
        bootstrap(),
    )

    assert result.status is GoalCompletionStatus.PASS
    assert runner.issue_states[1] == "CLOSED"
    assert runner.project_states["goal-item"] == "Done"
    assert any(values[:3] == ("gh", "issue", "close") for values in runner.mutations)
    assert any(values[:3] == ("gh", "project", "item-edit") for values in runner.mutations)


def test_goal_completion_waits_until_work_project_is_done() -> None:
    runner = FakeRunner()
    runner.project_states["work-item"] = "In progress"

    result = GitHubGoalCompletion(runner, MemoryState()).finalize(
        registration(),
        bootstrap(),
    )

    assert result.status is GoalCompletionStatus.WAITING
    assert result.detail == "GOAL_WORK_PROJECT_NOT_COMPLETED"
    assert runner.mutations == []
