"""V2のDB起点再開判定を提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .work_state import EffectAttempt, RecoveredWork, WorkRecord


class V2ResumeStatus(str, Enum):
    READY = "READY"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
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


class WorkRecoveryPort(Protocol):
    def recover(self, work_identity: str) -> RecoveredWork | None: ...

    def upsert_work(self, record: WorkRecord) -> None: ...

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None: ...


class WorkDefinitionPort(Protocol):
    """IssueとProjectから作業定義だけを同期する。"""

    def synchronize(self, record: WorkRecord) -> WorkRecord | None: ...


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
        if recovered is None:
            return V2ResumeResult(V2ResumeStatus.BLOCKED, "WORK_RECOVERY_MISSING")

        synchronized = self._definitions.synchronize(recovered.record)
        if synchronized is None:
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


def _same_work(before: WorkRecord, after: WorkRecord) -> bool:
    return (
        before.identity == after.identity
        and before.repository == after.repository
        and before.issue_number == after.issue_number
    )
