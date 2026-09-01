import json
import subprocess
from dataclasses import replace
from pathlib import Path
from collections.abc import Sequence

import pytest

from loop_engineering.v2_bootstrap_state import BootstrapEffect
from loop_engineering.v2_goal_planning import (
    ProductDevelopmentRegistration,
    SingleWorkGoalPlanner,
    V2GoalBootstrapService,
)
from loop_engineering.v2_planning_projection import (
    GitHubPlanningProjectionAdapter,
    PlanningProjectionError,
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


class FakeGitHubRunner:
    def __init__(self, *, fail_first_issue_response: bool = False) -> None:
        self.issues: list[dict[str, object]] = []
        self.items: list[dict[str, object]] = []
        self.item_fields: dict[str, dict[str, str]] = {}
        self.next_issue = 1
        self.next_item = 1
        self.fail_first_issue_response = fail_first_issue_response
        self.issue_create_calls = 0
        self.field_mode = "complete"

    def run(self, args: Sequence[str]) -> str:
        command = tuple(args)
        if command[:3] == ("gh", "issue", "list"):
            return json.dumps(self.issues)
        if command[:3] == ("gh", "api", "repos/owner/sample/issues"):
            self.issue_create_calls += 1
            title = _raw_field(command, "title")
            body = _raw_field(command, "body")
            number = self.next_issue
            self.next_issue += 1
            issue = {
                "number": number,
                "title": title,
                "body": body,
                "url": f"https://github.com/owner/sample/issues/{number}",
            }
            self.issues.append(issue)
            if self.fail_first_issue_response:
                self.fail_first_issue_response = False
                raise subprocess.CalledProcessError(1, command)
            return json.dumps(issue)
        if command[:3] == ("gh", "project", "view"):
            return json.dumps({"id": "PROJECT"})
        if command[:3] == ("gh", "project", "item-add"):
            issue_url = command[command.index("--url") + 1]
            item = {"id": f"ITEM{self.next_item}", "issue_url": issue_url}
            self.next_item += 1
            self.items.append(item)
            self.item_fields[item["id"]] = {}
            return json.dumps({"id": item["id"]})
        if command[:3] == ("gh", "project", "item-edit"):
            item_id = command[command.index("--id") + 1]
            field_id = command[command.index("--field-id") + 1]
            if "--text" in command:
                value = command[command.index("--text") + 1]
                name = "Acceptance criteria digest" if field_id == "DIGEST" else field_id
            else:
                option = command[command.index("--single-select-option-id") + 1]
                value = "Backlog" if option == "BACKLOG" else option
                name = "Status" if field_id == "STATUS" else field_id
            self.item_fields[item_id][name] = value
            return "{}"
        if command[:3] == ("gh", "api", "graphql"):
            query = command[command.index("-f") + 1]
            if "fields(first:100)" in query:
                nodes: list[dict[str, object]] = [
                    {
                        "id": "STATUS",
                        "name": "Status",
                        "options": [{"id": "BACKLOG", "name": "Backlog"}],
                    }
                ]
                if self.field_mode == "complete":
                    nodes.append(
                        {
                            "id": "DIGEST",
                            "name": "Acceptance criteria digest",
                            "dataType": "TEXT",
                        }
                    )
                return _graphql({"fields": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}})
            if "items(first:100)" in query:
                nodes = [
                    {"id": item["id"], "content": {"url": item["issue_url"]}}
                    for item in self.items
                ]
                return _graphql({"items": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}})
            if "fieldValues(first:100)" in query:
                item_id = command[-1].split("=", 1)[1]
                nodes = []
                for name, value in self.item_fields[item_id].items():
                    if name == "Status":
                        nodes.append({"name": value, "field": {"name": name}})
                    else:
                        nodes.append({"text": value, "field": {"name": name}})
                return json.dumps(
                    {
                        "data": {
                            "node": {
                                "fieldValues": {
                                    "nodes": nodes,
                                    "pageInfo": {"hasNextPage": False},
                                }
                            }
                        }
                    }
                )
        raise AssertionError(f"unexpected command: {command}")


def _graphql(value: dict[str, object]) -> str:
    return json.dumps({"data": {"node": value}})


def _raw_field(command: tuple[str, ...], key: str) -> str:
    prefix = f"{key}="
    for index, value in enumerate(command):
        if value == "--raw-field" and command[index + 1].startswith(prefix):
            return command[index + 1][len(prefix) :]
    raise AssertionError(key)


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


def test_bootstrap_repeated_run_converges_to_same_issues_and_items() -> None:
    runner = FakeGitHubRunner()
    state = MemoryState()
    service = V2GoalBootstrapService(
        SingleWorkGoalPlanner(), GitHubPlanningProjectionAdapter(runner, state)
    )

    first = service.bootstrap(registration())
    second = service.bootstrap(registration())

    assert first.projection == second.projection
    assert len(runner.issues) == 2
    assert len(runner.items) == 2
    assert first.projection.works[0].acceptance_digest == runner.item_fields["ITEM2"][
        "Acceptance criteria digest"
    ]
    assert runner.item_fields["ITEM2"]["Status"] == "Backlog"


def test_issue_create_response_failure_is_reconciled_by_marker_readback() -> None:
    runner = FakeGitHubRunner(fail_first_issue_response=True)
    state = MemoryState()
    service = V2GoalBootstrapService(
        SingleWorkGoalPlanner(), GitHubPlanningProjectionAdapter(runner, state)
    )

    result = service.bootstrap(registration())

    assert result.projection.goal_issue_number == 1
    assert len(runner.issues) == 2
    assert runner.issue_create_calls == 2
    assert any(effect.status == "CONFIRMED" for effect in state.effects.values())


def test_missing_acceptance_digest_field_fails_before_issue_mutation() -> None:
    runner = FakeGitHubRunner()
    runner.field_mode = "status-only"
    state = MemoryState()
    service = V2GoalBootstrapService(
        SingleWorkGoalPlanner(), GitHubPlanningProjectionAdapter(runner, state)
    )

    with pytest.raises(PlanningProjectionError, match="PROJECT_FIELD_REQUIRED"):
        service.bootstrap(registration())

    assert runner.issues == []
    assert state.effects == {}


def test_duplicate_goal_marker_fails_closed() -> None:
    runner = FakeGitHubRunner()
    marker = "<!-- loop-engineering-goal:sample:rev-1 -->"
    runner.issues = [
        {"number": 1, "title": "A", "body": marker, "url": "https://x/1"},
        {"number": 2, "title": "B", "body": marker, "url": "https://x/2"},
    ]
    state = MemoryState()
    service = V2GoalBootstrapService(
        SingleWorkGoalPlanner(), GitHubPlanningProjectionAdapter(runner, state)
    )

    with pytest.raises(PlanningProjectionError, match="ISSUE_MARKER_CONFLICT"):
        service.bootstrap(registration())
