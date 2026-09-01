"""旧actual-hostから分離したV2の1作業パケット実行入口。"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from .v2_effect_executor import V2EffectExecutionResult
from .v2_execution_state import (
    V2ExecutionPacket,
    V2PacketFinalizationResult,
    V2PacketStartResult,
)
from .v2_resume import (
    EffectReadbackPort,
    EffectReadbackStatus,
    V2ResumeResult,
    V2ResumeStatus,
)
from .work_state import EffectAttempt, WorkCheckpoint, WorkRecord, WorkStateUnavailable


class V2HostStatus(str, Enum):
    TRANSITION_COMPLETED = "TRANSITION_COMPLETED"
    WORK_COMPLETED = "WORK_COMPLETED"
    WAITING = "WAITING"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class V2HostResult:
    status: V2HostStatus
    detail: str
    work_identity: str
    packet_identity: str | None = None

    def as_json(self) -> str:
        return json.dumps(
            {
                "status": self.status.value,
                "detail": self.detail,
                "work_identity": self.work_identity,
                "packet_identity": self.packet_identity,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


class V2ResumePort(Protocol):
    def resume(self, work_identity: str) -> V2ResumeResult: ...


class V2ExecutionStatePort(Protocol):
    def packet(self, packet_identity_value: str) -> V2ExecutionPacket | None: ...

    def start_packet(
        self,
        *,
        record: WorkRecord,
        packet: V2ExecutionPacket,
        safe_checkpoint_identity: str,
        holder_identity: str,
        run_identity: str,
        lease_seconds: int = 300,
    ) -> V2PacketStartResult | None: ...

    def acquire_terminal_lease(
        self,
        *,
        work_identity: str,
        packet_generation: int,
        holder_identity: str,
        lease_seconds: int = 300,
    ) -> bool: ...

    def finalize_packet(
        self,
        *,
        packet: V2ExecutionPacket,
        holder_identity: str,
        run_identity: str,
    ) -> V2PacketFinalizationResult | None: ...

    def release_lease(self, work_identity: str, holder_identity: str) -> None: ...


class V2WorkStatePort(Protocol):
    def record_effect_outcome(self, idempotency_key: str, status: str) -> None: ...

    def latest_checkpoint(self, work_identity: str) -> WorkCheckpoint | None: ...

    def enqueue_issue_report(
        self,
        *,
        identity: str,
        work_identity: str,
        report_kind: str,
        checkpoint_identity: str | None,
        body: str,
    ) -> None: ...


class V2EffectExecutorPort(Protocol):
    def execute(self, attempt: EffectAttempt) -> V2EffectExecutionResult: ...


class V2IssueReportPublisherPort(Protocol):
    def publish_pending(self, record: WorkRecord) -> object: ...


@dataclass(slots=True)
class V2Host:
    resume_port: V2ResumePort
    execution_state: V2ExecutionStatePort
    work_state: V2WorkStatePort
    effects: EffectReadbackPort
    executor: V2EffectExecutorPort
    publisher: V2IssueReportPublisherPort
    holder_factory: Callable[[], str] = lambda: f"holder:{uuid.uuid4().hex}"
    run_factory: Callable[[], str] = lambda: f"run:{uuid.uuid4().hex}"
    lease_seconds: int = 300

    def run_once(self, work_identity: str) -> V2HostResult:
        try:
            decision = self.resume_port.resume(work_identity)
            if decision.status is V2ResumeStatus.READY:
                return self._execute_ready(work_identity, decision)
            if decision.status is V2ResumeStatus.FINALIZE_REQUIRED:
                return self._recover_finalization(work_identity, decision)
            if decision.status is V2ResumeStatus.COMPLETED:
                return V2HostResult(V2HostStatus.WORK_COMPLETED, decision.detail, work_identity)
            if decision.status is V2ResumeStatus.WAITING:
                if decision.detail == "PACKET_TERMINAL":
                    report_result = self._recover_terminal_report(work_identity, decision)
                    if report_result is not None:
                        return report_result
                return V2HostResult(V2HostStatus.WAITING, decision.detail, work_identity)
            if decision.status is V2ResumeStatus.RECONCILE_REQUIRED:
                return V2HostResult(
                    V2HostStatus.RECONCILE_REQUIRED,
                    decision.detail,
                    work_identity,
                )
            return V2HostResult(V2HostStatus.BLOCKED, decision.detail, work_identity)
        except WorkStateUnavailable as error:
            return V2HostResult(V2HostStatus.BLOCKED, str(error), work_identity)

    def _execute_ready(self, work_identity: str, decision: V2ResumeResult) -> V2HostResult:
        recovered = decision.recovered
        if recovered is None or recovered.task_packet is None or recovered.checkpoint is None:
            return V2HostResult(V2HostStatus.BLOCKED, "WORK_RECOVERY_MISSING", work_identity)
        packet = self.execution_state.packet(recovered.task_packet.identity)
        if (
            packet is None
            or packet.status != "ISSUED"
            or packet.generation != recovered.task_packet.generation
            or packet.work_identity != work_identity
        ):
            return V2HostResult(
                V2HostStatus.RECONCILE_REQUIRED,
                "PACKET_PLAN_MISMATCH",
                work_identity,
                recovered.task_packet.identity,
            )
        holder = self.holder_factory()
        run_identity = self.run_factory()
        started = self.execution_state.start_packet(
            record=recovered.record,
            packet=packet,
            safe_checkpoint_identity=recovered.checkpoint.identity,
            holder_identity=holder,
            run_identity=run_identity,
            lease_seconds=self.lease_seconds,
        )
        if started is None:
            return V2HostResult(
                V2HostStatus.BLOCKED,
                "PACKET_START_TRANSACTION_REJECTED",
                work_identity,
                packet.identity,
            )
        try:
            self.executor.execute(started.effect)
            readback = self.effects.readback(started.effect)
            if readback is EffectReadbackStatus.CONFIRMED:
                self.work_state.record_effect_outcome(started.effect.idempotency_key, "CONFIRMED")
            elif readback is EffectReadbackStatus.NO_EFFECT:
                self.work_state.record_effect_outcome(started.effect.idempotency_key, "NO_EFFECT")
            else:
                self.work_state.record_effect_outcome(started.effect.idempotency_key, "UNCERTAIN")
                return V2HostResult(
                    V2HostStatus.RECONCILE_REQUIRED,
                    "EFFECT_READBACK_UNKNOWN",
                    work_identity,
                    packet.identity,
                )
            started_packet = replace(packet, status="STARTED")
            finalization = self.execution_state.finalize_packet(
                packet=started_packet,
                holder_identity=holder,
                run_identity=self.run_factory(),
            )
            if finalization is None:
                return V2HostResult(
                    V2HostStatus.RECONCILE_REQUIRED,
                    "PACKET_FINALIZATION_FAILED",
                    work_identity,
                    packet.identity,
                )
            report_pending = self._publish_terminal_report(recovered.record, finalization)
            if report_pending:
                return V2HostResult(
                    V2HostStatus.WAITING,
                    "ISSUE_REPORT_PENDING",
                    work_identity,
                    packet.identity,
                )
            return V2HostResult(
                V2HostStatus.TRANSITION_COMPLETED,
                finalization.effect_status,
                work_identity,
                packet.identity,
            )
        finally:
            self.execution_state.release_lease(work_identity, holder)

    def _recover_finalization(
        self,
        work_identity: str,
        decision: V2ResumeResult,
    ) -> V2HostResult:
        recovered = decision.recovered
        if recovered is None or recovered.task_packet is None:
            return V2HostResult(V2HostStatus.BLOCKED, "WORK_RECOVERY_MISSING", work_identity)
        packet = self.execution_state.packet(recovered.task_packet.identity)
        if packet is None or packet.status != "STARTED":
            return V2HostResult(
                V2HostStatus.RECONCILE_REQUIRED,
                "PACKET_PLAN_MISMATCH",
                work_identity,
                recovered.task_packet.identity,
            )
        holder = self.holder_factory()
        acquired = self.execution_state.acquire_terminal_lease(
            work_identity=work_identity,
            packet_generation=packet.generation,
            holder_identity=holder,
            lease_seconds=self.lease_seconds,
        )
        if not acquired:
            return V2HostResult(
                V2HostStatus.WAITING,
                "WORK_LEASE_HELD",
                work_identity,
                packet.identity,
            )
        try:
            finalization = self.execution_state.finalize_packet(
                packet=packet,
                holder_identity=holder,
                run_identity=self.run_factory(),
            )
            if finalization is None:
                return V2HostResult(
                    V2HostStatus.RECONCILE_REQUIRED,
                    "FINALIZATION_EVIDENCE_MISSING",
                    work_identity,
                    packet.identity,
                )
            report_pending = self._publish_terminal_report(recovered.record, finalization)
            if report_pending:
                return V2HostResult(
                    V2HostStatus.WAITING,
                    "ISSUE_REPORT_PENDING",
                    work_identity,
                    packet.identity,
                )
            return V2HostResult(
                V2HostStatus.TRANSITION_COMPLETED,
                finalization.effect_status,
                work_identity,
                packet.identity,
            )
        finally:
            self.execution_state.release_lease(work_identity, holder)

    def _recover_terminal_report(
        self,
        work_identity: str,
        decision: V2ResumeResult,
    ) -> V2HostResult | None:
        recovered = decision.recovered
        if recovered is None or recovered.task_packet is None:
            return None
        packet = self.execution_state.packet(recovered.task_packet.identity)
        if packet is None or packet.status not in {"COMPLETED", "SUPERSEDED"}:
            return V2HostResult(
                V2HostStatus.RECONCILE_REQUIRED,
                "PACKET_PLAN_MISMATCH",
                work_identity,
                recovered.task_packet.identity,
            )
        holder = self.holder_factory()
        if not self.execution_state.acquire_terminal_lease(
            work_identity=work_identity,
            packet_generation=packet.generation,
            holder_identity=holder,
            lease_seconds=self.lease_seconds,
        ):
            return V2HostResult(
                V2HostStatus.WAITING,
                "WORK_LEASE_HELD",
                work_identity,
                packet.identity,
            )
        try:
            checkpoint = self.work_state.latest_checkpoint(work_identity)
            if checkpoint is None or checkpoint.task_packet_identity != packet.identity:
                return V2HostResult(
                    V2HostStatus.RECONCILE_REQUIRED,
                    "TERMINAL_CHECKPOINT_MISSING",
                    work_identity,
                    packet.identity,
                )
            effect_status = "CONFIRMED" if packet.status == "COMPLETED" else "NO_EFFECT"
            finalization = V2PacketFinalizationResult(
                packet.status,
                effect_status,
                checkpoint.identity,
                recovered.record.lifecycle == "COMPLETED",
            )
            if self._publish_terminal_report(recovered.record, finalization):
                return V2HostResult(
                    V2HostStatus.WAITING,
                    "ISSUE_REPORT_PENDING",
                    work_identity,
                    packet.identity,
                )
            return None
        finally:
            self.execution_state.release_lease(work_identity, holder)

    def _publish_terminal_report(
        self,
        record: WorkRecord,
        finalization: V2PacketFinalizationResult,
    ) -> bool:
        report_identity = _report_identity(
            record.identity,
            finalization.checkpoint_identity,
            "PACKET_FINALIZED",
        )
        body = (
            "V2の作業パケット遷移をDBへ確定しました。\n\n"
            f"- 外部効果の確認結果: `{finalization.effect_status}`\n"
            "- 次の外部効果が必要な場合は、新しいgenerationの作業パケットを明示発行してください。"
        )
        self.work_state.enqueue_issue_report(
            identity=report_identity,
            work_identity=record.identity,
            report_kind="PACKET_FINALIZED",
            checkpoint_identity=finalization.checkpoint_identity,
            body=body,
        )
        publish_result = self.publisher.publish_pending(record)
        pending = getattr(publish_result, "pending", None)
        return not isinstance(pending, int) or pending > 0


def _report_identity(work_identity: str, checkpoint_identity: str, report_kind: str) -> str:
    digest = hashlib.sha256(
        f"{work_identity}\0{checkpoint_identity}\0{report_kind}".encode()
    ).hexdigest()
    return f"report:{digest}"
