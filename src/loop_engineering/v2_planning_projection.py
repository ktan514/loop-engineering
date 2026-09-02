"""検証済みGoal PlanをGitHub Issue / Projectへ冪等に投影する。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from .v2_bootstrap_state import BootstrapEffect
from .v2_goal_planning import (
    PlannedWork,
    ProductDevelopmentRegistration,
    ProjectedPlan,
    ProjectedWork,
    WorkPlanProposal,
    acceptance_digest,
    goal_marker,
    work_marker,
)

T = TypeVar("T")


class PlanningProjectionError(RuntimeError):
    """GitHub Planning Authorityを安全に確立できない。"""


class PlanningCommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str: ...


class BootstrapEffectStatePort(Protocol):
    def ensure_intent(self, effect: BootstrapEffect) -> BootstrapEffect: ...

    def get(self, idempotency_key: str) -> BootstrapEffect | None: ...

    def record_outcome(self, idempotency_key: str, status: str) -> BootstrapEffect: ...


@dataclass(frozen=True, slots=True)
class _Issue:
    number: int
    url: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class _ProjectItem:
    item_id: str
    issue_url: str


@dataclass(frozen=True, slots=True)
class _ProjectField:
    field_id: str
    name: str
    kind: str
    options: tuple[tuple[str, str], ...] = ()


class GitHubPlanningProjectionAdapter:
    """bootstrap mutationを専用DB intentとreadbackで保護するGitHub adapter。"""

    def __init__(self, runner: PlanningCommandRunner, state: BootstrapEffectStatePort) -> None:
        self._runner = runner
        self._state = state

    def ensure_plan(
        self,
        registration: ProductDevelopmentRegistration,
        proposal: WorkPlanProposal,
    ) -> ProjectedPlan:
        project_id = self._project_id(registration)
        fields = self._project_fields(project_id)
        digest_field = _required_field(fields, "Acceptance criteria digest", "TEXT")
        status_field = _required_field(fields, "Status", "SINGLE_SELECT")
        status_option = _required_option(status_field, registration.initial_project_status)

        goal_issue = self._ensure_issue(
            registration,
            role="goal",
            marker=goal_marker(registration),
            title=f"Goal: {_title(registration.goal_text)}",
            body=_goal_body(registration, proposal),
        )
        goal_item = self._ensure_project_item(
            registration, project_id, goal_issue.url, role="goal"
        )
        self._ensure_field(
            registration,
            project_id,
            goal_item.item_id,
            status_field,
            registration.initial_project_status,
            role="goal-status",
            option_id=status_option,
        )

        works: list[ProjectedWork] = []
        for work in proposal.works:
            issue = self._ensure_issue(
                registration,
                role=f"work:{work.logical_key}",
                marker=work_marker(registration, work.logical_key),
                title=work.title,
                body=_work_body(goal_issue.number, registration, work),
            )
            item = self._ensure_project_item(
                registration,
                project_id,
                issue.url,
                role=f"work:{work.logical_key}",
            )
            digest = acceptance_digest(work.acceptance_criteria)
            self._ensure_field(
                registration,
                project_id,
                item.item_id,
                digest_field,
                digest,
                role=f"work:{work.logical_key}:acceptance",
            )
            self._ensure_field(
                registration,
                project_id,
                item.item_id,
                status_field,
                registration.initial_project_status,
                role=f"work:{work.logical_key}:status",
                option_id=status_option,
            )
            works.append(
                ProjectedWork(
                    work.logical_key,
                    issue.number,
                    issue.url,
                    item.item_id,
                    digest,
                    work.dependencies,
                )
            )
        return ProjectedPlan(
            goal_issue.number,
            goal_issue.url,
            goal_item.item_id,
            tuple(works),
        )

    def _ensure_issue(
        self,
        registration: ProductDevelopmentRegistration,
        *,
        role: str,
        marker: str,
        title: str,
        body: str,
    ) -> _Issue:
        def readback() -> _Issue | None:
            return self._find_issue(registration.repository_identity, marker)

        def mutate() -> None:
            self._run_json(
                (
                    "gh",
                    "api",
                    f"repos/{registration.repository_identity}/issues",
                    "--method",
                    "POST",
                    "--raw-field",
                    f"title={title}",
                    "--raw-field",
                    f"body={body}",
                )
            )

        return self._ensure_created(
            registration,
            kind="ISSUE_CREATE",
            role=role,
            target_identity=f"issue-marker:{marker}",
            expected_effect=(("marker", marker),),
            readback=readback,
            mutate=mutate,
        )

    def _ensure_project_item(
        self,
        registration: ProductDevelopmentRegistration,
        project_id: str,
        issue_url: str,
        *,
        role: str,
    ) -> _ProjectItem:
        def readback() -> _ProjectItem | None:
            return self._find_project_item(project_id, issue_url)

        def mutate() -> None:
            self._run_json(
                (
                    "gh",
                    "project",
                    "item-add",
                    str(registration.project_number),
                    "--owner",
                    registration.project_owner,
                    "--url",
                    issue_url,
                    "--format",
                    "json",
                )
            )

        return self._ensure_created(
            registration,
            kind="PROJECT_ITEM_ADD",
            role=role,
            target_identity=f"project:{project_id}:issue:{issue_url}",
            expected_effect=(("issue_url", issue_url),),
            readback=readback,
            mutate=mutate,
        )

    def _ensure_created(
        self,
        registration: ProductDevelopmentRegistration,
        *,
        kind: str,
        role: str,
        target_identity: str,
        expected_effect: tuple[tuple[str, str], ...],
        readback: Callable[[], T | None],
        mutate: Callable[[], None],
    ) -> T:
        observed = readback()
        if observed is not None:
            return observed
        effect = self._prepare_effect(
            registration,
            kind=kind,
            role=role,
            target_identity=target_identity,
            expected_preconditions=(("present", "false"),),
            expected_effect=expected_effect,
        )
        if effect.status == "CONFIRMED":
            confirmed = readback()
            if confirmed is None:
                raise PlanningProjectionError("CONFIRMED_EFFECT_MISSING")
            return confirmed
        if effect.status == "UNCERTAIN":
            reconciled = readback()
            if reconciled is None:
                raise PlanningProjectionError("BOOTSTRAP_EFFECT_UNCERTAIN")
            self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
            return reconciled

        reconciled = readback()
        if reconciled is not None:
            self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
            return reconciled
        try:
            mutate()
        except PlanningProjectionError:
            reconciled = readback()
            if reconciled is not None:
                self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
                return reconciled
            self._state.record_outcome(effect.idempotency_key, "NO_EFFECT")
            raise
        reconciled = readback()
        if reconciled is None:
            self._state.record_outcome(effect.idempotency_key, "UNCERTAIN")
            raise PlanningProjectionError("BOOTSTRAP_CREATE_READBACK_UNPROVEN")
        self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
        return reconciled

    def _ensure_field(
        self,
        registration: ProductDevelopmentRegistration,
        project_id: str,
        item_id: str,
        field: _ProjectField,
        value: str,
        *,
        role: str,
        option_id: str | None = None,
    ) -> None:
        observed = self._item_fields(item_id)
        current = observed.get(field.name)
        if current == value:
            return
        before = current or "<unset>"
        effect = self._prepare_effect(
            registration,
            kind="PROJECT_FIELD_UPDATE",
            role=role,
            target_identity=f"project-item:{item_id}:field:{field.name}",
            expected_preconditions=(("value", before),),
            expected_effect=(("value", value),),
        )
        if effect.status == "CONFIRMED":
            if self._item_fields(item_id).get(field.name) != value:
                raise PlanningProjectionError("CONFIRMED_FIELD_MISMATCH")
            return
        if effect.status == "UNCERTAIN":
            if self._item_fields(item_id).get(field.name) == value:
                self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
                return
            raise PlanningProjectionError("PROJECT_FIELD_UPDATE_UNCERTAIN")

        fresh = self._item_fields(item_id).get(field.name) or "<unset>"
        if fresh != before:
            if fresh == value:
                self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
                return
            self._state.record_outcome(effect.idempotency_key, "NO_EFFECT")
            raise PlanningProjectionError("PROJECT_FIELD_STALE_PRECONDITION")

        command = [
            "gh",
            "project",
            "item-edit",
            "--id",
            item_id,
            "--project-id",
            project_id,
            "--field-id",
            field.field_id,
        ]
        if field.kind == "TEXT":
            command.extend(("--text", value))
        elif field.kind == "SINGLE_SELECT" and option_id is not None:
            command.extend(("--single-select-option-id", option_id))
        else:
            self._state.record_outcome(effect.idempotency_key, "NO_EFFECT")
            raise PlanningProjectionError("PROJECT_FIELD_KIND_UNSUPPORTED")
        try:
            self._run_text(tuple(command))
        except PlanningProjectionError:
            if self._item_fields(item_id).get(field.name) == value:
                self._state.record_outcome(effect.idempotency_key, "CONFIRMED")
                return
            self._state.record_outcome(effect.idempotency_key, "NO_EFFECT")
            raise
        if self._item_fields(item_id).get(field.name) != value:
            self._state.record_outcome(effect.idempotency_key, "UNCERTAIN")
            raise PlanningProjectionError("PROJECT_FIELD_UPDATE_READBACK_UNPROVEN")
        self._state.record_outcome(effect.idempotency_key, "CONFIRMED")

    def _prepare_effect(
        self,
        registration: ProductDevelopmentRegistration,
        *,
        kind: str,
        role: str,
        target_identity: str,
        expected_preconditions: tuple[tuple[str, str], ...],
        expected_effect: tuple[tuple[str, str], ...],
    ) -> BootstrapEffect:
        for generation in range(1, 65):
            key = _effect_key(registration, kind, role, generation)
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
                        expected_preconditions=expected_preconditions,
                        expected_effect=expected_effect,
                    )
                )
            if (
                existing.kind != kind
                or existing.target_identity != target_identity
                or existing.expected_effect != expected_effect
            ):
                raise PlanningProjectionError("BOOTSTRAP_EFFECT_IDENTITY_CONFLICT")
            if existing.status != "NO_EFFECT":
                return existing
        raise PlanningProjectionError("BOOTSTRAP_EFFECT_GENERATION_EXHAUSTED")

    def _find_issue(self, repository: str, marker: str) -> _Issue | None:
        payload = self._run_json(
            (
                "gh",
                "issue",
                "list",
                "--repo",
                repository,
                "--state",
                "all",
                "--limit",
                "1000",
                "--json",
                "number,title,body,url",
            )
        )
        if not isinstance(payload, list):
            raise PlanningProjectionError("ISSUE_LIST_INVALID")
        matches: list[_Issue] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            body = raw.get("body")
            title = raw.get("title")
            url = raw.get("url")
            number = raw.get("number")
            if (
                isinstance(body, str)
                and marker in body
                and isinstance(title, str)
                and isinstance(url, str)
                and isinstance(number, int)
            ):
                matches.append(_Issue(number, url, title, body))
        if len(matches) > 1:
            raise PlanningProjectionError("ISSUE_MARKER_CONFLICT")
        return matches[0] if matches else None

    def _project_id(self, registration: ProductDevelopmentRegistration) -> str:
        raw = self._run_json(
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
        if not isinstance(raw, dict):
            raise PlanningProjectionError("PROJECT_ID_UNAVAILABLE")
        project_id = raw.get("id")
        if not isinstance(project_id, str) or not project_id:
            raise PlanningProjectionError("PROJECT_ID_UNAVAILABLE")
        return project_id

    def _project_fields(self, project_id: str) -> tuple[_ProjectField, ...]:
        query = (
            "query($project:ID!){node(id:$project){... on ProjectV2{fields(first:100){"
            "nodes{... on ProjectV2Field{id name dataType} "
            "... on ProjectV2SingleSelectField{id name options{id name}}} "
            "pageInfo{hasNextPage}}}}}"
        )
        raw = self._run_json(
            (
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"project={project_id}",
            )
        )
        fields = _nested(raw, "data", "node", "fields")
        if _has_next_page(fields):
            raise PlanningProjectionError("PROJECT_FIELDS_PAGINATED")
        nodes = fields.get("nodes")
        if not isinstance(nodes, list):
            raise PlanningProjectionError("PROJECT_FIELDS_INVALID")
        result: list[_ProjectField] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            field_id = node.get("id")
            name = node.get("name")
            if not isinstance(field_id, str) or not isinstance(name, str):
                continue
            options_raw = node.get("options")
            if isinstance(options_raw, list):
                options: list[tuple[str, str]] = []
                for option in options_raw:
                    if not isinstance(option, dict):
                        continue
                    option_id = option.get("id")
                    option_name = option.get("name")
                    if isinstance(option_id, str) and isinstance(option_name, str):
                        options.append((option_id, option_name))
                result.append(_ProjectField(field_id, name, "SINGLE_SELECT", tuple(options)))
                continue
            data_type = node.get("dataType")
            kind = data_type if isinstance(data_type, str) else "UNKNOWN"
            result.append(_ProjectField(field_id, name, kind))
        return tuple(result)

    def _find_project_item(self, project_id: str, issue_url: str) -> _ProjectItem | None:
        query = (
            "query($project:ID!){node(id:$project){... on ProjectV2{items(first:100){"
            "nodes{id content{... on Issue{url}}} pageInfo{hasNextPage}}}}}"
        )
        raw = self._run_json(
            (
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"project={project_id}",
            )
        )
        items = _nested(raw, "data", "node", "items")
        if _has_next_page(items):
            raise PlanningProjectionError("PROJECT_ITEMS_PAGINATED")
        nodes = items.get("nodes")
        if not isinstance(nodes, list):
            raise PlanningProjectionError("PROJECT_ITEMS_INVALID")
        matches: list[_ProjectItem] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            item_id = node.get("id")
            content = node.get("content")
            if not isinstance(content, dict):
                continue
            observed_url = content.get("url")
            if isinstance(item_id, str) and observed_url == issue_url:
                matches.append(_ProjectItem(item_id, issue_url))
        if len(matches) > 1:
            raise PlanningProjectionError("PROJECT_ITEM_CONFLICT")
        return matches[0] if matches else None

    def _item_fields(self, item_id: str) -> dict[str, str]:
        query = (
            "query($item:ID!){node(id:$item){... on ProjectV2Item{"
            "fieldValues(first:100){nodes{"
            "... on ProjectV2ItemFieldTextValue{text field{"
            "... on ProjectV2FieldCommon{name}}} "
            "... on ProjectV2ItemFieldSingleSelectValue{name field{"
            "... on ProjectV2FieldCommon{name}}}} pageInfo{hasNextPage}}}}}"
        )
        raw = self._run_json(
            (
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"item={item_id}",
            )
        )
        values = _nested(raw, "data", "node", "fieldValues")
        if _has_next_page(values):
            raise PlanningProjectionError("PROJECT_ITEM_FIELDS_PAGINATED")
        nodes = values.get("nodes")
        if not isinstance(nodes, list):
            raise PlanningProjectionError("PROJECT_ITEM_FIELDS_INVALID")
        result: dict[str, str] = {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            field = node.get("field")
            if not isinstance(field, dict):
                continue
            field_name = field.get("name")
            if not isinstance(field_name, str):
                continue
            text_value = node.get("text")
            select_value = node.get("name")
            value = text_value if isinstance(text_value, str) else select_value
            if isinstance(value, str):
                result[field_name] = value
        return result

    def _run_json(self, command: Sequence[str]) -> object:
        text = self._run_text(command)
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise PlanningProjectionError("GITHUB_JSON_INVALID") from error

    def _run_text(self, command: Sequence[str]) -> str:
        try:
            return self._runner.run(command)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise PlanningProjectionError("GITHUB_COMMAND_FAILED") from error


def _required_field(
    fields: tuple[_ProjectField, ...], name: str, kind: str
) -> _ProjectField:
    matches = [field for field in fields if field.name == name]
    if len(matches) != 1 or matches[0].kind != kind:
        raise PlanningProjectionError("PROJECT_FIELD_REQUIRED")
    return matches[0]


def _required_option(field: _ProjectField, name: str) -> str:
    matches = [option_id for option_id, option_name in field.options if option_name == name]
    if len(matches) != 1:
        raise PlanningProjectionError("PROJECT_STATUS_OPTION_REQUIRED")
    return matches[0]


def _effect_key(
    registration: ProductDevelopmentRegistration,
    kind: str,
    role: str,
    generation: int,
) -> str:
    raw = (
        f"{registration.repository_identity}|{registration.product_key}|"
        f"{registration.goal_revision}|{kind}|{role}|{generation}"
    )
    return "bootstrap:" + hashlib.sha256(raw.encode()).hexdigest()


def _goal_body(
    registration: ProductDevelopmentRegistration, proposal: WorkPlanProposal
) -> str:
    acceptance = "\n".join(f"- {item}" for item in registration.acceptance_criteria)
    return (
        f"{goal_marker(registration)}\n\n"
        "## 目的\n\n"
        f"{registration.goal_text}\n\n"
        "## 受入条件\n\n"
        f"{acceptance}\n\n"
        "## Planning\n\n"
        f"- Goal revision: `{registration.goal_revision}`\n"
        f"- Proposal: `{proposal.proposal_identity}`\n"
    )


def _work_body(
    goal_issue: int,
    registration: ProductDevelopmentRegistration,
    work: PlannedWork,
) -> str:
    acceptance = "\n".join(f"- [ ] {item}" for item in work.acceptance_criteria)
    dependencies = ", ".join(f"`{item}`" for item in work.dependencies) or "なし"
    designs = ", ".join(f"`{item}`" for item in work.canonical_design_targets) or "未指定"
    verification = "必要" if work.human_verification_required else "不要"
    return (
        f"{work_marker(registration, work.logical_key)}\n\n"
        f"Goal: #{goal_issue}\n\n"
        "## 目的\n\n"
        f"{work.purpose}\n\n"
        "## 受入条件\n\n"
        f"{acceptance}\n\n"
        "## 設計・依存\n\n"
        f"- Work kind: `{work.work_kind}`\n"
        f"- logical dependencies: {dependencies}\n"
        f"- canonical design targets: {designs}\n"
        f"- Human Verification: {verification}\n"
    )


def _title(goal_text: str) -> str:
    first = next(
        (line.strip() for line in goal_text.splitlines() if line.strip()),
        "Product Goal",
    )
    return first[:120]


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
