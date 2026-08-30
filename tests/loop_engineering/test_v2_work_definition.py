from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from loop_engineering.v2_resume import WorkDefinitionStatus
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
                {"field": {"name": "Acceptance criteria digest"}, "name": "sha256:abc"},
            ]}}]},
            "blockedBy": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        }}}}
    runner = Runner(json.dumps(payload), [])

    actual = GitHubWorkDefinitionAdapter(runner, 9).synchronize(record())

    assert actual.status is WorkDefinitionStatus.READY
    assert actual.record is not None
    assert actual.record.issue_revision.startswith("definition:")
    assert actual.record.identity == record().identity
    assert len(runner.calls) == 1
    command = " ".join(runner.calls[0])
    assert "projectItems" in command
    assert "body" not in command
    assert "comments" not in command


def test_closed_or_missing_project_is_unavailable() -> None:
    closed = {"data": {"repository": {"issue": {
        "number": 65, "state": "CLOSED", "updatedAt": "2026-08-31T00:00:00Z",
        "projectItems": {"nodes": []},
    }}}}
    adapter = GitHubWorkDefinitionAdapter(Runner(json.dumps(closed), []), 9)
    assert adapter.synchronize(record()).status is WorkDefinitionStatus.CLOSED_BEFORE_COMPLETION


def test_comment_updates_do_not_change_revision_and_open_dependency_waits() -> None:
    issue = {
        "number": 65, "state": "OPEN",
        "updatedAt": "comment-update-only", "parent": {"number": 62, "state": "CLOSED"},
        "blockedBy": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "projectItems": {"nodes": [{"project": {"number": 9}, "fieldValues": {"nodes": [
            {"field": {"name": "Acceptance criteria digest"}, "name": "sha256:abc"},
        ]}}]},
    }
    payload = {"data": {"repository": {"issue": issue}}}
    first = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9).synchronize(record())
    issue["updatedAt"] = "another-comment-update"
    second = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9).synchronize(record())
    assert first.record is not None and second.record is not None
    assert first.record.issue_revision == second.record.issue_revision

    issue["blockedBy"] = {
        "nodes": [{"number": 66, "state": "OPEN"}],
        "pageInfo": {"hasNextPage": False},
    }
    adapter = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9)
    assert adapter.synchronize(record()).status is WorkDefinitionStatus.DEPENDENCY_PENDING


def test_text_digest_completed_closed_and_truncated_dependencies_are_safe() -> None:
    issue = {
        "number": 65,
        "state": "OPEN",
        "blockedBy": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "projectItems": {"nodes": [{"project": {"number": 9}, "fieldValues": {"nodes": [
            {"field": {"name": "Acceptance criteria digest"}, "text": "digest:65"},
        ]}}]},
    }
    payload = {"data": {"repository": {"issue": issue}}}
    result = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9).synchronize(record())
    assert result.status is WorkDefinitionStatus.READY

    issue["state"] = "CLOSED"
    completed = WorkRecord("work:repo:65", "ktan514/loop-engineering", 65, "old", "COMPLETED")
    adapter = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9)
    assert adapter.synchronize(completed).status is WorkDefinitionStatus.COMPLETED

    issue["state"] = "OPEN"
    issue["blockedBy"] = {"nodes": [], "pageInfo": {"hasNextPage": True}}
    adapter = GitHubWorkDefinitionAdapter(Runner(json.dumps(payload), []), 9)
    assert adapter.synchronize(record()).status is WorkDefinitionStatus.UNAVAILABLE

    issue.pop("blockedBy")
    assert adapter.synchronize(record()).status is WorkDefinitionStatus.UNAVAILABLE
