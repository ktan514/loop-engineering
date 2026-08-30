from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from loop_engineering.work_state import (
    EffectAttempt,
    PostgreSQLWorkStateStore,
    WorkCheckpoint,
    WorkRecord,
    WorkStateUnavailable,
)


@dataclass
class Database:
    rows: list[dict[str, object]] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)
    writable: bool = True

    def execute_sql(self, sql: str) -> bool:
        self.statements.append(sql)
        return self.writable

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None:
        self.statements.append(select_sql)
        return self.rows


def record() -> WorkRecord:
    return WorkRecord(
        identity="work:ktan514/loop-engineering:62",
        repository="ktan514/loop-engineering",
        issue_number=62,
        issue_revision="issue:62:1",
        lifecycle="SELECTED",
        selected_transition="DESIGN",
    )


def test_work_and_checkpoint_are_persisted_without_issue_comment_text() -> None:
    database = Database()
    store = PostgreSQLWorkStateStore(database)

    store.upsert_work(record())
    store.record_checkpoint(
        WorkCheckpoint(
            identity="checkpoint:62:1",
            work_identity=record().identity,
            run_identity="run:1",
            checkpoint_kind="SAFE_POINT",
            resumable_state="DESIGN_READY",
            next_action="設計を実装する",
            external_target_identities=("branch:feature/v2",),
        )
    )

    written = "\n".join(database.statements)
    assert "loop_work_records" in written
    assert "loop_work_checkpoints" in written
    assert "Issue comment" not in written
    assert "latest_checkpoint_identity = 'checkpoint:62:1'" in written


def test_latest_checkpoint_reconstructs_db_state() -> None:
    database = Database(
        rows=[
            {
                "identity": "checkpoint:62:2",
                "work_identity": record().identity,
                "run_identity": "run:2",
                "task_packet_identity": None,
                "checkpoint_kind": "EFFECT_PENDING",
                "resumable_state": "MERGE_PENDING",
                "next_action": "対象を照合する",
                "external_target_identities": ["pr:63", "head:abc"],
                "evidence_identities": ["ci:1"],
            }
        ]
    )

    checkpoint = PostgreSQLWorkStateStore(database).latest_checkpoint(record().identity)

    assert checkpoint is not None
    assert checkpoint.checkpoint_kind == "EFFECT_PENDING"
    assert checkpoint.external_target_identities == ("pr:63", "head:abc")
    assert checkpoint.evidence_identities == ("ci:1",)


def test_effect_intent_is_idempotent_and_outcome_never_reenables_resend() -> None:
    database = Database(rows=[{"status": "INTENT_RECORDED"}])
    store = PostgreSQLWorkStateStore(database)
    attempt = EffectAttempt(
        idempotency_key="merge:pr:63:head:abc",
        work_identity=record().identity,
        kind="MERGE",
        target_identity="pr:63|head:abc",
        status="INTENT_RECORDED",
    )

    assert store.record_effect_intent(attempt)
    store.record_effect_outcome(attempt.idempotency_key, "UNCERTAIN")

    written = "\n".join(database.statements)
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in written
    assert "AND status = 'INTENT_RECORDED'" in written
    assert "UNCERTAIN" in written


def test_issue_report_is_outbox_and_requires_bounded_body() -> None:
    database = Database()
    store = PostgreSQLWorkStateStore(database)

    store.enqueue_issue_report(
        identity="report:62:1",
        work_identity=record().identity,
        report_kind="PROGRESS",
        checkpoint_identity="checkpoint:62:1",
        body="DB確定済みの進捗報告",
    )
    store.mark_issue_report_published("report:62:1")

    written = "\n".join(database.statements)
    assert "loop_issue_report_outbox" in written
    assert "'PENDING'" in written
    assert "'PUBLISHED'" in written
    with pytest.raises(WorkStateUnavailable, match="ISSUE_REPORT_INVALID"):
        store.enqueue_issue_report(
            identity="report:too-long",
            work_identity=record().identity,
            report_kind="PROGRESS",
            checkpoint_identity=None,
            body="x" * 4001,
        )


def test_invalid_lifecycle_and_database_write_failure_fail_closed() -> None:
    with pytest.raises(WorkStateUnavailable, match="WORK_RECORD_INVALID"):
        PostgreSQLWorkStateStore(Database()).upsert_work(
            WorkRecord("work:bad", "repo", 62, "rev", "UNKNOWN")
        )
    with pytest.raises(WorkStateUnavailable, match="WORK_STATE_WRITE_FAILED"):
        PostgreSQLWorkStateStore(Database(writable=False)).upsert_work(record())
