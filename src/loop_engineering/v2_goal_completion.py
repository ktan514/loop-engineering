"""Goal配下Work完了後にGoal Issue / Projectを安全に完了する。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .v2_bootstrap_state import BootstrapEffect
from .v2_goal_planning import BootstrapResult, ProductDevelopmentRegistration


class GoalCompletionStatus(str, Enum):
    PASS = "PASS"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class GoalCompletionResult:
    status: GoalCompletionStatus
    detail: str


class GoalCompletionCommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str: ...


class GoalCompletionStatePort(Protocol):
    def ensure_intent(self, effect: BootstrapEffect) -> BootstrapEffect: ...

    def get(self, idempotency_key: str) -> BootstrapEffect | None: ...

    def record_outcome(self, idempotency_key: str, status: str) -> BootstrapEffect: ...


class GitHubGoalCompletion:
    """typed live readback後にだけGoalをclose / Doneへ投影する。"""

    def __init__(
        self,
        runner: GoalCompletionCommandRunner,
        state: GoalCompletionStatePort,
        *,
        done_status: str = "Done",
    ) -> None:
        if not done_status.strip():
            raise ValueError("GOAL_DONE_STATUS_INVALID")
        self._runner = runner
        self._state = state
        self._done_status = done_status

    def finalize(
        self,
        registration: ProductDevelopmentRegistration,
        bootstrap: BootstrapResult,
    ) -> GoalCompletionResult:
        for item in bootstrap.projection.works:
            if self._issue_state(registration.repository_identity, item.issue_number) != "CLOSED":
                return GoalCompletionResult(
                    GoalCompletionStatus.WAITING,
                    "GOAL_WORK_ISSUE_NOT_COMPLETED",
                )
            if self._project_item_status(item.project_item_id) != self._done_status:
                return GoalCompletionResult(
                    GoalCompletionStatus.WAITING,
                    "GOAL_WORK_PROJECT_NOT_COMPLETED",
                )

        goal_issue = bootstrap.projection.goal_issue
        state = self._issue_state(registration.repository_identity, goal_issue)
        if state is None:
            return GoalCompletionResult(
                GoalCompletionStatus.WAITING,
                "GOAL_ISSUE_READBACK_UNAVAILABLE",
            )
        if state == "OPEN":
            result = self._close_goal_issue(registration, goal_issue)
            if result is not None:
                return result
        elif state != "CLOSED":
            return GoalCompletionResult(
                GoalCompletionStatus.BLOCKED,
                "GOAL_ISSUE_STATE_INVALID",
            )

        project_result = self._complete_goal_project_item(
            registration,
            bootstrap.projection.goal_project_item_id,
        )
        if project_result is not None:
            return project_result

        if self._issue_state(registration.repository_identity, goal_issue) != "CLOSED":
            return GoalCompletionResult(
                GoalCompletionStatus.WAITING,
                "GOAL_ISSUE_FINAL_READBACK_UNAVAILABLE",
            )
        if (
            self._project_item_status(bootstrap.projection.goal_project_item_id)
            != self._done_status
        ):
            return GoalCompletionResult(
                GoalCompletionStatus.WAITING,
                "GOAL_PROJECT_FINAL_READBACK_UNAVAILABLE",
            )
        return GoalCompletionResult(GoalCompletionStatus.PASS, "GOAL_EFFECTS_CONFIRMED")

    def _close_goal_issue(
        self,
        registration: ProductDevelopmentRegistration,
        issue_number: int,
    ) -> GoalCompletionResult | None:
        target = f"issue:{issue_number}"
        effect = self._next_effect(
            registration,
            "GOAL_ISSUE_CLOSE",
            target,
            (("state", "OPEN"),),
            (("state", "CLOSED"),),
        )
        if effect.status == "CONFIRMED":
            return None
        if effect.status == "UNCERTAIN":
            if self._issue_state(registration.repository_identity, issue_number) == "CLOSED":
                self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
                return None
            return GoalCompletionResult(
                GoalCompletionStatus.BLOCKED,
                "GOAL_ISSUE_CLOSE_UNCERTAIN",
            )
        try:
            self._runner.run(
                (
                    "gh",
                    "issue",
                    "close",
                    str(issue_number),
                    "--repo",
                    registration.repository_identity,
                )
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            if self._issue_state(registration.repository_identity, issue_number) == "CLOSED":
                self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
                return None
            self._state.record_outcome(effect.idempotency_key, "NO_EFFECT")
            return GoalCompletionResult(
                GoalCompletionStatus.WAITING,
                "GOAL_ISSUE_CLOSE_NO_EFFECT",
            )
        if self._issue_state(registration.repository_identity, issue_number) == "CLOSED":
            self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
            return None
        self._state.record_outcome(effect.idempotency_key, "UNCERTAIN")
        return GoalCompletionResult(
            GoalCompletionStatus.BLOCKED,
            "GOAL_ISSUE_CLOSE_READBACK_UNPROVEN",
        )

    def _complete_goal_project_item(
        self,
        registration: ProductDevelopmentRegistration,
        item_id: str,
    ) -> GoalCompletionResult | None:
        current = self._project_item_status(item_id)
        if current == self._done_status:
            return None
        project_id = self._project_id(registration)
        status_field = self._status_field(project_id)
        if project_id is None or status_field is None:
            return GoalCompletionResult(
                GoalCompletionStatus.BLOCKED,
                "GOAL_PROJECT_STATUS_AUTHORITY_UNAVAILABLE",
            )
        field_id, option_id = status_field
        effect = self._next_effect(
            registration,
            "GOAL_PROJECT_DONE",
            f"project-item:{item_id}:Status",
            (("value", current or "<unset>"),),
            (("value", self._done_status),),
        )
        if effect.status == "CONFIRMED":
            return None
        if effect.status == "UNCERTAIN":
            if self._project_item_status(item_id) == self._done_status:
                self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
                return None
            return GoalCompletionResult(
                GoalCompletionStatus.BLOCKED,
                "GOAL_PROJECT_UPDATE_UNCERTAIN",
            )
        try:
            self._runner.run(
                (
                    "gh",
                    "project",
                    "item-edit",
                    "--id",
                    item_id,
                    "--project-id",
                    project_id,
                    "--field-id",
                    field_id,
                    "--single-select-option-id",
                    option_id,
                )
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            if self._project_item_status(item_id) == self._done_status:
                self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
                return None
            self._state.record_outcome(effect.idempotency_key, "NO_EFFECT")
            return GoalCompletionResult(
                GoalCompletionStatus.WAITING,
                "GOAL_PROJECT_UPDATE_NO_EFFECT",
            )
        if self._project_item_status(item_id) == self._done_status:
            self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
            return None
        self._state.record_outcome(effect.idempotency_key, "UNCERTAIN")
        return GoalCompletionResult(
            GoalCompletionStatus.BLOCKED,
            "GOAL_PROJECT_UPDATE_READBACK_UNPROVEN",
        )

    def _next_effect(
        self,
        registration: ProductDevelopmentRegistration,
        kind: str,
        target_identity: str,
        before: tuple[tuple[str, str], ...],
        after: tuple[tuple[str, str], ...],
    ) -> BootstrapEffect:
        for generation in range(1, 65):
            key = _goal_effect_key(
                registration,
                kind,
                target_identity,
                generation,
            )
            existing = self._state.get(key)
            if existing is None:
                return self._state.ensure_intent(
                    BootstrapEffect(
                        idempotency_key=key,
                        product_key=registration.product_key,
                        repository=registration.repository_identity,
                        goal_revision=registration.goal_revision,
                        kind=kind,
                        target_identity=target_identity,
                        expected_preconditions=before,
                        expected_effect=after,
                    )
                )
            if (
                existing.kind != kind
                or existing.target_identity != target_identity
                or existing.expected_preconditions != before
                or existing.expected_effect != after
            ):
                raise RuntimeError("GOAL_COMPLETION_EFFECT_CONFLICT")
            if existing.status != "NO_EFFECT":
                return existing
        raise RuntimeError("GOAL_COMPLETION_EFFECT_GENERATION_EXHAUSTED")

    def _issue_state(self, repository: str, issue_number: int) -> str | None:
        raw = self._json(
            (
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repository,
                "--json",
                "number,state",
            )
        )
        if not isinstance(raw, dict) or raw.get("number") != issue_number:
            return None
        state = raw.get("state")
        return state if isinstance(state, str) else None

    def _project_id(
        self,
        registration: ProductDevelopmentRegistration,
    ) -> str | None:
        raw = self._json(
            (
                "gh",
                "project",
                "view",
                str(registration.project_number),
                "--owner",
                registration.project_owner,
                "--format",
                "json",
            )
        )
        value = raw.get("id") if isinstance(raw, dict) else None
        return value if isinstance(value, str) else None

    def _status_field(self, project_id: str | None) -> tuple[str, str] | None:
        if project_id is None:
            return None
        query = (
            "query($id:ID!){node(id:$id){... on ProjectV2{fields(first:100){nodes{"
            "... on ProjectV2SingleSelectField{id name options{id name}}}"
            "pageInfo{hasNextPage}}}}}"
        )
        raw = self._json(
            (
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"id={project_id}",
            )
        )
        fields = _nested(raw, "data", "node", "fields")
        if _has_next_page(fields):
            return None
        nodes = fields.get("nodes")
        if not isinstance(nodes, list):
            return None
        for item in nodes:
            if not isinstance(item, dict) or item.get("name") != "Status":
                continue
            field_id = item.get("id")
            options = item.get("options")
            if not isinstance(field_id, str) or not isinstance(options, list):
                return None
            for option in options:
                if not isinstance(option, dict) or option.get("name") != self._done_status:
                    continue
                option_id = option.get("id")
                if isinstance(option_id, str):
                    return field_id, option_id
        return None

    def _project_item_status(self, item_id: str) -> str | None:
        query = (
            "query($id:ID!){node(id:$id){... on ProjectV2Item{"
            "fieldValues(first:100){nodes{... on ProjectV2ItemFieldSingleSelectValue{"
            "field{... on ProjectV2FieldCommon{name}} name}}pageInfo{hasNextPage}}}}}"
        )
        raw = self._json(
            (
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"id={item_id}",
            )
        )
        values = _nested(raw, "data", "node", "fieldValues")
        if _has_next_page(values):
            return None
        nodes = values.get("nodes")
        if not isinstance(nodes, list):
            return None
        for item in nodes:
            if not isinstance(item, dict):
                continue
            if _nested(item, "field").get("name") != "Status":
                continue
            value = item.get("name")
            return value if isinstance(value, str) else None
        return None

    def _json(self, command: Sequence[str]) -> object:
        try:
            raw = self._runner.run(command)
            return json.loads(raw)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return None


def _goal_effect_key(
    registration: ProductDevelopmentRegistration,
    kind: str,
    target_identity: str,
    generation: int,
) -> str:
    raw = (
        f"{registration.repository_identity}|{registration.product_key}|"
        f"{registration.goal_revision}|{kind}|{target_identity}|{generation}"
    )
    return "goal-completion:" + hashlib.sha256(raw.encode()).hexdigest()


def _nested(raw: object, *keys: str) -> Mapping[str, object]:
    current = raw
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _has_next_page(value: Mapping[str, object]) -> bool:
    page_info = value.get("pageInfo")
    return isinstance(page_info, dict) and page_info.get("hasNextPage") is True
