import json
from pathlib import Path

import pytest

from loop_engineering.v2_goal_planning import ProductDevelopmentRegistration
from loop_engineering.v2_work_definition import WorkDefinitionSnapshot
from loop_engineering.v2_work_queue import GitHubV2WorkQueue, WorkQueueUnavailable
from loop_engineering.work_state import RecoveredWork, WorkRecord


class FakeRunner:
    def __init__(self, issues: list[dict[str, object]]) -> None:
        self.issues = issues

    def run(self, args: tuple[str, ...]) -> str:
        assert args[:3] == ("gh", "issue", "list")
        return json.dumps(self.issues)


class FakeDefinitions:
    def __init__(self, definitions: dict[int, WorkDefinitionSnapshot]) -> None:
        self.definitions = definitions

    def snapshot(self, repository: str, issue_number: int) -> WorkDefinitionSnapshot | None:
        result = self.definitions.get(issue_number)
        if result is not None:
            assert result.repository == repository
        return result


class FakeExecutionState:
    def __init__(self) -> None:
        self.records: dict[str, WorkRecord] = {}

    def work_record(self, work_identity: str) -> WorkRecord | None:
        return self.records.get(work_identity)

    def migrate_candidate(self, record: WorkRecord) -> bool:
        self.records.setdefault(record.identity, record)
        return True


class FakeWorkState:
    def __init__(self, execution: FakeExecutionState) -> None:
        self.execution = execution

    def upsert_work(self, record: WorkRecord) -> None:
        self.execution.records[record.identity] = record

    def recover(self, work_identity: str) -> RecoveredWork | None:
        record = self.execution.records.get(work_identity)
        return RecoveredWork(record, None, None, ()) if record is not None else None


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
        goal_text="Sample Goal",
        acceptance_criteria=("done",),
        work_branch_template="feature/work-{issue}",
        ci_workflow_name="CI",
        initial_project_status="Backlog",
    )


def definition(
    issue: int,
    *,
    state: str = "OPEN",
    dependencies: tuple[str, ...] = (),
    dependency_states: tuple[str, ...] = (),
) -> WorkDefinitionSnapshot:
    return WorkDefinitionSnapshot(
        repository="owner/sample",
        issue_number=issue,
        issue_state=state,
        acceptance_criteria_digest=f"digest-{issue}" if state == "OPEN" else None,
        dependency_identities=dependencies,
        dependency_states=dependency_states,
        project_status="Backlog",
        priority="P1",
        start_date=None,
        target_date=None,
    )


def test_synchronize_discovers_bootstrap_works_and_creates_work_records() -> None:
    marker1 = "<!-- loop-engineering-work:sample:rev-1:first -->"
    marker2 = "<!-- loop-engineering-work:sample:rev-1:second -->"
    runner = FakeRunner(
        [
            {"number": 1, "body": marker1},
            {"number": 2, "body": marker2},
            {"number": 3, "body": "unrelated"},
        ]
    )
    execution = FakeExecutionState()
    queue = GitHubV2WorkQueue(
        runner,
        FakeDefinitions({1: definition(1), 2: definition(2)}),
        execution,
        FakeWorkState(execution),
    )

    snapshot = queue.synchronize(registration())

    assert [item.issue_number for item in snapshot.works] == [1, 2]
    assert snapshot.current_work_identity is None
    assert set(execution.records) == {"work:owner/sample:1", "work:owner/sample:2"}


def test_synchronize_preserves_current_work_and_updates_issue_revision() -> None:
    marker = "<!-- loop-engineering-work:sample:rev-1:first -->"
    execution = FakeExecutionState()
    execution.records["work:owner/sample:1"] = WorkRecord(
        identity="work:owner/sample:1",
        repository="owner/sample",
        issue_number=1,
        issue_revision="old",
        lifecycle="RUNNING",
    )
    queue = GitHubV2WorkQueue(
        FakeRunner([{"number": 1, "body": marker}]),
        FakeDefinitions({1: definition(1)}),
        execution,
        FakeWorkState(execution),
    )

    snapshot = queue.synchronize(registration())

    assert snapshot.current_work_identity == "work:owner/sample:1"
    assert execution.records["work:owner/sample:1"].issue_revision == definition(1).revision


def test_multiple_current_works_fail_closed() -> None:
    execution = FakeExecutionState()
    for issue in (1, 2):
        execution.records[f"work:owner/sample:{issue}"] = WorkRecord(
            identity=f"work:owner/sample:{issue}",
            repository="owner/sample",
            issue_number=issue,
            issue_revision=f"old-{issue}",
            lifecycle="RUNNING",
        )
    queue = GitHubV2WorkQueue(
        FakeRunner(
            [
                {"number": 1, "body": "<!-- loop-engineering-work:sample:rev-1:first -->"},
                {"number": 2, "body": "<!-- loop-engineering-work:sample:rev-1:second -->"},
            ]
        ),
        FakeDefinitions({1: definition(1), 2: definition(2)}),
        execution,
        FakeWorkState(execution),
    )

    with pytest.raises(WorkQueueUnavailable, match="MULTIPLE_CURRENT_WORKS"):
        queue.synchronize(registration())


def test_closed_issue_with_noncompleted_db_state_is_conflict() -> None:
    marker = "<!-- loop-engineering-work:sample:rev-1:first -->"
    execution = FakeExecutionState()
    execution.records["work:owner/sample:1"] = WorkRecord(
        identity="work:owner/sample:1",
        repository="owner/sample",
        issue_number=1,
        issue_revision="old",
        lifecycle="RUNNING",
    )
    queue = GitHubV2WorkQueue(
        FakeRunner([{"number": 1, "body": marker}]),
        FakeDefinitions({1: definition(1, state="CLOSED")}),
        execution,
        FakeWorkState(execution),
    )

    snapshot = queue.synchronize(registration())

    assert snapshot.works[0].unresolved_conflict is True
