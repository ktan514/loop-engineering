"""V2のIssue / Project定義を本文解析なしで同期する接続層。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .v2_resume import WorkDefinitionResult, WorkDefinitionStatus
from .work_state import WorkRecord


class WorkDefinitionCommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str: ...


@dataclass(frozen=True, slots=True)
class WorkDefinitionSnapshot:
    repository: str
    issue_number: int
    issue_state: str
    acceptance_criteria_digest: str | None
    dependency_identities: tuple[str, ...]
    dependency_states: tuple[str, ...]
    project_status: str | None
    priority: str | None
    start_date: str | None
    target_date: str | None

    @property
    def revision(self) -> str:
        payload = json.dumps(
            {
                "issue": self.issue_number,
                "state": self.issue_state,
                "acceptance_criteria_digest": self.acceptance_criteria_digest,
                "dependencies": self.dependency_identities,
                "project_status": self.project_status,
                "priority": self.priority,
                "start_date": self.start_date,
                "target_date": self.target_date,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "definition:" + hashlib.sha256(payload.encode()).hexdigest()

    @property
    def blocking_reason(self) -> str | None:
        if self.issue_state == "CLOSED":
            return "WORK_CLOSED_BEFORE_COMPLETION"
        if any(state != "CLOSED" for state in self.dependency_states):
            return "DEPENDENCY_PENDING"
        return None


class GitHubWorkDefinitionAdapter:
    """対象IssueとそのProject項目だけを1回のGraphQL読取りで同期する。"""

    def __init__(self, runner: WorkDefinitionCommandRunner, project_number: int) -> None:
        self._runner = runner
        self._project_number = project_number

    def synchronize(self, current: WorkRecord) -> WorkDefinitionResult:
        snapshot = self._snapshot(current.repository, current.issue_number)
        if snapshot is None:
            return WorkDefinitionResult(WorkDefinitionStatus.UNAVAILABLE)
        if snapshot.issue_state == "CLOSED" and current.lifecycle != "COMPLETED":
            return WorkDefinitionResult(WorkDefinitionStatus.CLOSED_BEFORE_COMPLETION)
        if snapshot.issue_state == "CLOSED":
            return WorkDefinitionResult(WorkDefinitionStatus.COMPLETED, current)
        if snapshot.blocking_reason is not None:
            return WorkDefinitionResult(WorkDefinitionStatus.DEPENDENCY_PENDING)
        if snapshot.acceptance_criteria_digest is None:
            return WorkDefinitionResult(WorkDefinitionStatus.UNAVAILABLE)
        return WorkDefinitionResult(WorkDefinitionStatus.READY, WorkRecord(
            identity=current.identity,
            repository=current.repository,
            issue_number=current.issue_number,
            issue_revision=snapshot.revision,
            lifecycle=current.lifecycle,
            selected_transition=current.selected_transition,
            active_lineage_identity=current.active_lineage_identity,
            latest_task_packet_identity=current.latest_task_packet_identity,
            latest_checkpoint_identity=current.latest_checkpoint_identity,
        ))

    def _snapshot(self, repository: str, issue_number: int) -> WorkDefinitionSnapshot | None:
        if "/" not in repository or issue_number < 1 or self._project_number < 1:
            return None
        owner, name = repository.split("/", maxsplit=1)
        query = (
            "query($owner:String!,$name:String!,$issue:Int!){"
            "repository(owner:$owner,name:$name){issue(number:$issue){number state "
            "blockedBy(first:50){nodes{repository{nameWithOwner} number state}"
            "pageInfo{hasNextPage}} "
            "projectItems(first:20){nodes{project{number} fieldValues(first:20){nodes{"
            "... on ProjectV2ItemFieldSingleSelectValue{field{... on ProjectV2FieldCommon{"
            "name}}name}"
            "... on ProjectV2ItemFieldDateValue{field{... on ProjectV2FieldCommon{name}}date}"
            "... on ProjectV2ItemFieldTextValue{field{... on ProjectV2FieldCommon{name}}text}"
            "}pageInfo{hasNextPage}}}}}}}"
        )
        try:
            raw = self._runner.run(
                (
                    "gh", "api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}",
                    "-f", f"name={name}", "-F", f"issue={issue_number}",
                )
            )
            payload = json.loads(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        issue = _mapping(_mapping(_mapping(payload, "data"), "repository"), "issue")
        number = issue.get("number")
        state = issue.get("state")
        if number != issue_number or not isinstance(state, str):
            return None
        if state == "CLOSED":
            return WorkDefinitionSnapshot(
                repository, issue_number, state, None, (), (), None, None, None, None
            )
        values = _project_values(issue, self._project_number)
        if values is None:
            return None
        dependencies = _dependencies(issue)
        if dependencies is None:
            return None
        identities, states = dependencies
        return WorkDefinitionSnapshot(
            repository,
            issue_number,
            state,
            values.get("Acceptance criteria digest"),
            identities,
            states,
            values.get("Status"),
            values.get("Priority"),
            values.get("Start date"),
            values.get("Target date"),
        )


def _project_values(
    issue: Mapping[str, object], project_number: int
) -> dict[str, str | None] | None:
    items = _mapping(issue, "projectItems").get("nodes")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict) or _mapping(item, "project").get("number") != project_number:
            continue
        values: dict[str, str | None] = {}
        field_values = _mapping(item, "fieldValues")
        if _mapping(field_values, "pageInfo").get("hasNextPage") is True:
            return None
        nodes = field_values.get("nodes")
        if not isinstance(nodes, list):
            return None
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = _mapping(node, "field").get("name")
            value = node.get("name") if isinstance(node.get("name"), str) else node.get("date")
            if not isinstance(value, str):
                value = node.get("text")
            if isinstance(name, str) and (value is None or isinstance(value, str)):
                values[name] = value
        return values
    return None


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    return nested if isinstance(nested, dict) else {}


def _dependencies(issue: Mapping[str, object]) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    candidates: list[Mapping[str, object]] = []
    blocked_by = _mapping(issue, "blockedBy")
    if _mapping(blocked_by, "pageInfo").get("hasNextPage") is True:
        return None
    nodes = blocked_by.get("nodes")
    if isinstance(nodes, list):
        candidates.extend(node for node in nodes if isinstance(node, dict))
    identities: list[str] = []
    states: list[str] = []
    for candidate in candidates:
        number, state = candidate.get("number"), candidate.get("state")
        dependency_repository = _mapping(candidate, "repository").get("nameWithOwner")
        if isinstance(number, int) and isinstance(state, str):
            identities.append(
                f"issue:{dependency_repository}:{number}"
                if isinstance(dependency_repository, str)
                else f"issue:{number}"
            )
            states.append(state)
    return tuple(identities), tuple(states)
