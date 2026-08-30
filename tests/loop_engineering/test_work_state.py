from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from loop_engineering.work_state import (
    EffectAttempt,
    PostgreSQLWorkStateStore,
    WorkCheckpoint,
    WorkLease,
    WorkRecord,
    WorkStateUnavailable,
    WorkTaskPacket,
)


@dataclass
class Database:
    rows: list[dict[str, object]] = field(default_factory=list)
    query_results: list[list[dict[str, object]]] | None = None
    statements: list[str] = field(default_factory=list)
    writable: bool = True
    transaction_result: dict[str, object] | None = None

    def execute_sql(self, sql: str) -> bool:
        self.statements.append(sql)
        return self.writable

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None:
        self.statements.append(select_sql)
        if self.query_results is not None:
            return self.query_results.pop(0)
        return self.rows

    def execute_transaction_json(self, sql: str) -> dict[str, object] | None:
        self.statements.append(sql)
        return self.transaction_result


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
        query_results=[
            [
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
        ]
    )

    checkpoint = PostgreSQLWorkStateStore(database).latest_checkpoint(record().identity)

    assert checkpoint is not None
    assert checkpoint.checkpoint_kind == "EFFECT_PENDING"
    assert checkpoint.external_target_identities == ("pr:63", "head:abc")
    assert checkpoint.evidence_identities == ("ci:1",)


def test_task_packet_and_pending_effects_are_recovered_from_db() -> None:
    database = Database(
        query_results=[
            [
                {
                    "identity": record().identity,
                    "repository": record().repository,
                    "issue_number": 62,
                    "issue_revision": "issue:62:2",
                    "lifecycle": "RUNNING",
                    "selected_transition": "IMPLEMENT",
                    "active_lineage_identity": None,
                    "latest_task_packet_identity": "packet:62:1",
                    "latest_checkpoint_identity": "checkpoint:62:2",
                }
            ],
            [
                {
                    "identity": "packet:62:1",
                    "work_identity": record().identity,
                    "generation": 1,
                    "transition": "IMPLEMENT",
                    "status": "ISSUED",
                    "canonical_design_identities": ["design:62"],
                    "external_target_identities": ["branch:feature/v2"],
                }
            ],
            [
                {
                    "identity": "checkpoint:62:2",
                    "work_identity": record().identity,
                    "run_identity": "run:2",
                    "task_packet_identity": "packet:62:1",
                    "checkpoint_kind": "EFFECT_PENDING",
                    "resumable_state": "IMPLEMENT_PENDING",
                    "next_action": "対象を照合する",
                    "external_target_identities": ["pr:63"],
                    "evidence_identities": [],
                }
            ],
            [
                {
                    "idempotency_key": "effect:62:1",
                    "work_identity": record().identity,
                    "kind": "PUSH",
                    "target_identity": "branch:feature/v2",
                    "status": "UNCERTAIN",
                    "request_identity": None,
                }
            ],
        ]
    )
    store = PostgreSQLWorkStateStore(database)

    recovered = store.recover(record().identity)

    assert recovered is not None
    assert recovered.record.lifecycle == "RUNNING"
    assert recovered.task_packet is not None
    assert recovered.task_packet.transition == "IMPLEMENT"
    assert recovered.checkpoint is not None
    assert recovered.checkpoint.resumable_state == "IMPLEMENT_PENDING"
    assert recovered.pending_effects[0].status == "UNCERTAIN"


def test_task_packet_updates_recovery_pointer() -> None:
    database = Database()
    store = PostgreSQLWorkStateStore(database)

    store.record_task_packet(
        WorkTaskPacket(
            identity="packet:62:1",
            work_identity=record().identity,
            generation=1,
            transition="DESIGN",
            status="ISSUED",
        )
    )

    assert "latest_task_packet_identity = 'packet:62:1'" in "\n".join(database.statements)


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


def test_packet_intent_checkpoint_and_work_lease_are_one_transaction() -> None:
    database = Database(transaction_result={"acquired": True})
    store = PostgreSQLWorkStateStore(database)
    packet = WorkTaskPacket(
        identity="packet:62:2",
        work_identity=record().identity,
        generation=2,
        transition="IMPLEMENT",
        status="STARTED",
        canonical_design_identities=("design:62",),
    )
    effect = EffectAttempt(
        idempotency_key="effect:62:2",
        work_identity=record().identity,
        kind="PUSH",
        target_identity="branch:feature/v2",
        status="INTENT_RECORDED",
    )
    checkpoint = WorkCheckpoint(
        identity="checkpoint:62:3",
        work_identity=record().identity,
        run_identity="run:3",
        task_packet_identity=packet.identity,
        checkpoint_kind="EFFECT_PENDING",
        resumable_state="EFFECT_INTENT_RECORDED",
        next_action="外部効果を実行する",
    )

    assert store.issue_packet_transaction(
        record=record(),
        lease=WorkLease(record().identity, "run:3", 2, 300),
        packet=packet,
        effect=effect,
        checkpoint=checkpoint,
    )

    assert len(database.statements) == 1
    transaction = database.statements[0]
    assert transaction.startswith("WITH eligible AS")
    assert "loop_work_leases" in transaction
    assert "loop_task_packets" in transaction
    assert "loop_effect_attempts" in transaction
    assert "loop_work_checkpoints" in transaction
    assert "'INTENT_RECORDED'" in transaction
    assert "'EFFECT_PENDING'" in transaction
    assert "status IN ('INTENT_RECORDED', 'UNCERTAIN')" in transaction
    assert transaction.count("ON CONFLICT") == 1
    assert "ON CONFLICT (identity) DO NOTHING" not in transaction
    assert "ON CONFLICT (idempotency_key) DO NOTHING" not in transaction


def test_lease_conflict_never_issues_packet_or_starts_external_effect() -> None:
    database = Database(transaction_result={"acquired": False})
    store = PostgreSQLWorkStateStore(database)
    packet = WorkTaskPacket("packet:62:2", record().identity, 2, "IMPLEMENT", "STARTED")
    effect = EffectAttempt(
        "effect:62:2", record().identity, "PUSH", "branch:feature/v2", "INTENT_RECORDED"
    )
    checkpoint = WorkCheckpoint(
        "checkpoint:62:3",
        record().identity,
        "run:3",
        "EFFECT_PENDING",
        "EFFECT_INTENT_RECORDED",
        "外部効果を実行する",
        task_packet_identity=packet.identity,
    )

    assert not store.issue_packet_transaction(
        record=record(),
        lease=WorkLease(record().identity, "run:3", 2, 300),
        packet=packet,
        effect=effect,
        checkpoint=checkpoint,
    )
    assert len(database.statements) == 1
    assert "FROM acquired" in database.statements[0]


def test_failed_transaction_leaves_no_partial_packet_state() -> None:
    database = Database(transaction_result=None)
    store = PostgreSQLWorkStateStore(database)
    packet = WorkTaskPacket("packet:62:2", record().identity, 2, "IMPLEMENT", "STARTED")
    effect = EffectAttempt(
        "effect:62:2", record().identity, "PUSH", "branch:feature/v2", "INTENT_RECORDED"
    )
    checkpoint = WorkCheckpoint(
        "checkpoint:62:3",
        record().identity,
        "run:3",
        "EFFECT_PENDING",
        "EFFECT_INTENT_RECORDED",
        "外部効果を実行する",
        task_packet_identity=packet.identity,
    )

    with pytest.raises(WorkStateUnavailable, match="WORK_STATE_TRANSACTION_FAILED"):
        store.issue_packet_transaction(
            record=record(),
            lease=WorkLease(record().identity, "run:3", 2, 300),
            packet=packet,
            effect=effect,
            checkpoint=checkpoint,
        )
    assert len(database.statements) == 1
