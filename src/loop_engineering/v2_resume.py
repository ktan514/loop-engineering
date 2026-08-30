"""V2のDB起点再開判定を提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .work_state import EffectAttempt, RecoveredWork, WorkRecord


class V2ResumeStatus(str, Enum):
    READY = "READY"
    COMPLETED = "COMPLETED"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"


class EffectReadbackStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    NO_EFFECT = "NO_EFFECT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class V2ResumeResult:
    status: V2ResumeStatus
    detail: str
    recovered: RecoveredWork | None = None


class WorkDefinitionStatus(str, Enum):
    READY = "READY"
    COMPLETED = "COMPLETED"
    UNAVAILABLE = "UNAVAILABLE"
    CLOSED_BEFORE_COMPLETION = "CLOSED_BEFORE_COMPLETION"
    DEPENDENCY_PENDING = "DEPENDENCY_PENDING"


@dataclass(frozen=True, slots=True)
class WorkDefinitionResult:
    status: WorkDefinitionStatus
    record: WorkRecord | None = None


class WorkRecoveryPort(Protocol):
    def recover(self, work_identity: str) -> RecoveredWork | None: ...

    def upsert_work(self, record: WorkRecord) -> None: ...

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None: ...


class WorkDefinitionPort(Protocol):
    """IssueとProjectから作業定義だけを同期する。"""

    def synchronize(self, record: WorkRecord) -> WorkDefinitionResult: ...


class EffectReadbackPort(Protocol):
    """DBに記録済みの外部effectだけを照合する。"""

    def readback(self, attempt: EffectAttempt) -> EffectReadbackStatus: ...


class V2ResumeCoordinator:
    """DBを起点に、安全な実行開始可否だけを判定する。"""

    def __init__(
        self,
        recovery: WorkRecoveryPort,
        definitions: WorkDefinitionPort,
        effects: EffectReadbackPort,
    ) -> None:
        self._recovery = recovery
        self._definitions = definitions
        self._effects = effects

    def resume(self, work_identity: str) -> V2ResumeResult:
        recovered = self._recovery.recover(work_identity)
        if recovered is None or not _complete_recovery(recovered):
            return V2ResumeResult(V2ResumeStatus.BLOCKED, "WORK_RECOVERY_MISSING", recovered)

        definition = self._definitions.synchronize(recovered.record)
        if definition.status is WorkDefinitionStatus.COMPLETED:
            return V2ResumeResult(V2ResumeStatus.COMPLETED, "WORK_COMPLETED", recovered)
        if definition.status is WorkDefinitionStatus.DEPENDENCY_PENDING:
            return V2ResumeResult(V2ResumeStatus.WAITING, "DEPENDENCY_PENDING", recovered)
        if definition.status is WorkDefinitionStatus.CLOSED_BEFORE_COMPLETION:
            return V2ResumeResult(
                V2ResumeStatus.BLOCKED, "WORK_CLOSED_BEFORE_COMPLETION", recovered
            )
        synchronized = definition.record
        if definition.status is not WorkDefinitionStatus.READY or synchronized is None:
            return V2ResumeResult(
                V2ResumeStatus.BLOCKED,
                "WORK_DEFINITION_UNAVAILABLE",
                recovered,
            )
        if not _same_work(recovered.record, synchronized):
            return V2ResumeResult(
                V2ResumeStatus.BLOCKED,
                "WORK_DEFINITION_CONFLICT",
                recovered,
            )
        self._recovery.upsert_work(synchronized)

        for attempt in recovered.pending_effects:
            readback = self._effects.readback(attempt)
            if readback is EffectReadbackStatus.CONFIRMED:
                self._recovery.record_effect_outcome(attempt.idempotency_key, "CONFIRMED")
                continue
            if readback is EffectReadbackStatus.NO_EFFECT:
                self._recovery.record_effect_outcome(attempt.idempotency_key, "NO_EFFECT")
                continue
            return V2ResumeResult(
                V2ResumeStatus.RECONCILE_REQUIRED,
                "EFFECT_READBACK_UNKNOWN",
                recovered,
            )

        return V2ResumeResult(V2ResumeStatus.READY, "RESUME_READY", recovered)


def _complete_recovery(recovered: RecoveredWork) -> bool:
    packet = recovered.task_packet
    checkpoint = recovered.checkpoint
    if packet is None or checkpoint is None:
        return False
    record = recovered.record
    return (
        record.latest_task_packet_identity == packet.identity
        and record.latest_checkpoint_identity == checkpoint.identity
        and packet.work_identity == record.identity
        and checkpoint.work_identity == record.identity
        and checkpoint.task_packet_identity == packet.identity
    )


def _same_work(before: WorkRecord, after: WorkRecord) -> bool:
    return (
        before.identity == after.identity
        and before.repository == after.repository
        and before.issue_number == after.issue_number
    )
