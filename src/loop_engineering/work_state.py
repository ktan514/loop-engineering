"""V2の作業状態をDBへ保存し、停止後の再開情報を復元する。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


class WorkStateUnavailable(RuntimeError):
    """作業状態を安全に読み書きできない。"""


class WorkStateDatabase(Protocol):
    def execute_sql(self, sql: str) -> bool: ...

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None: ...


@dataclass(frozen=True, slots=True)
class WorkRecord:
    identity: str
    repository: str
    issue_number: int
    issue_revision: str
    lifecycle: str
    selected_transition: str | None = None
    active_lineage_identity: str | None = None
    latest_task_packet_identity: str | None = None
    latest_checkpoint_identity: str | None = None


@dataclass(frozen=True, slots=True)
class WorkCheckpoint:
    identity: str
    work_identity: str
    run_identity: str
    checkpoint_kind: str
    resumable_state: str
    next_action: str
    task_packet_identity: str | None = None
    external_target_identities: tuple[str, ...] = ()
    evidence_identities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EffectAttempt:
    idempotency_key: str
    work_identity: str
    kind: str
    target_identity: str
    status: str
    request_identity: str | None = None


class PostgreSQLWorkStateStore:
    """秘密を含まないV2作業状態だけをPostgreSQLへ保存する。"""

    _LIFECYCLES = frozenset({"PLANNED", "SELECTED", "RUNNING", "WAITING", "BLOCKED", "COMPLETED"})
    _CHECKPOINT_KINDS = frozenset({"SAFE_POINT", "EFFECT_PENDING", "EFFECT_CONFIRMED", "WAITING"})
    _EFFECT_STATUSES = frozenset({"INTENT_RECORDED", "CONFIRMED", "NO_EFFECT", "UNCERTAIN"})
    _OUTBOX_STATUSES = frozenset({"PENDING", "PUBLISHED"})

    def __init__(self, database: WorkStateDatabase) -> None:
        self._database = database

    def upsert_work(self, record: WorkRecord) -> None:
        if record.lifecycle not in self._LIFECYCLES or record.issue_number < 1:
            raise WorkStateUnavailable("WORK_RECORD_INVALID")
        self._execute(
            "INSERT INTO loop_work_records "
            "(identity, repository, issue_number, issue_revision, lifecycle, selected_transition, "
            "active_lineage_identity, latest_task_packet_identity, "
            "latest_checkpoint_identity) VALUES ("
            f"{_literal(record.identity)}, {_literal(record.repository)}, {record.issue_number}, "
            f"{_literal(record.issue_revision)}, {_literal(record.lifecycle)}, "
            f"{_nullable_literal(record.selected_transition)}, "
            f"{_nullable_literal(record.active_lineage_identity)}, "
            f"{_nullable_literal(record.latest_task_packet_identity)}, "
            f"{_nullable_literal(record.latest_checkpoint_identity)}) "
            "ON CONFLICT (identity) DO UPDATE SET "
            "issue_revision = EXCLUDED.issue_revision, lifecycle = EXCLUDED.lifecycle, "
            "selected_transition = EXCLUDED.selected_transition, "
            "active_lineage_identity = EXCLUDED.active_lineage_identity, "
            "latest_task_packet_identity = EXCLUDED.latest_task_packet_identity, "
            "latest_checkpoint_identity = EXCLUDED.latest_checkpoint_identity, "
            "revision = loop_work_records.revision + 1, updated_at = now()"
        )

    def record_checkpoint(self, checkpoint: WorkCheckpoint) -> None:
        if checkpoint.checkpoint_kind not in self._CHECKPOINT_KINDS:
            raise WorkStateUnavailable("WORK_CHECKPOINT_INVALID")
        self._execute(
            "INSERT INTO loop_work_checkpoints "
            "(identity, work_identity, run_identity, task_packet_identity, checkpoint_kind, "
            "resumable_state, next_action, external_target_identities, "
            "evidence_identities) VALUES ("
            f"{_literal(checkpoint.identity)}, {_literal(checkpoint.work_identity)}, "
            f"{_literal(checkpoint.run_identity)}, "
            f"{_nullable_literal(checkpoint.task_packet_identity)}, "
            f"{_literal(checkpoint.checkpoint_kind)}, {_literal(checkpoint.resumable_state)}, "
            f"{_literal(checkpoint.next_action)}, "
            f"{_json_literal(checkpoint.external_target_identities)}, "
            f"{_json_literal(checkpoint.evidence_identities)}) "
            "ON CONFLICT (identity) DO NOTHING"
        )
        self._execute(
            "UPDATE loop_work_records SET "
            f"latest_checkpoint_identity = {_literal(checkpoint.identity)}, updated_at = now() "
            f"WHERE identity = {_literal(checkpoint.work_identity)}"
        )

    def latest_checkpoint(self, work_identity: str) -> WorkCheckpoint | None:
        rows = self._query(
            "SELECT identity, work_identity, run_identity, task_packet_identity, checkpoint_kind, "
            "resumable_state, next_action, external_target_identities, evidence_identities "
            "FROM loop_work_checkpoints "
            f"WHERE work_identity = {_literal(work_identity)} "
            "ORDER BY recorded_at DESC, identity DESC LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        return WorkCheckpoint(
            identity=_required_string(row, "identity"),
            work_identity=_required_string(row, "work_identity"),
            run_identity=_required_string(row, "run_identity"),
            task_packet_identity=_optional_string(row, "task_packet_identity"),
            checkpoint_kind=_required_string(row, "checkpoint_kind"),
            resumable_state=_required_string(row, "resumable_state"),
            next_action=_required_string(row, "next_action"),
            external_target_identities=_string_tuple(row, "external_target_identities"),
            evidence_identities=_string_tuple(row, "evidence_identities"),
        )

    def record_effect_intent(self, attempt: EffectAttempt) -> bool:
        if attempt.status != "INTENT_RECORDED":
            raise WorkStateUnavailable("EFFECT_INTENT_INVALID")
        self._execute(
            "INSERT INTO loop_effect_attempts "
            "(idempotency_key, work_identity, kind, target_identity, status, "
            "request_identity) VALUES ("
            f"{_literal(attempt.idempotency_key)}, {_literal(attempt.work_identity)}, "
            f"{_literal(attempt.kind)}, {_literal(attempt.target_identity)}, "
            "'INTENT_RECORDED', "
            f"{_nullable_literal(attempt.request_identity)}) "
            "ON CONFLICT (idempotency_key) DO NOTHING"
        )
        rows = self._query(
            "SELECT status FROM loop_effect_attempts "
            f"WHERE idempotency_key = {_literal(attempt.idempotency_key)} LIMIT 1"
        )
        return bool(rows) and _optional_string(rows[0], "status") == "INTENT_RECORDED"

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None:
        if status not in self._EFFECT_STATUSES or status == "INTENT_RECORDED":
            raise WorkStateUnavailable("EFFECT_OUTCOME_INVALID")
        self._execute(
            "UPDATE loop_effect_attempts SET "
            f"status = {_literal(status)}, "
            "confirmed_at = CASE WHEN "
            f"{_literal(status)} = 'CONFIRMED' THEN now() ELSE confirmed_at END "
            f"WHERE idempotency_key = {_literal(idempotency_key)} "
            "AND status = 'INTENT_RECORDED'"
        )

    def enqueue_issue_report(
        self,
        *,
        identity: str,
        work_identity: str,
        report_kind: str,
        checkpoint_identity: str | None,
        body: str,
    ) -> None:
        if not body or len(body) > 4000:
            raise WorkStateUnavailable("ISSUE_REPORT_INVALID")
        self._execute(
            "INSERT INTO loop_issue_report_outbox "
            "(identity, work_identity, status, report_kind, checkpoint_identity, body) VALUES ("
            f"{_literal(identity)}, {_literal(work_identity)}, 'PENDING', {_literal(report_kind)}, "
            f"{_nullable_literal(checkpoint_identity)}, {_literal(body)}) "
            "ON CONFLICT (identity) DO NOTHING"
        )

    def mark_issue_report_published(self, identity: str) -> None:
        self._execute(
            "UPDATE loop_issue_report_outbox SET status = 'PUBLISHED', published_at = now() "
            f"WHERE identity = {_literal(identity)} AND status = 'PENDING'"
        )

    def _execute(self, sql: str) -> None:
        if not self._database.execute_sql(sql):
            raise WorkStateUnavailable("WORK_STATE_WRITE_FAILED")

    def _query(self, sql: str) -> list[dict[str, object]]:
        rows = self._database.query_json_rows(sql)
        if rows is None:
            raise WorkStateUnavailable("WORK_STATE_READ_FAILED")
        return rows


def _literal(value: str) -> str:
    if "\x00" in value or len(value) > 4096:
        raise WorkStateUnavailable("WORK_STATE_VALUE_INVALID")
    return "'" + value.replace("'", "''") + "'"


def _nullable_literal(value: str | None) -> str:
    return "NULL" if value is None else _literal(value)


def _json_literal(values: tuple[str, ...]) -> str:
    if any("\x00" in value or len(value) > 1024 for value in values):
        raise WorkStateUnavailable("WORK_STATE_VALUE_INVALID")
    return _literal(json.dumps(values, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def _required_string(row: dict[str, object], name: str) -> str:
    value = _optional_string(row, name)
    if value is None:
        raise WorkStateUnavailable("WORK_STATE_ROW_INVALID")
    return value


def _optional_string(row: dict[str, object], name: str) -> str | None:
    value = row.get(name)
    return value if isinstance(value, str) else None


def _string_tuple(row: dict[str, object], name: str) -> tuple[str, ...]:
    value = row.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkStateUnavailable("WORK_STATE_ROW_INVALID")
    return tuple(value)
