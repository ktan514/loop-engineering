"""V2のIssue / Project定義を本文解析なしで同期する接続層。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .work_state import WorkRecord


class WorkDefinitionCommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str: ...


@dataclass(frozen=True, slots=True)
class WorkDefinitionSnapshot:
    repository: str
    issue_number: int
    issue_state: str
    issue_updated_at: str
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
                "updated_at": self.issue_updated_at,
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


class GitHubWorkDefinitionAdapter:
    """対象IssueとそのProject項目だけを1回のGraphQL読取りで同期する。"""

    def __init__(self, runner: WorkDefinitionCommandRunner, project_number: int) -> None:
        self._runner = runner
        self._project_number = project_number

    def synchronize(self, current: WorkRecord) -> WorkRecord | None:
        snapshot = self._snapshot(current.repository, current.issue_number)
        if snapshot is None or snapshot.issue_state != "OPEN":
            return None
        return WorkRecord(
            identity=current.identity,
            repository=current.repository,
            issue_number=current.issue_number,
            issue_revision=snapshot.revision,
            lifecycle=current.lifecycle,
            selected_transition=current.selected_transition,
            active_lineage_identity=current.active_lineage_identity,
            latest_task_packet_identity=current.latest_task_packet_identity,
            latest_checkpoint_identity=current.latest_checkpoint_identity,
        )

    def _snapshot(self, repository: str, issue_number: int) -> WorkDefinitionSnapshot | None:
        if "/" not in repository or issue_number < 1 or self._project_number < 1:
            return None
        owner, name = repository.split("/", maxsplit=1)
        query = (
            "query($owner:String!,$name:String!,$issue:Int!,$project:Int!){"
            "repository(owner:$owner,name:$name){issue(number:$issue){number state updatedAt "
            "projectItems(first:20){nodes{project{number} fieldValues(first:20){nodes{"
            "... on ProjectV2ItemFieldSingleSelectValue{field{... on ProjectV2FieldCommon{"
            "name}}name}"
            "... on ProjectV2ItemFieldDateValue{field{... on ProjectV2FieldCommon{name}}date}"
            "}}}}}}}"
        )
        try:
            raw = self._runner.run(
                (
                    "gh", "api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}",
                    "-f", f"name={name}", "-F", f"issue={issue_number}", "-F",
                    f"project={self._project_number}",
                )
            )
            payload = json.loads(raw)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        issue = _mapping(_mapping(_mapping(payload, "data"), "repository"), "issue")
        number = issue.get("number")
        state = issue.get("state")
        updated_at = issue.get("updatedAt")
        if number != issue_number or not isinstance(state, str) or not isinstance(updated_at, str):
            return None
        values = _project_values(issue, self._project_number)
        if values is None:
            return None
        return WorkDefinitionSnapshot(
            repository, issue_number, state, updated_at, values.get("Status"),
            values.get("Priority"), values.get("Start date"), values.get("Target date"),
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
        nodes = _mapping(item, "fieldValues").get("nodes")
        if not isinstance(nodes, list):
            return None
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = _mapping(node, "field").get("name")
            value = node.get("name") if isinstance(node.get("name"), str) else node.get("date")
            if isinstance(name, str) and (value is None or isinstance(value, str)):
                values[name] = value
        return values
    return None


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    return nested if isinstance(nested, dict) else {}
