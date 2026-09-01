"""V2 Host専用の切替・作業パケット実行状態をPostgreSQLで管理する。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from .work_state import EffectAttempt, WorkRecord, WorkStateUnavailable


class V2ExecutionDatabase(Protocol):
    def execute_sql(self, sql: str) -> bool: ...

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None: ...

    def execute_transaction_json(self, sql: str) -> dict[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class V2PacketPlan:
    transition: str
    effect_kind: str
    target_identity: str
    idempotency_key: str
    expected_preconditions: tuple[tuple[str, str], ...]
    expected_effect: tuple[tuple[str, str], ...]
    canonical_design_identities: tuple[str, ...] = ()

    def effect_attempt(self, work_identity: str, generation: int) -> EffectAttempt:
        return EffectAttempt(
            idempotency_key=self.idempotency_key,
            work_identity=work_identity,
            kind=self.effect_kind,
            target_identity=self.target_identity,
            status="INTENT_RECORDED",
            packet_generation=generation,
            expected_preconditions=self.expected_preconditions,
            expected_effect=self.expected_effect,
        )


@dataclass(frozen=True, slots=True)
class V2ExecutionPacket:
    identity: str
    work_identity: str
    generation: int
    status: str
    plan: V2PacketPlan


@dataclass(frozen=True, slots=True)
class V2PacketIssueResult:
    packet: V2ExecutionPacket
    checkpoint_identity: str
    already_issued: bool = False


@dataclass(frozen=True, slots=True)
class V2PacketStartResult:
    effect: EffectAttempt
    checkpoint_identity: str


@dataclass(frozen=True, slots=True)
class V2PacketFinalizationResult:
    packet_status: str
    effect_status: str
    checkpoint_identity: str
    work_completed: bool


class V2ExecutionStateStore:
    """V2 Hostが必要とする追加状態だけを既存Work Stateへ重ねる。"""

    _PACKET_TERMINAL = frozenset({"COMPLETED", "SUPERSEDED"})

    def __init__(self, database: V2ExecutionDatabase) -> None:
        self._database = database

    def is_cutover(self, repository: str) -> bool:
        _validate_repository(repository)
        rows = self._query(
            "SELECT repository FROM loop_v2_cutovers "
            f"WHERE repository = {_literal(repository)} LIMIT 1"
        )
        return bool(rows)

    def migrate_candidate(self, record: WorkRecord) -> bool:
        if record.lifecycle != "PLANNED" or record.issue_number < 1:
            raise WorkStateUnavailable("V2_MIGRATION_RECORD_INVALID")
        _validate_repository(record.repository)
        result = self._transaction(
            "WITH inserted_work AS ("
            "INSERT INTO loop_work_records "
            "(identity, repository, issue_number, issue_revision, lifecycle) VALUES ("
            f"{_literal(record.identity)}, {_literal(record.repository)}, {record.issue_number}, "
            f"{_literal(record.issue_revision)}, 'PLANNED') "
            "ON CONFLICT (identity) DO NOTHING RETURNING identity"
            "), existing_work AS ("
            "SELECT identity FROM loop_work_records "
            f"WHERE identity = {_literal(record.identity)} "
            f"AND repository = {_literal(record.repository)} "
            f"AND issue_number = {record.issue_number}"
            "), matching_work AS ("
            "SELECT identity FROM inserted_work UNION ALL "
            "SELECT identity FROM existing_work"
            "), recorded_cutover AS ("
            "INSERT INTO loop_v2_cutovers (repository) "
            f"SELECT {_literal(record.repository)} FROM matching_work "
            "ON CONFLICT (repository) DO NOTHING RETURNING repository"
            "), existing_cutover AS ("
            "SELECT repository FROM loop_v2_cutovers "
            f"WHERE repository = {_literal(record.repository)}"
            ") SELECT json_build_object("
            "'migrated', EXISTS (SELECT 1 FROM matching_work), "
            "'cutover', EXISTS (SELECT 1 FROM recorded_cutover) "
            "OR EXISTS (SELECT 1 FROM existing_cutover)"
            ")::text"
        )
        return result.get("migrated") is True and result.get("cutover") is True

    def work_record(self, work_identity: str) -> WorkRecord | None:
        rows = self._query(
            "SELECT identity, repository, issue_number, issue_revision, lifecycle, "
            "selected_transition, active_lineage_identity, latest_task_packet_identity, "
            "latest_checkpoint_identity FROM loop_work_records "
            f"WHERE identity = {_literal(work_identity)} LIMIT 1"
        )
        if not rows:
            return None
        return _work_record(rows[0])

    def issue_packet(
        self,
        *,
        record: WorkRecord,
        generation: int,
        plan: V2PacketPlan,
        run_identity: str,
    ) -> V2PacketIssueResult | None:
        if generation < 1 or not run_identity:
            raise WorkStateUnavailable("V2_PACKET_ISSUE_INVALID")
        _validate_plan(plan)
        packet_identity_value = packet_identity(record.identity, generation)
        checkpoint_identity = _checkpoint_identity(packet_identity_value, "issued")
        packet = V2ExecutionPacket(
            packet_identity_value,
            record.identity,
            generation,
            "ISSUED",
            plan,
        )
        result = self._transaction(
            _issue_packet_sql(
                record=record,
                packet=packet,
                run_identity=run_identity,
                checkpoint_identity=checkpoint_identity,
            )
        )
        if result.get("issued") is True:
            return V2PacketIssueResult(packet, checkpoint_identity)
        existing = self.packet(packet_identity_value)
        if existing == packet:
            return V2PacketIssueResult(packet, checkpoint_identity, already_issued=True)
        return None

    def packet(self, packet_identity_value: str) -> V2ExecutionPacket | None:
        rows = self._query(
            "SELECT identity, work_identity, generation, transition, status, "
            "canonical_design_identities, effect_kind, effect_target_identity, "
            "effect_idempotency_key, expected_preconditions, expected_effect "
            "FROM loop_task_packets "
            f"WHERE identity = {_literal(packet_identity_value)} LIMIT 1"
        )
        if not rows:
            return None
        return _execution_packet(rows[0])

    def start_packet(
        self,
        *,
        record: WorkRecord,
        packet: V2ExecutionPacket,
        safe_checkpoint_identity: str,
        holder_identity: str,
        run_identity: str,
        lease_seconds: int = 300,
    ) -> V2PacketStartResult | None:
        if (
            packet.status != "ISSUED"
            or packet.work_identity != record.identity
            or not holder_identity
            or not run_identity
            or not safe_checkpoint_identity
            or not 1 <= lease_seconds <= 3600
        ):
            raise WorkStateUnavailable("V2_PACKET_START_INVALID")
        _validate_plan(packet.plan)
        pending_checkpoint = _checkpoint_identity(packet.identity, "effect-pending")
        effect = packet.plan.effect_attempt(record.identity, packet.generation)
        result = self._transaction(
            _start_packet_sql(
                record=record,
                packet=packet,
                safe_checkpoint_identity=safe_checkpoint_identity,
                pending_checkpoint_identity=pending_checkpoint,
                effect=effect,
                holder_identity=holder_identity,
                run_identity=run_identity,
                lease_seconds=lease_seconds,
            )
        )
        if result.get("started") is not True:
            return None
        return V2PacketStartResult(effect, pending_checkpoint)

    def acquire_terminal_lease(
        self,
        *,
        work_identity: str,
        packet_generation: int,
        holder_identity: str,
        lease_seconds: int = 300,
    ) -> bool:
        if packet_generation < 1 or not holder_identity or not 1 <= lease_seconds <= 3600:
            raise WorkStateUnavailable("V2_LEASE_INVALID")
        result = self._transaction(
            "WITH eligible AS ("
            "SELECT p.work_identity FROM loop_task_packets p "
            f"WHERE p.work_identity = {_literal(work_identity)} "
            f"AND p.generation = {packet_generation} "
            "AND p.status IN ('STARTED', 'COMPLETED', 'SUPERSEDED') "
            "AND NOT EXISTS (SELECT 1 FROM loop_effect_attempts e "
            "WHERE e.work_identity = p.work_identity "
            "AND e.status IN ('INTENT_RECORDED', 'UNCERTAIN'))"
            "), acquired AS ("
            "INSERT INTO loop_work_leases "
            "(work_identity, holder_identity, packet_generation, expires_at) SELECT "
            f"{_literal(work_identity)}, {_literal(holder_identity)}, {packet_generation}, "
            f"now() + INTERVAL '{lease_seconds} seconds' FROM eligible "
            "ON CONFLICT (work_identity) DO UPDATE SET "
            "holder_identity = EXCLUDED.holder_identity, "
            "packet_generation = EXCLUDED.packet_generation, acquired_at = now(), "
            "expires_at = EXCLUDED.expires_at "
            "WHERE loop_work_leases.expires_at <= now() "
            f"OR loop_work_leases.holder_identity = {_literal(holder_identity)} "
            "RETURNING work_identity"
            ") SELECT json_build_object("
            "'acquired', EXISTS (SELECT 1 FROM acquired))::text"
        )
        return result.get("acquired") is True

    def terminal_effect_status(self, work_identity: str, packet_generation: int) -> str | None:
        rows = self._query(
            "SELECT status FROM loop_effect_attempts "
            f"WHERE work_identity = {_literal(work_identity)} "
            f"AND packet_generation = {packet_generation} "
            "AND status IN ('CONFIRMED', 'NO_EFFECT') ORDER BY recorded_at DESC LIMIT 2"
        )
        if len(rows) != 1:
            return None
        status = rows[0].get("status")
        return status if status in {"CONFIRMED", "NO_EFFECT"} else None

    def finalize_packet(
        self,
        *,
        packet: V2ExecutionPacket,
        holder_identity: str,
        run_identity: str,
    ) -> V2PacketFinalizationResult | None:
        if packet.status != "STARTED" or not holder_identity or not run_identity:
            raise WorkStateUnavailable("V2_PACKET_FINALIZE_INVALID")
        terminal_effect = self.terminal_effect_status(packet.work_identity, packet.generation)
        if terminal_effect is None:
            return None
        checkpoint_identity = _checkpoint_identity(
            packet.identity,
            "confirmed" if terminal_effect == "CONFIRMED" else "no-effect",
        )
        result = self._transaction(
            _finalize_packet_sql(
                packet=packet,
                holder_identity=holder_identity,
                run_identity=run_identity,
                checkpoint_identity=checkpoint_identity,
                expected_effect_status=terminal_effect,
            )
        )
        if result.get("finalized") is not True:
            return None
        packet_status = result.get("packet_status")
        effect_status = result.get("effect_status")
        work_completed = result.get("work_completed")
        if (
            packet_status not in self._PACKET_TERMINAL
            or effect_status not in {"CONFIRMED", "NO_EFFECT"}
            or not isinstance(work_completed, bool)
        ):
            raise WorkStateUnavailable("V2_PACKET_FINALIZE_RESULT_INVALID")
        return V2PacketFinalizationResult(
            packet_status,
            effect_status,
            checkpoint_identity,
            work_completed,
        )

    def release_lease(self, work_identity: str, holder_identity: str) -> None:
        if not holder_identity:
            raise WorkStateUnavailable("V2_LEASE_INVALID")
        self._execute(
            "DELETE FROM loop_work_leases "
            f"WHERE work_identity = {_literal(work_identity)} "
            f"AND holder_identity = {_literal(holder_identity)}"
        )

    def _query(self, sql: str) -> list[dict[str, object]]:
        rows = self._database.query_json_rows(sql)
        if rows is None:
            raise WorkStateUnavailable("WORK_STATE_READ_FAILED")
        return rows

    def _execute(self, sql: str) -> None:
        if not self._database.execute_sql(sql):
            raise WorkStateUnavailable("WORK_STATE_WRITE_FAILED")

    def _transaction(self, sql: str) -> dict[str, object]:
        result = self._database.execute_transaction_json(sql)
        if result is None:
            raise WorkStateUnavailable("WORK_STATE_TRANSACTION_FAILED")
        return result


def build_packet_plan(
    *,
    work_identity: str,
    generation: int,
    transition: str,
    effect_kind: str,
    target_identity: str,
    expected_preconditions: tuple[tuple[str, str], ...],
    expected_effect: tuple[tuple[str, str], ...],
    canonical_design_identities: tuple[str, ...] = (),
) -> V2PacketPlan:
    base = {
        "work": work_identity,
        "generation": generation,
        "transition": transition,
        "kind": effect_kind,
        "target": target_identity,
        "before": dict(expected_preconditions),
        "after": dict(expected_effect),
    }
    digest = hashlib.sha256(
        json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    plan = V2PacketPlan(
        transition=transition,
        effect_kind=effect_kind,
        target_identity=target_identity,
        idempotency_key=f"effect:{digest}",
        expected_preconditions=expected_preconditions,
        expected_effect=expected_effect,
        canonical_design_identities=canonical_design_identities,
    )
    _validate_plan(plan)
    return plan


def packet_identity(work_identity: str, generation: int) -> str:
    if generation < 1:
        raise WorkStateUnavailable("V2_PACKET_GENERATION_INVALID")
    digest = hashlib.sha256(f"{work_identity}\0{generation}".encode()).hexdigest()
    return f"packet:{digest}"


def _checkpoint_identity(packet_identity_value: str, kind: str) -> str:
    digest = hashlib.sha256(f"{packet_identity_value}\0{kind}".encode()).hexdigest()
    return f"checkpoint:{digest}"


def _validate_plan(plan: V2PacketPlan) -> None:
    if not plan.transition or len(plan.transition) > 128:
        raise WorkStateUnavailable("V2_PACKET_PLAN_INVALID")
    if not plan.idempotency_key or len(plan.idempotency_key) > 256:
        raise WorkStateUnavailable("V2_PACKET_PLAN_INVALID")
    before = _pairs(plan.expected_preconditions)
    after = _pairs(plan.expected_effect)
    if before is None or after is None or not before or not after or before == after:
        raise WorkStateUnavailable("V2_PACKET_PLAN_INVALID")
    if plan.effect_kind == "PUSH":
        valid = (
            _target_suffix(plan.target_identity, "branch") is not None
            and set(before) == {"head"}
            and set(after) == {"head"}
        )
    elif plan.effect_kind == "READY":
        valid = (
            _target_number(plan.target_identity, "pr") is not None
            and set(before) == {"head", "draft"}
            and set(after) == {"draft"}
            and before["draft"] == "true"
            and after["draft"] == "false"
        )
    elif plan.effect_kind == "MERGE":
        valid = (
            _target_number(plan.target_identity, "pr") is not None
            and set(before) == {"head", "base", "state"}
            and set(after) == {"state"}
            and before["state"] == "OPEN"
            and after["state"] == "MERGED"
        )
    elif plan.effect_kind == "ISSUE_UPDATE":
        keys = set(before)
        valid = (
            _target_number(plan.target_identity, "issue") is not None
            and keys == set(after)
            and bool(keys)
            and keys.issubset({"state", "title"})
            and ("state" not in keys or before["state"] in {"OPEN", "CLOSED"})
            and ("state" not in keys or after["state"] in {"OPEN", "CLOSED"})
        )
    else:
        valid = False
    if not valid:
        raise WorkStateUnavailable("V2_PACKET_PLAN_INVALID")
    if any(
        not value or "\x00" in value or len(value) > 1024
        for value in plan.canonical_design_identities
    ):
        raise WorkStateUnavailable("V2_PACKET_PLAN_INVALID")


def _issue_packet_sql(
    *,
    record: WorkRecord,
    packet: V2ExecutionPacket,
    run_identity: str,
    checkpoint_identity: str,
) -> str:
    plan = packet.plan
    return (
        "WITH eligible AS ("
        "SELECT w.identity FROM loop_work_records w "
        f"WHERE w.identity = {_literal(record.identity)} "
        f"AND w.repository = {_literal(record.repository)} "
        f"AND w.issue_number = {record.issue_number} "
        "AND NOT EXISTS (SELECT 1 FROM loop_effect_attempts e "
        "WHERE e.work_identity = w.identity "
        "AND e.status IN ('INTENT_RECORDED', 'UNCERTAIN')) "
        "AND NOT EXISTS (SELECT 1 FROM loop_work_leases l "
        "WHERE l.work_identity = w.identity AND l.expires_at > now()) "
        "AND (w.latest_task_packet_identity IS NULL OR EXISTS ("
        "SELECT 1 FROM loop_task_packets previous "
        "WHERE previous.identity = w.latest_task_packet_identity "
        "AND previous.status IN ('COMPLETED', 'SUPERSEDED'))) "
        "AND NOT EXISTS (SELECT 1 FROM loop_task_packets p "
        f"WHERE p.identity = {_literal(packet.identity)} "
        "OR (p.work_identity = "
        f"{_literal(packet.work_identity)} AND p.generation = {packet.generation}))"
        "), recorded_packet AS ("
        "INSERT INTO loop_task_packets "
        "(identity, work_identity, generation, transition, status, canonical_design_identities, "
        "external_target_identities, effect_kind, effect_target_identity, effect_idempotency_key, "
        "expected_preconditions, expected_effect) SELECT "
        f"{_literal(packet.identity)}, {_literal(packet.work_identity)}, {packet.generation}, "
        f"{_literal(plan.transition)}, 'ISSUED', {_json_array(plan.canonical_design_identities)}, "
        f"{_json_array((plan.target_identity,))}, {_literal(plan.effect_kind)}, "
        f"{_literal(plan.target_identity)}, {_literal(plan.idempotency_key)}, "
        f"{_json_pairs(plan.expected_preconditions)}, {_json_pairs(plan.expected_effect)} "
        "FROM eligible RETURNING identity"
        "), recorded_checkpoint AS ("
        "INSERT INTO loop_work_checkpoints "
        "(identity, work_identity, run_identity, task_packet_identity, checkpoint_kind, "
        "resumable_state, next_action, external_target_identities, evidence_identities) SELECT "
        f"{_literal(checkpoint_identity)}, {_literal(record.identity)}, {_literal(run_identity)}, "
        f"{_literal(packet.identity)}, 'SAFE_POINT', 'PACKET_ISSUED', "
        "'明示発行済みの作業パケットを実行する', "
        f"{_json_array((plan.target_identity,))}, '[]'::jsonb FROM recorded_packet "
        "RETURNING identity"
        "), updated_work AS ("
        "UPDATE loop_work_records SET "
        f"issue_revision = {_literal(record.issue_revision)}, lifecycle = 'RUNNING', "
        f"selected_transition = {_literal(plan.transition)}, "
        f"latest_task_packet_identity = {_literal(packet.identity)}, "
        f"latest_checkpoint_identity = {_literal(checkpoint_identity)}, updated_at = now() "
        "WHERE identity = (SELECT identity FROM eligible) "
        "AND EXISTS (SELECT 1 FROM recorded_checkpoint) RETURNING identity"
        ") SELECT json_build_object("
        "'issued', EXISTS (SELECT 1 FROM updated_work))::text"
    )


def _start_packet_sql(
    *,
    record: WorkRecord,
    packet: V2ExecutionPacket,
    safe_checkpoint_identity: str,
    pending_checkpoint_identity: str,
    effect: EffectAttempt,
    holder_identity: str,
    run_identity: str,
    lease_seconds: int,
) -> str:
    plan = packet.plan
    return (
        "WITH eligible AS ("
        "SELECT w.identity FROM loop_work_records w "
        "JOIN loop_task_packets p ON p.identity = w.latest_task_packet_identity "
        "JOIN loop_work_checkpoints c ON c.identity = w.latest_checkpoint_identity "
        f"WHERE w.identity = {_literal(record.identity)} "
        f"AND w.issue_revision = {_literal(record.issue_revision)} "
        f"AND p.identity = {_literal(packet.identity)} "
        f"AND p.generation = {packet.generation} AND p.status = 'ISSUED' "
        f"AND c.identity = {_literal(safe_checkpoint_identity)} "
        "AND c.checkpoint_kind = 'SAFE_POINT' "
        "AND c.task_packet_identity = p.identity "
        f"AND p.effect_kind = {_literal(plan.effect_kind)} "
        f"AND p.effect_target_identity = {_literal(plan.target_identity)} "
        f"AND p.effect_idempotency_key = {_literal(plan.idempotency_key)} "
        f"AND p.expected_preconditions = {_json_pairs(plan.expected_preconditions)} "
        f"AND p.expected_effect = {_json_pairs(plan.expected_effect)} "
        "AND NOT EXISTS (SELECT 1 FROM loop_effect_attempts e "
        "WHERE e.work_identity = w.identity "
        "AND e.status IN ('INTENT_RECORDED', 'UNCERTAIN')) "
        "AND NOT EXISTS (SELECT 1 FROM loop_effect_attempts e "
        f"WHERE e.idempotency_key = {_literal(effect.idempotency_key)}) "
        "AND NOT EXISTS (SELECT 1 FROM loop_work_checkpoints c2 "
        f"WHERE c2.identity = {_literal(pending_checkpoint_identity)})"
        "), acquired AS ("
        "INSERT INTO loop_work_leases "
        "(work_identity, holder_identity, packet_generation, expires_at) SELECT "
        f"{_literal(record.identity)}, {_literal(holder_identity)}, {packet.generation}, "
        f"now() + INTERVAL '{lease_seconds} seconds' FROM eligible "
        "ON CONFLICT (work_identity) DO UPDATE SET "
        "holder_identity = EXCLUDED.holder_identity, "
        "packet_generation = EXCLUDED.packet_generation, "
        "acquired_at = now(), expires_at = EXCLUDED.expires_at "
        "WHERE loop_work_leases.expires_at <= now() RETURNING work_identity"
        "), started_packet AS ("
        "UPDATE loop_task_packets SET status = 'STARTED' "
        f"WHERE identity = {_literal(packet.identity)} AND status = 'ISSUED' "
        "AND EXISTS (SELECT 1 FROM acquired) RETURNING identity"
        "), recorded_effect AS ("
        "INSERT INTO loop_effect_attempts "
        "(idempotency_key, work_identity, packet_generation, kind, target_identity, status, "
        "request_identity, expected_preconditions, expected_effect) SELECT "
        f"{_literal(effect.idempotency_key)}, {_literal(effect.work_identity)}, "
        f"{packet.generation}, {_literal(effect.kind)}, "
        f"{_literal(effect.target_identity)}, 'INTENT_RECORDED', NULL, "
        f"{_json_pairs(effect.expected_preconditions)}, {_json_pairs(effect.expected_effect)} "
        "FROM started_packet RETURNING idempotency_key"
        "), recorded_checkpoint AS ("
        "INSERT INTO loop_work_checkpoints "
        "(identity, work_identity, run_identity, task_packet_identity, checkpoint_kind, "
        "resumable_state, next_action, external_target_identities, evidence_identities) SELECT "
        f"{_literal(pending_checkpoint_identity)}, {_literal(record.identity)}, "
        f"{_literal(run_identity)}, {_literal(packet.identity)}, "
        "'EFFECT_PENDING', 'EFFECT_INTENT_RECORDED', "
        "'記録済み対象の外部効果を実行または読戻す', "
        f"{_json_array((plan.target_identity,))}, '[]'::jsonb FROM recorded_effect "
        "RETURNING identity"
        "), updated_work AS ("
        "UPDATE loop_work_records SET "
        f"latest_checkpoint_identity = {_literal(pending_checkpoint_identity)}, updated_at = now() "
        f"WHERE identity = {_literal(record.identity)} "
        "AND EXISTS (SELECT 1 FROM recorded_checkpoint) RETURNING identity"
        ") SELECT json_build_object("
        "'started', EXISTS (SELECT 1 FROM updated_work))::text"
    )


def _finalize_packet_sql(
    *,
    packet: V2ExecutionPacket,
    holder_identity: str,
    run_identity: str,
    checkpoint_identity: str,
    expected_effect_status: str,
) -> str:
    checkpoint_kind = (
        "EFFECT_CONFIRMED"
        if expected_effect_status == "CONFIRMED"
        else "EFFECT_NO_EFFECT"
    )
    resumable_state = (
        "EFFECT_CONFIRMED"
        if expected_effect_status == "CONFIRMED"
        else "EFFECT_NO_EFFECT"
    )
    next_action = (
        "次の作業パケットを明示発行する"
        if expected_effect_status == "CONFIRMED"
        else "必要なら新generationの作業パケットを明示発行する"
    )
    return (
        "WITH terminal_effect AS ("
        "SELECT e.status FROM loop_effect_attempts e "
        f"WHERE e.work_identity = {_literal(packet.work_identity)} "
        f"AND e.packet_generation = {packet.generation} "
        f"AND e.status = {_literal(expected_effect_status)}"
        "), eligible AS ("
        "SELECT p.identity FROM loop_task_packets p "
        "JOIN loop_work_records w ON w.identity = p.work_identity "
        "JOIN loop_work_leases l ON l.work_identity = p.work_identity "
        f"WHERE p.identity = {_literal(packet.identity)} "
        f"AND p.work_identity = {_literal(packet.work_identity)} "
        f"AND p.generation = {packet.generation} AND p.status = 'STARTED' "
        f"AND l.holder_identity = {_literal(holder_identity)} "
        f"AND l.packet_generation = {packet.generation} AND l.expires_at > now() "
        "AND (SELECT count(*) FROM terminal_effect) = 1"
        "), finalized_packet AS ("
        "UPDATE loop_task_packets SET status = CASE "
        f"WHEN {_literal(expected_effect_status)} = 'CONFIRMED' THEN 'COMPLETED' "
        "ELSE 'SUPERSEDED' END "
        "WHERE identity = (SELECT identity FROM eligible) RETURNING status"
        "), recorded_checkpoint AS ("
        "INSERT INTO loop_work_checkpoints "
        "(identity, work_identity, run_identity, task_packet_identity, checkpoint_kind, "
        "resumable_state, next_action, external_target_identities, evidence_identities) SELECT "
        f"{_literal(checkpoint_identity)}, {_literal(packet.work_identity)}, "
        f"{_literal(run_identity)}, {_literal(packet.identity)}, "
        f"{_literal(checkpoint_kind)}, {_literal(resumable_state)}, "
        f"{_literal(next_action)}, {_json_array((packet.plan.target_identity,))}, "
        "'[]'::jsonb FROM finalized_packet RETURNING identity"
        "), updated_work AS ("
        "UPDATE loop_work_records SET "
        "lifecycle = CASE WHEN "
        f"{_literal(expected_effect_status)} = 'CONFIRMED' "
        "AND EXISTS (SELECT 1 FROM loop_task_packets p2 "
        f"WHERE p2.identity = {_literal(packet.identity)} "
        "AND p2.effect_kind = 'ISSUE_UPDATE' "
        "AND p2.expected_effect ->> 'state' = 'CLOSED') "
        "THEN 'COMPLETED' ELSE lifecycle END, "
        f"latest_checkpoint_identity = {_literal(checkpoint_identity)}, updated_at = now() "
        f"WHERE identity = {_literal(packet.work_identity)} "
        "AND EXISTS (SELECT 1 FROM recorded_checkpoint) RETURNING lifecycle"
        ") SELECT json_build_object("
        "'finalized', EXISTS (SELECT 1 FROM updated_work), "
        "'packet_status', (SELECT status FROM finalized_packet LIMIT 1), "
        f"'effect_status', {_literal(expected_effect_status)}, "
        "'work_completed', COALESCE((SELECT lifecycle = 'COMPLETED' "
        "FROM updated_work LIMIT 1), false)"
        ")::text"
    )


def _execution_packet(row: dict[str, object]) -> V2ExecutionPacket:
    generation = row.get("generation")
    if not isinstance(generation, int) or generation < 1:
        raise WorkStateUnavailable("WORK_STATE_ROW_INVALID")
    status = _required_string(row, "status")
    plan = V2PacketPlan(
        transition=_required_string(row, "transition"),
        effect_kind=_required_string(row, "effect_kind"),
        target_identity=_required_string(row, "effect_target_identity"),
        idempotency_key=_required_string(row, "effect_idempotency_key"),
        expected_preconditions=_string_pairs(row, "expected_preconditions"),
        expected_effect=_string_pairs(row, "expected_effect"),
        canonical_design_identities=_string_tuple(row, "canonical_design_identities"),
    )
    _validate_plan(plan)
    return V2ExecutionPacket(
        identity=_required_string(row, "identity"),
        work_identity=_required_string(row, "work_identity"),
        generation=generation,
        status=status,
        plan=plan,
    )


def _work_record(row: dict[str, object]) -> WorkRecord:
    issue_number = row.get("issue_number")
    if not isinstance(issue_number, int) or issue_number < 1:
        raise WorkStateUnavailable("WORK_STATE_ROW_INVALID")
    return WorkRecord(
        identity=_required_string(row, "identity"),
        repository=_required_string(row, "repository"),
        issue_number=issue_number,
        issue_revision=_required_string(row, "issue_revision"),
        lifecycle=_required_string(row, "lifecycle"),
        selected_transition=_optional_string(row, "selected_transition"),
        active_lineage_identity=_optional_string(row, "active_lineage_identity"),
        latest_task_packet_identity=_optional_string(row, "latest_task_packet_identity"),
        latest_checkpoint_identity=_optional_string(row, "latest_checkpoint_identity"),
    )


def _validate_repository(repository: str) -> None:
    if repository.count("/") != 1 or "\x00" in repository or len(repository) > 200:
        raise WorkStateUnavailable("V2_REPOSITORY_INVALID")
    owner, name = repository.split("/", maxsplit=1)
    if not owner or not name or owner.strip() != owner or name.strip() != name:
        raise WorkStateUnavailable("V2_REPOSITORY_INVALID")


def _pairs(values: tuple[tuple[str, str], ...]) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for key, value in values:
        if (
            not key
            or key in result
            or "\x00" in key
            or "\x00" in value
            or len(key) > 128
            or len(value) > 1024
        ):
            return None
        result[key] = value
    return result


def _target_suffix(identity: str, prefix: str) -> str | None:
    marker = f"{prefix}:"
    if not identity.startswith(marker):
        return None
    value = identity[len(marker) :]
    if not value or "\x00" in value or len(value) > 255:
        return None
    return value


def _target_number(identity: str, prefix: str) -> int | None:
    value = _target_suffix(identity, prefix)
    if value is None or not value.isdigit():
        return None
    number = int(value)
    return number if number > 0 else None


def _literal(value: str) -> str:
    if "\x00" in value or len(value) > 4096:
        raise WorkStateUnavailable("WORK_STATE_VALUE_INVALID")
    return "'" + value.replace("'", "''") + "'"


def _json_array(values: tuple[str, ...]) -> str:
    if any("\x00" in value or len(value) > 1024 for value in values):
        raise WorkStateUnavailable("WORK_STATE_VALUE_INVALID")
    return _literal(json.dumps(values, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def _json_pairs(values: tuple[tuple[str, str], ...]) -> str:
    payload = _pairs(values)
    if payload is None:
        raise WorkStateUnavailable("WORK_STATE_VALUE_INVALID")
    return _literal(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ) + "::jsonb"


def _required_string(row: dict[str, object], name: str) -> str:
    value = _optional_string(row, name)
    if value is None:
        raise WorkStateUnavailable("WORK_STATE_ROW_INVALID")
    return value


def _optional_string(row: dict[str, object], name: str) -> str | None:
    value = row.get(name)
    return value if isinstance(value, str) else None


def _string_pairs(row: dict[str, object], name: str) -> tuple[tuple[str, str], ...]:
    value = row.get(name)
    if not isinstance(value, dict):
        raise WorkStateUnavailable("WORK_STATE_ROW_INVALID")
    pairs: list[tuple[str, str]] = []
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise WorkStateUnavailable("WORK_STATE_ROW_INVALID")
        pairs.append((key, item))
    return tuple(sorted(pairs))


def _string_tuple(row: dict[str, object], name: str) -> tuple[str, ...]:
    value = row.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkStateUnavailable("WORK_STATE_ROW_INVALID")
    return tuple(value)
