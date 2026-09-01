from __future__ import annotations

from dataclasses import dataclass, field

from loop_engineering.v2_resume import (
    EffectReadbackStatus,
    V2ResumeCoordinator,
    V2ResumeStatus,
    WorkDefinitionResult,
    WorkDefinitionStatus,
)
from loop_engineering.work_state import (
    EffectAttempt,
    RecoveredWork,
    WorkCheckpoint,
    WorkRecord,
    WorkTaskPacket,
)


def record() -> WorkRecord:
    return WorkRecord(
        identity="work:ktan514/loop-engineering:62",
        repository="ktan514/loop-engineering",
        issue_number=62,
        issue_revision="issue:62:1",
        lifecycle="SELECTED",
        selected_transition="IMPLEMENT",
        latest_task_packet_identity="packet:62:1",
        latest_checkpoint_identity="checkpoint:62:1",
    )


def packet() -> WorkTaskPacket:
    return WorkTaskPacket(
        identity="packet:62:1",
        work_identity=record().identity,
        generation=1,
        transition="IMPLEMENT",
        status="ISSUED",
    )


def checkpoint() -> WorkCheckpoint:
    return WorkCheckpoint(
        identity="checkpoint:62:1",
        work_identity=record().identity,
        run_identity="run:62:1",
        checkpoint_kind="SAFE_POINT",
        resumable_state="IMPLEMENT_READY",
        next_action="作業パケットを実行する",
        task_packet_identity=packet().identity,
    )


@dataclass
class Recovery:
    value: RecoveredWork | None
    synchronized: list[WorkRecord] = field(default_factory=list)
    outcomes: list[tuple[str, str]] = field(default_factory=list)

    def recover(self, work_identity: str) -> RecoveredWork | None:
        assert work_identity == record().identity
        return self.value

    def upsert_work(self, value: WorkRecord) -> None:
        self.synchronized.append(value)

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None:
        self.outcomes.append((idempotency_key, status))


@dataclass
class Definitions:
    value: WorkRecord | None
    calls: int = 0

    def synchronize(self, current: WorkRecord) -> WorkDefinitionResult:
        assert current == record()
        self.calls += 1
        status = (
            WorkDefinitionStatus.READY
            if self.value is not None
            else WorkDefinitionStatus.UNAVAILABLE
        )
        return WorkDefinitionResult(
            status,
            self.value,
        )


@dataclass
class Effects:
    status: EffectReadbackStatus
    calls: list[EffectAttempt] = field(default_factory=list)

    def readback(self, attempt: EffectAttempt) -> EffectReadbackStatus:
        self.calls.append(attempt)
        return self.status


def recovered(
    *,
    pending: tuple[EffectAttempt, ...] = (),
    task_packet: WorkTaskPacket | None = None,
    safe_checkpoint: WorkCheckpoint | None = None,
) -> RecoveredWork:
    return RecoveredWork(
        record(),
        packet() if task_packet is None else task_packet,
        checkpoint() if safe_checkpoint is None else safe_checkpoint,
        pending,
    )


def test_resume_is_ready_after_complete_db_recovery_and_definition_sync() -> None:
    recovery = Recovery(recovered())
    definitions = Definitions(record())
    effects = Effects(EffectReadbackStatus.CONFIRMED)

    result = V2ResumeCoordinator(recovery, definitions, effects).resume(record().identity)

    assert result.status is V2ResumeStatus.READY
    assert result.detail == "RESUME_READY"
    assert recovery.synchronized == [record()]
    assert effects.calls == []


def test_missing_or_inconsistent_recovery_stays_blocked_before_definition_sync() -> None:
    definitions = Definitions(record())
    missing_packet = RecoveredWork(record(), None, checkpoint(), ())

    result = V2ResumeCoordinator(
        Recovery(missing_packet),
        definitions,
        Effects(EffectReadbackStatus.CONFIRMED),
    ).resume(record().identity)

    assert result.status is V2ResumeStatus.BLOCKED
    assert result.detail == "WORK_RECOVERY_MISSING"
    assert definitions.calls == 0

    mismatched_checkpoint = WorkCheckpoint(
        identity="checkpoint:62:1",
        work_identity=record().identity,
        run_identity="run:62:1",
        checkpoint_kind="SAFE_POINT",
        resumable_state="IMPLEMENT_READY",
        next_action="作業パケットを実行する",
        task_packet_identity="packet:other",
    )
    inconsistent = V2ResumeCoordinator(
        Recovery(RecoveredWork(record(), packet(), mismatched_checkpoint, ())),
        Definitions(record()),
        Effects(EffectReadbackStatus.CONFIRMED),
    ).resume(record().identity)

    assert inconsistent.status is V2ResumeStatus.BLOCKED
    assert inconsistent.detail == "WORK_RECOVERY_MISSING"


def test_effect_packet_generation_mismatch_stops_before_any_provider_read() -> None:
    mismatch = EffectAttempt(
        "effect:62:old",
        record().identity,
        "PUSH",
        "branch:feature/v2",
        "UNCERTAIN",
        packet_generation=2,
        expected_preconditions=(("head", "before"),),
        expected_effect=(("head", "after"),),
    )
    definitions = Definitions(record())
    effects = Effects(EffectReadbackStatus.CONFIRMED)
    recovery = Recovery(recovered(pending=(mismatch,)))

    result = V2ResumeCoordinator(recovery, definitions, effects).resume(record().identity)

    assert result.status is V2ResumeStatus.RECONCILE_REQUIRED
    assert result.detail == "EFFECT_PACKET_MISMATCH"
    assert definitions.calls == 0
    assert effects.calls == []
    assert recovery.synchronized == []
    assert recovery.outcomes == []

    missing_generation = EffectAttempt(
        "effect:62:legacy",
        record().identity,
        "PUSH",
        "branch:feature/v2",
        "UNCERTAIN",
        expected_preconditions=(("head", "before"),),
        expected_effect=(("head", "after"),),
    )
    result = V2ResumeCoordinator(
        Recovery(recovered(pending=(missing_generation,))),
        Definitions(record()),
        Effects(EffectReadbackStatus.CONFIRMED),
    ).resume(record().identity)
    assert result.status is V2ResumeStatus.RECONCILE_REQUIRED
    assert result.detail == "EFFECT_PACKET_MISMATCH"


def test_unknown_effect_stops_before_any_new_execution() -> None:
    attempt = EffectAttempt(
        "effect:62:1",
        record().identity,
        "MERGE",
        "pr:63",
        "UNCERTAIN",
        packet_generation=1,
        expected_preconditions=(("head", "abc"), ("base", "main"), ("state", "OPEN")),
        expected_effect=(("state", "MERGED"),),
    )
    recovery = Recovery(recovered(pending=(attempt,)))
    effects = Effects(EffectReadbackStatus.UNKNOWN)

    result = V2ResumeCoordinator(recovery, Definitions(record()), effects).resume(record().identity)

    assert result.status is V2ResumeStatus.RECONCILE_REQUIRED
    assert result.detail == "EFFECT_READBACK_UNKNOWN"
    assert recovery.outcomes == []
    assert effects.calls == [attempt]


def test_confirmed_and_no_effect_are_recorded_before_ready() -> None:
    attempt = EffectAttempt(
        "effect:62:1",
        record().identity,
        "PUSH",
        "branch:feature/v2",
        "INTENT_RECORDED",
        packet_generation=1,
        expected_preconditions=(("head", "before"),),
        expected_effect=(("head", "after"),),
    )
    recovery = Recovery(recovered(pending=(attempt,)))

    confirmed = V2ResumeCoordinator(
        recovery,
        Definitions(record()),
        Effects(EffectReadbackStatus.CONFIRMED),
    ).resume(record().identity)

    assert confirmed.status is V2ResumeStatus.READY
    assert recovery.outcomes == [(attempt.idempotency_key, "CONFIRMED")]

    recovery = Recovery(recovered(pending=(attempt,)))
    no_effect = V2ResumeCoordinator(
        recovery,
        Definitions(record()),
        Effects(EffectReadbackStatus.NO_EFFECT),
    ).resume(record().identity)

    assert no_effect.status is V2ResumeStatus.READY
    assert recovery.outcomes == [(attempt.idempotency_key, "NO_EFFECT")]


def test_missing_or_conflicting_work_stays_blocked() -> None:
    missing = V2ResumeCoordinator(
        Recovery(None),
        Definitions(record()),
        Effects(EffectReadbackStatus.CONFIRMED),
    ).resume(record().identity)
    assert missing.status is V2ResumeStatus.BLOCKED
    assert missing.detail == "WORK_RECOVERY_MISSING"

    conflicting = WorkRecord(
        identity="work:other:62",
        repository=record().repository,
        issue_number=62,
        issue_revision="issue:62:2",
        lifecycle="SELECTED",
    )
    recovery = Recovery(recovered())
    conflict = V2ResumeCoordinator(
        recovery,
        Definitions(conflicting),
        Effects(EffectReadbackStatus.CONFIRMED),
    ).resume(record().identity)

    assert conflict.status is V2ResumeStatus.BLOCKED
    assert conflict.detail == "WORK_DEFINITION_CONFLICT"
    assert recovery.synchronized == []
