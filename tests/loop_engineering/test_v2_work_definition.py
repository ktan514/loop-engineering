from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from loop_engineering.v2_work_definition import GitHubWorkDefinitionAdapter
from loop_engineering.work_state import WorkRecord


@dataclass
class Runner:
    output: str
    calls: list[Sequence[str]]

    def run(self, args: Sequence[str]) -> str:
        self.calls.append(args)
        return self.output


def record() -> WorkRecord:
    return WorkRecord("work:repo:65", "ktan514/loop-engineering", 65, "old", "SELECTED")


def test_synchronizes_only_typed_issue_and_project_fields() -> None:
    payload = {
        "data": {"repository": {"issue": {
            "number": 65, "state": "OPEN", "updatedAt": "2026-08-31T00:00:00Z",
            "body": "この本文は読まない",
            "projectItems": {"nodes": [{"project": {"number": 9}, "fieldValues": {"nodes": [
                {"field": {"name": "Status"}, "name": "In Progress"},
                {"field": {"name": "Priority"}, "name": "P1"},
                {"field": {"name": "Start date"}, "date": "2026-09-02"},
            ]}}]},
        }}}}
    runner = Runner(json.dumps(payload), [])

    actual = GitHubWorkDefinitionAdapter(runner, 9).synchronize(record())

    assert actual is not None
    assert actual.issue_revision.startswith("definition:")
    assert actual.identity == record().identity
    assert len(runner.calls) == 1
    command = " ".join(runner.calls[0])
    assert "projectItems" in command
    assert " body " in command
    assert "comments" not in command


def test_closed_or_missing_project_is_unavailable() -> None:
    closed = {"data": {"repository": {"issue": {
        "number": 65, "state": "CLOSED", "updatedAt": "2026-08-31T00:00:00Z",
        "projectItems": {"nodes": []},
    }}}}
    adapter = GitHubWorkDefinitionAdapter(Runner(json.dumps(closed), []), 9)
    assert adapter.synchronize(record()) is None


def test_comment_updates_do_not_change_revision_and_open_dependency_waits() -> None:
    issue = {
        "number": 65, "state": "OPEN", "body": "## 受入条件\n\n- 同期する",
        "updatedAt": "comment-update-only", "parent": {"number": 62, "state": "CLOSED"},
        "trackedIssues": {"nodes": []},
        "projectItems": {"nodes": [{"project": {"number": 9}, "fieldValues": {"nodes": []}}]},
    }
    payload = {"data": {"repository": {"issue": issue}}}
    first = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9).synchronize(record())
    issue["updatedAt"] = "another-comment-update"
    second = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9).synchronize(record())
    assert first is not None and second is not None
    assert first.issue_revision == second.issue_revision

    issue["trackedIssues"] = {"nodes": [{"number": 66, "state": "OPEN"}]}
    adapter = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9)
    assert adapter.synchronize(record()) is None
