"""V2自律RunnerのGoal単位状態・dispatch・Planning snapshotをPostgreSQLへ保存する。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from .v2_goal_planning import (
    BootstrapResult,
    PlannedWork,
    ProductDevelopmentRegistration,
    ProjectedPlan,
    ProjectedWork,
    WorkPlanProposal,
)


class AutonomousRuntimeUnavailable(RuntimeError):
    """自律Runnerの永続状態を安全に利用できない。"""


class AutonomousRuntimeDatabase(Protocol):
    def execute_sql(self, sql: str) -> bool: ...

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None: ...


@dataclass(frozen=True, slots=True)
class AutonomousRuntimeState:
    runtime_identity: str
    product_key: str
    repository: str
    goal_revision: str
    status: str
    current_work_identity: str | None
    last_schedule_key: str | None
    last_progress_fingerprint: str | None
    no_progress_count: int
    last_detail: str


@dataclass(frozen=True, slots=True)
class AutonomousDispatch:
    schedule_key: str
    runtime_identity: str
    work_identity: str
    transition: str
    status: str
    detail: str


class PostgreSQLAutonomousRuntimeStore:
    _RUNTIME_STATUSES = frozenset(
        {"ACTIVE", "WAITING", "INTERVENTION_REQUIRED", "COMPLETED"}
    )
    _DISPATCH_STATUSES = frozenset(
        {"DISPATCHED", "COMPLETED", "WAITING", "FAILED", "SUPERSEDED"}
    )

    def __init__(self, database: AutonomousRuntimeDatabase) -> None:
        self._database = database

    def ensure_runtime(
        self,
        registration: ProductDevelopmentRegistration,
    ) -> AutonomousRuntimeState:
        identity = runtime_identity(registration)
        sql = (
            "INSERT INTO loop_autonomous_runtimes "
            "(runtime_identity, product_key, repository, goal_revision, status) VALUES ("
            f"{_literal(identity)}, {_literal(registration.product_key)}, "
            f"{_literal(registration.repository_identity)}, "
            f"{_literal(registration.goal_revision)}, 'ACTIVE') "
            "ON CONFLICT (runtime_identity) DO NOTHING"
        )
        self._execute(sql)
        state = self.get(identity)
        if state is None:
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_READBACK_MISSING")
        if (
            state.product_key != registration.product_key
            or state.repository != registration.repository_identity
            or state.goal_revision != registration.goal_revision
        ):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_IDENTITY_CONFLICT")
        return state

    def get(self, runtime_identity_value: str) -> AutonomousRuntimeState | None:
        rows = self._query(
            "SELECT runtime_identity, product_key, repository, goal_revision, status, "
            "current_work_identity, last_schedule_key, last_progress_fingerprint, "
            "no_progress_count, last_detail FROM loop_autonomous_runtimes "
            f"WHERE runtime_identity = {_literal(runtime_identity_value)} LIMIT 1"
        )
        if not rows:
            return None
        return _runtime_state(rows[0])

    def update_runtime(
        self,
        runtime_identity_value: str,
        *,
        status: str,
        current_work_identity: str | None,
        schedule_key: str | None,
        progress_fingerprint: str | None,
        no_progress_count: int,
        detail: str,
    ) -> AutonomousRuntimeState:
        if (
            status not in self._RUNTIME_STATUSES
            or no_progress_count < 0
            or not detail
            or len(detail) > 1024
        ):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_UPDATE_INVALID")
        completed = "now()" if status == "COMPLETED" else "NULL"
        self._execute(
            "UPDATE loop_autonomous_runtimes SET "
            f"status = {_literal(status)}, "
            f"current_work_identity = {_nullable_literal(current_work_identity)}, "
            f"last_schedule_key = {_nullable_literal(schedule_key)}, "
            f"last_progress_fingerprint = {_nullable_literal(progress_fingerprint)}, "
            f"no_progress_count = {no_progress_count}, "
            f"last_detail = {_literal(detail)}, updated_at = now(), completed_at = {completed} "
            f"WHERE runtime_identity = {_literal(runtime_identity_value)}"
        )
        state = self.get(runtime_identity_value)
        if state is None:
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_UPDATE_READBACK_MISSING")
        return state

    def dispatch(self, item: AutonomousDispatch) -> bool:
        if (
            item.status not in self._DISPATCH_STATUSES
            or not item.schedule_key
            or not item.runtime_identity
            or not item.work_identity
            or not item.transition
            or len(item.detail) > 1024
        ):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_DISPATCH_INVALID")
        self._execute(
            "INSERT INTO loop_autonomous_dispatches "
            "(schedule_key, runtime_identity, work_identity, transition, status, detail) VALUES ("
            f"{_literal(item.schedule_key)}, {_literal(item.runtime_identity)}, "
            f"{_literal(item.work_identity)}, {_literal(item.transition)}, "
            f"{_literal(item.status)}, {_literal(item.detail)}) "
            "ON CONFLICT (schedule_key) DO NOTHING"
        )
        found = self.dispatch_record(item.schedule_key)
        return found == item

    def update_dispatch(self, schedule_key: str, status: str, detail: str) -> None:
        if status not in self._DISPATCH_STATUSES or not detail or len(detail) > 1024:
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_DISPATCH_UPDATE_INVALID")
        self._execute(
            "UPDATE loop_autonomous_dispatches SET "
            f"status = {_literal(status)}, detail = {_literal(detail)}, updated_at = now() "
            f"WHERE schedule_key = {_literal(schedule_key)}"
        )

    def dispatch_record(self, schedule_key: str) -> AutonomousDispatch | None:
        rows = self._query(
            "SELECT schedule_key, runtime_identity, work_identity, transition, status, detail "
            "FROM loop_autonomous_dispatches "
            f"WHERE schedule_key = {_literal(schedule_key)} LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        return AutonomousDispatch(
            _required_string(row, "schedule_key"),
            _required_string(row, "runtime_identity"),
            _required_string(row, "work_identity"),
            _required_string(row, "transition"),
            _required_string(row, "status"),
            _required_string(row, "detail"),
        )

    def dispatched_schedule_keys(self, runtime_identity_value: str) -> frozenset[str]:
        rows = self._query(
            "SELECT schedule_key FROM loop_autonomous_dispatches "
            f"WHERE runtime_identity = {_literal(runtime_identity_value)} "
            "AND status IN ('DISPATCHED', 'COMPLETED', 'WAITING')"
        )
        return frozenset(_required_string(row, "schedule_key") for row in rows)

    def save_plan(self, runtime_identity_value: str, result: BootstrapResult) -> None:
        proposal_json = json.dumps(
            _proposal_to_json(result.proposal),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        projection_json = json.dumps(
            _projection_to_json(result.projection),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._execute(
            "INSERT INTO loop_goal_plans "
            "(runtime_identity, proposal_identity, proposal_json, projection_json) VALUES ("
            f"{_literal(runtime_identity_value)}, {_literal(result.proposal.proposal_identity)}, "
            f"{_literal(proposal_json)}::jsonb, {_literal(projection_json)}::jsonb) "
            "ON CONFLICT (runtime_identity) DO NOTHING"
        )
        stored = self.plan(runtime_identity_value)
        if stored != result:
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_IDENTITY_CONFLICT")

    def plan(self, runtime_identity_value: str) -> BootstrapResult | None:
        rows = self._query(
            "SELECT proposal_identity, proposal_json, projection_json FROM loop_goal_plans "
            f"WHERE runtime_identity = {_literal(runtime_identity_value)} LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        proposal_raw = row.get("proposal_json")
        projection_raw = row.get("projection_json")
        if not isinstance(proposal_raw, dict) or not isinstance(projection_raw, dict):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
        result = BootstrapResult(
            _proposal_from_json(proposal_raw),
            _projection_from_json(projection_raw),
        )
        if result.proposal.proposal_identity != _required_string(row, "proposal_identity"):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
        return result

    def _execute(self, sql: str) -> None:
        if not self._database.execute_sql(sql):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_WRITE_FAILED")

    def _query(self, sql: str) -> list[dict[str, object]]:
        rows = self._database.query_json_rows(sql)
        if rows is None:
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_READ_FAILED")
        return rows


def runtime_identity(registration: ProductDevelopmentRegistration) -> str:
    payload = (
        registration.product_key,
        registration.repository_identity,
        registration.goal_definition_identity,
        registration.goal_revision,
    )
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "runtime:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _runtime_state(row: dict[str, object]) -> AutonomousRuntimeState:
    count = row.get("no_progress_count")
    if not isinstance(count, int) or count < 0:
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_ROW_INVALID")
    status = _required_string(row, "status")
    if status not in PostgreSQLAutonomousRuntimeStore._RUNTIME_STATUSES:
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_ROW_INVALID")
    return AutonomousRuntimeState(
        _required_string(row, "runtime_identity"),
        _required_string(row, "product_key"),
        _required_string(row, "repository"),
        _required_string(row, "goal_revision"),
        status,
        _optional_string(row, "current_work_identity"),
        _optional_string(row, "last_schedule_key"),
        _optional_string(row, "last_progress_fingerprint"),
        count,
        _required_string(row, "last_detail"),
    )


def _proposal_to_json(proposal: WorkPlanProposal) -> dict[str, object]:
    return {
        "proposal_identity": proposal.proposal_identity,
        "goal_revision": proposal.goal_revision,
        "completion_conditions": list(proposal.completion_conditions),
        "works": [
            {
                "logical_key": work.logical_key,
                "title": work.title,
                "purpose": work.purpose,
                "acceptance_criteria": list(work.acceptance_criteria),
                "dependencies": list(work.dependencies),
                "work_kind": work.work_kind,
                "human_verification_required": work.human_verification_required,
                "canonical_design_targets": list(work.canonical_design_targets),
            }
            for work in proposal.works
        ],
    }


def _proposal_from_json(raw: dict[str, object]) -> WorkPlanProposal:
    works = raw.get("works")
    completion = raw.get("completion_conditions")
    if not isinstance(works, list) or not isinstance(completion, list):
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
    parsed: list[PlannedWork] = []
    for item in works:
        if not isinstance(item, dict):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
        acceptance = _string_list(item, "acceptance_criteria")
        dependencies = _string_list(item, "dependencies")
        targets = _string_list(item, "canonical_design_targets")
        human = item.get("human_verification_required")
        if not isinstance(human, bool):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
        parsed.append(
            PlannedWork(
                logical_key=_required_string(item, "logical_key"),
                title=_required_string(item, "title"),
                purpose=_required_string(item, "purpose"),
                acceptance_criteria=acceptance,
                dependencies=dependencies,
                work_kind=_required_string(item, "work_kind"),
                human_verification_required=human,
                canonical_design_targets=targets,
            )
        )
    return WorkPlanProposal(
        _required_string(raw, "proposal_identity"),
        _required_string(raw, "goal_revision"),
        tuple(parsed),
        tuple(_strings(completion)),
    )


def _projection_to_json(projection: ProjectedPlan) -> dict[str, object]:
    return {
        "goal_issue_number": projection.goal_issue_number,
        "goal_issue_url": projection.goal_issue_url,
        "goal_project_item_id": projection.goal_project_item_id,
        "works": [
            {
                "logical_key": work.logical_key,
                "issue_number": work.issue_number,
                "issue_url": work.issue_url,
                "project_item_id": work.project_item_id,
                "acceptance_digest": work.acceptance_digest,
                "dependencies": list(work.dependencies),
            }
            for work in projection.works
        ],
    }


def _projection_from_json(raw: dict[str, object]) -> ProjectedPlan:
    works = raw.get("works")
    goal_issue_number = raw.get("goal_issue_number")
    if not isinstance(works, list) or not isinstance(goal_issue_number, int):
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
    projected: list[ProjectedWork] = []
    for item in works:
        if not isinstance(item, dict):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
        issue_number = item.get("issue_number")
        if not isinstance(issue_number, int):
            raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
        projected.append(
            ProjectedWork(
                _required_string(item, "logical_key"),
                issue_number,
                _required_string(item, "issue_url"),
                _required_string(item, "project_item_id"),
                _required_string(item, "acceptance_digest"),
                _string_list(item, "dependencies"),
            )
        )
    return ProjectedPlan(
        goal_issue_number,
        _required_string(raw, "goal_issue_url"),
        _required_string(raw, "goal_project_item_id"),
        tuple(projected),
    )


def _required_string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_ROW_INVALID")
    return value


def _optional_string(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_ROW_INVALID")
    return value


def _string_list(row: dict[str, object], key: str) -> tuple[str, ...]:
    value = row.get(key)
    if not isinstance(value, list):
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
    return tuple(_strings(value))


def _strings(values: list[object]) -> list[str]:
    if not all(isinstance(value, str) for value in values):
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_PLAN_ROW_INVALID")
    return [value for value in values if isinstance(value, str)]


def _literal(value: str) -> str:
    if "\x00" in value or len(value) > 100_000:
        raise AutonomousRuntimeUnavailable("AUTONOMOUS_RUNTIME_VALUE_INVALID")
    return "'" + value.replace("'", "''") + "'"


def _nullable_literal(value: str | None) -> str:
    return "NULL" if value is None else _literal(value)
