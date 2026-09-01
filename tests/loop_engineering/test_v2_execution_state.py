from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from loop_engineering.v2_execution_state import (
    V2ExecutionStateStore,
    build_packet_plan,
    packet_identity,
)
from loop_engineering.work_state import WorkRecord, WorkStateUnavailable


@dataclass
class Database:
    query_results: list[list[dict[str, object]]] = field(default_factory=list)
    transaction_results: list[dict[str, object] | None] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)
    writable: bool = True

    def execute_sql(self, sql: str) -> bool:
        self.statements.append(sql)
        return self.writable

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None:
        self.statements.append(select_sql)
        if self.query_results:
            return self.query_results.pop(0)
        return []

    def execute_transaction_json(self, sql: str) -> dict[str, object] | None:
        self.statements.append(sql)
        if self.transaction_results:
            return self.transaction_results.pop(0)
        return None


def record(*, lifecycle: str = "PLANNED") -> WorkRecord:
    return WorkRecord(
        identity="work:ktan514/loop-engineering:67",
        repository="ktan514/loop-engineering",
        issue_number=67,
        issue_revision="definition:67",
        lifecycle=lifecycle,
    )


def push_plan(generation: int = 1):
    return build_packet_plan(
        work_identity=record().identity,
        generation=generation,
        transition="IMPLEMENT",
        effect_kind="PUSH",
        target_identity="branch:feature/v2-host-cutover",
        expected_preconditions=(("head", "before"),),
        expected_effect=(("head", "after"),),
        canonical_design_identities=("design:v2-host",),
    )


def test_migration_records_work_and_cutover_in_one_transaction() -> None:
    database = Database(transaction_results=[{"migrated": True, "cutover": True}])
    store = V2ExecutionStateStore(database)

    assert store.migrate_candidate(record())

    assert len(database.statements) == 1
    sql = database.statements[0]
    assert "loop_work_records" in sql
    assert "loop_v2_cutovers" in sql
    assert "PLANNED" in sql
    assert "ON CONFLICT (identity) DO NOTHING" in sql


def test_packet_plan_is_typed_and_idempotency_depends_on_generation() -> None:
    first = push_plan(1)
    repeated = push_plan(1)
    next_generation = push_plan(2)

    assert first == repeated
    assert first.idempotency_key != next_generation.idempotency_key

    with pytest.raises(WorkStateUnavailable, match="V2_PACKET_PLAN_INVALID"):
        build_packet_plan(
            work_identity=record().identity,
            generation=1,
            transition="IMPLEMENT",
            effect_kind="READY",
            target_identity="pr:70",
            expected_preconditions=(("head", "abc"), ("draft", "false")),
            expected_effect=(("draft", "false"),),
        )


def test_issue_packet_creates_issued_packet_safe_checkpoint_and_pointers_atomically() -> None:
    database = Database(transaction_results=[{"issued": True}])
    store = V2ExecutionStateStore(database)

    result = store.issue_packet(
        record=record(),
        generation=1,
        plan=push_plan(),
        run_identity="run:issue:1",
    )

    assert result is not None
    assert result.packet.status == "ISSUED"
    assert result.packet.identity == packet_identity(record().identity, 1)
    sql = database.statements[0]
    assert "loop_task_packets" in sql
    assert "'ISSUED'" in sql
    assert "'SAFE_POINT'" in sql
    assert "latest_task_packet_identity" in sql
    assert "latest_checkpoint_identity" in sql
    assert "effect_kind" in sql
    assert "expected_preconditions" in sql
    assert "NOT EXISTS (SELECT 1 FROM loop_work_leases" in sql
    assert "INTENT_RECORDED" in sql
    assert "UNCERTAIN" in sql


def test_start_packet_updates_existing_packet_and_records_lease_effect_checkpoint_once() -> None:
    database = Database(transaction_results=[{"started": True}])
    store = V2ExecutionStateStore(database)
    issued = store.issue_packet  # 型検査でmethodを保持するだけ
    del issued
    packet = type("PacketFactory", (), {})
    del packet

    from loop_engineering.v2_execution_state import V2ExecutionPacket

    value = V2ExecutionPacket(
        packet_identity(record().identity, 1),
        record().identity,
        1,
        "ISSUED",
        push_plan(),
    )
    result = store.start_packet(
        record=WorkRecord(
            **{**record().__dict__}  # type: ignore[attr-defined]
        ),
        packet=value,
        safe_checkpoint_identity="checkpoint:safe",
        holder_identity="holder:1",
        run_identity="run:1",
    )

    assert result is not None
    assert result.effect.status == "INTENT_RECORDED"
    sql = database.statements[0]
    assert "UPDATE loop_task_packets SET status = 'STARTED'" in sql
    assert "INSERT INTO loop_work_leases" in sql
    assert "INSERT INTO loop_effect_attempts" in sql
    assert "'EFFECT_PENDING'" in sql
    assert "JOIN loop_work_checkpoints" in sql
    assert "p.status = 'ISSUED'" in sql
    assert "INSERT INTO loop_task_packets" not in sql


def test_finalize_requires_terminal_effect_and_maps_no_effect_to_superseded() -> None:
    from loop_engineering.v2_execution_state import V2ExecutionPacket

    database = Database(
        query_results=[[{"status": "NO_EFFECT"}]],
        transaction_results=[
            {
                "finalized": True,
                "packet_status": "SUPERSEDED",
                "effect_status": "NO_EFFECT",
                "work_completed": False,
            }
        ],
    )
    store = V2ExecutionStateStore(database)
    packet = V2ExecutionPacket(
        packet_identity(record().identity, 1),
        record().identity,
        1,
        "STARTED",
        push_plan(),
    )

    result = store.finalize_packet(
        packet=packet,
        holder_identity="holder:1",
        run_identity="run:finalize",
    )

    assert result is not None
    assert result.packet_status == "SUPERSEDED"
    assert result.effect_status == "NO_EFFECT"
    transaction = database.statements[1]
    assert "EFFECT_NO_EFFECT" in transaction
    assert "SUPERSEDED" in transaction
    assert "l.holder_identity = 'holder:1'" in transaction


def test_terminal_lease_refuses_unresolved_effects_and_release_is_holder_scoped() -> None:
    database = Database(transaction_results=[{"acquired": False}])
    store = V2ExecutionStateStore(database)

    assert not store.acquire_terminal_lease(
        work_identity=record().identity,
        packet_generation=1,
        holder_identity="holder:2",
    )
    lease_sql = database.statements[0]
    assert "status IN ('INTENT_RECORDED', 'UNCERTAIN')" in lease_sql

    store.release_lease(record().identity, "holder:2")
    assert "holder_identity = 'holder:2'" in database.statements[1]


def test_transaction_failure_never_becomes_success() -> None:
    store = V2ExecutionStateStore(Database(transaction_results=[None]))

    with pytest.raises(WorkStateUnavailable, match="WORK_STATE_TRANSACTION_FAILED"):
        store.issue_packet(
            record=record(),
            generation=1,
            plan=push_plan(),
            run_identity="run:issue:1",
        )
