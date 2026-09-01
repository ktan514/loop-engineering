from __future__ import annotations

from dataclasses import dataclass, field

from loop_engineering.v2_effect_executor import (
    V2EffectExecutionResult,
    V2EffectExecutionStatus,
)
from loop_engineering.v2_execution_state import (
    V2ExecutionPacket,
    V2PacketFinalizationResult,
    V2PacketPlan,
    V2PacketStartResult,
)
from loop_engineering.v2_host_entrypoint import V2Host, V2HostStatus
from loop_engineering.v2_resume import (
    EffectReadbackStatus,
    V2ResumeResult,
    V2ResumeStatus,
)
from loop_engineering.work_state import (
    EffectAttempt,
    RecoveredWork,
    WorkCheckpoint,
    WorkRecord,
    WorkTaskPacket,
)


def record(*, lifecycle: str = "RUNNING") -> WorkRecord:
    return WorkRecord(
        identity="work:repo:67",
        repository="owner/repo",
        issue_number=67,
        issue_revision="definition:67",
        lifecycle=lifecycle,
        latest_task_packet_identity="packet:67:1",
        latest_checkpoint_identity="checkpoint:safe",
    )


def plan() -> V2PacketPlan:
    return V2PacketPlan(
        transition="READY_PR",
        effect_kind="READY",
        target_identity="pr:70",
        idempotency_key="effect:70:1",
        expected_preconditions=(("draft", "true"), ("head", "abc")),
        expected_effect=(("draft", "false"),),
    )


def execution_packet(*, status: str = "ISSUED") -> V2ExecutionPacket:
    return V2ExecutionPacket("packet:67:1", record().identity, 1, status, plan())


def task_packet(*, status: str = "ISSUED") -> WorkTaskPacket:
    return WorkTaskPacket("packet:67:1", record().identity, 1, "READY_PR", status)


def checkpoint(*, kind: str = "SAFE_POINT", identity: str = "checkpoint:safe") -> WorkCheckpoint:
    return WorkCheckpoint(
        identity=identity,
        work_identity=record().identity,
        run_identity="run:old",
        checkpoint_kind=kind,
        resumable_state="READY",
        next_action="次へ",
        task_packet_identity="packet:67:1",
    )


def recovered(
    *,
    packet_status: str = "ISSUED",
    checkpoint_kind: str = "SAFE_POINT",
) -> RecoveredWork:
    return RecoveredWork(
        record(),
        task_packet(status=packet_status),
        checkpoint(kind=checkpoint_kind),
        (),
    )


@dataclass
class Resume:
    result: V2ResumeResult
    calls: list[str] = field(default_factory=list)

    def resume(self, work_identity: str) -> V2ResumeResult:
        self.calls.append(work_identity)
        return self.result


@dataclass
class ExecutionState:
    packet_value: V2ExecutionPacket
    start_result: V2PacketStartResult | None = None
    finalization: V2PacketFinalizationResult | None = None
    lease_acquired: bool = True
    starts: int = 0
    lease_calls: int = 0
    finalize_calls: int = 0
    releases: int = 0

    def packet(self, packet_identity_value: str) -> V2ExecutionPacket | None:
        assert packet_identity_value == self.packet_value.identity
        return self.packet_value

    def start_packet(self, **kwargs: object) -> V2PacketStartResult | None:
        self.starts += 1
        return self.start_result

    def acquire_terminal_lease(self, **kwargs: object) -> bool:
        self.lease_calls += 1
        return self.lease_acquired

    def finalize_packet(self, **kwargs: object) -> V2PacketFinalizationResult | None:
        self.finalize_calls += 1
        return self.finalization

    def release_lease(self, work_identity: str, holder_identity: str) -> None:
        assert work_identity == record().identity
        assert holder_identity == "holder:test"
        self.releases += 1


@dataclass
class WorkState:
    outcomes: list[tuple[str, str]] = field(default_factory=list)
    reports: list[tuple[str, str]] = field(default_factory=list)
    latest: WorkCheckpoint | None = None

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None:
        self.outcomes.append((idempotency_key, status))

    def latest_checkpoint(self, work_identity: str) -> WorkCheckpoint | None:
        assert work_identity == record().identity
        return self.latest

    def enqueue_issue_report(self, **kwargs: object) -> None:
        self.reports.append((str(kwargs["report_kind"]), str(kwargs["checkpoint_identity"])))


@dataclass
class Effects:
    status: EffectReadbackStatus
    calls: int = 0

    def readback(self, attempt: EffectAttempt) -> EffectReadbackStatus:
        self.calls += 1
        return self.status


@dataclass
class Executor:
    calls: int = 0

    def execute(self, attempt: EffectAttempt) -> V2EffectExecutionResult:
        self.calls += 1
        return V2EffectExecutionResult(V2EffectExecutionStatus.EXECUTED, "EFFECT_SENT")


@dataclass(frozen=True)
class PublishResult:
    pending: int = 0


@dataclass
class Publisher:
    pending: int = 0
    calls: int = 0

    def publish_pending(self, value: WorkRecord) -> PublishResult:
        assert value.identity == record().identity
        self.calls += 1
        return PublishResult(self.pending)


def host(
    resume: Resume,
    execution: ExecutionState,
    state: WorkState,
    effects: Effects,
    executor: Executor,
    publisher: Publisher,
) -> V2Host:
    return V2Host(
        resume,
        execution,
        state,
        effects,
        executor,
        publisher,
        holder_factory=lambda: "holder:test",
        run_factory=lambda: "run:test",
    )


def test_ready_packet_executes_one_effect_then_finalizes_and_reports() -> None:
    effect = plan().effect_attempt(record().identity, 1)
    execution = ExecutionState(
        execution_packet(),
        start_result=V2PacketStartResult(effect, "checkpoint:pending"),
        finalization=V2PacketFinalizationResult(
            "COMPLETED", "CONFIRMED", "checkpoint:confirmed", False
        ),
    )
    state = WorkState()
    effects = Effects(EffectReadbackStatus.CONFIRMED)
    executor = Executor()
    publisher = Publisher()
    value = host(
        Resume(V2ResumeResult(V2ResumeStatus.READY, "RESUME_READY", recovered())),
        execution,
        state,
        effects,
        executor,
        publisher,
    ).run_once(record().identity)

    assert value.status is V2HostStatus.TRANSITION_COMPLETED
    assert value.detail == "CONFIRMED"
    assert execution.starts == 1
    assert executor.calls == 1
    assert effects.calls == 1
    assert state.outcomes == [(effect.idempotency_key, "CONFIRMED")]
    assert execution.finalize_calls == 1
    assert execution.releases == 1
    assert state.reports == [("PACKET_FINALIZED", "checkpoint:confirmed")]
    assert publisher.calls == 1


def test_unknown_readback_never_finalizes_or_retries_effect() -> None:
    effect = plan().effect_attempt(record().identity, 1)
    execution = ExecutionState(
        execution_packet(),
        start_result=V2PacketStartResult(effect, "checkpoint:pending"),
    )
    state = WorkState()
    executor = Executor()
    value = host(
        Resume(V2ResumeResult(V2ResumeStatus.READY, "RESUME_READY", recovered())),
        execution,
        state,
        Effects(EffectReadbackStatus.UNKNOWN),
        executor,
        Publisher(),
    ).run_once(record().identity)

    assert value.status is V2HostStatus.RECONCILE_REQUIRED
    assert state.outcomes == [(effect.idempotency_key, "UNCERTAIN")]
    assert executor.calls == 1
    assert execution.finalize_calls == 0
    assert execution.releases == 1


def test_started_packet_recovery_finalizes_without_executor() -> None:
    execution = ExecutionState(
        execution_packet(status="STARTED"),
        finalization=V2PacketFinalizationResult(
            "COMPLETED", "CONFIRMED", "checkpoint:confirmed", False
        ),
    )
    executor = Executor()
    value = host(
        Resume(
            V2ResumeResult(
                V2ResumeStatus.FINALIZE_REQUIRED,
                "PACKET_FINALIZATION_REQUIRED",
                recovered(packet_status="STARTED", checkpoint_kind="EFFECT_PENDING"),
            )
        ),
        execution,
        WorkState(),
        Effects(EffectReadbackStatus.CONFIRMED),
        executor,
        Publisher(),
    ).run_once(record().identity)

    assert value.status is V2HostStatus.TRANSITION_COMPLETED
    assert execution.lease_calls == 1
    assert execution.finalize_calls == 1
    assert executor.calls == 0
    assert execution.releases == 1


def test_terminal_packet_only_recovers_outbox_and_never_executes() -> None:
    terminal_checkpoint = checkpoint(kind="EFFECT_CONFIRMED", identity="checkpoint:confirmed")
    terminal_record = WorkRecord(
        identity=record().identity,
        repository=record().repository,
        issue_number=67,
        issue_revision=record().issue_revision,
        lifecycle="RUNNING",
        latest_task_packet_identity="packet:67:1",
        latest_checkpoint_identity="checkpoint:confirmed",
    )
    terminal_recovered = RecoveredWork(
        terminal_record,
        task_packet(status="COMPLETED"),
        terminal_checkpoint,
        (),
    )
    execution = ExecutionState(execution_packet(status="COMPLETED"))
    state = WorkState(latest=terminal_checkpoint)
    executor = Executor()
    publisher = Publisher()

    value = host(
        Resume(V2ResumeResult(V2ResumeStatus.WAITING, "PACKET_TERMINAL", terminal_recovered)),
        execution,
        state,
        Effects(EffectReadbackStatus.CONFIRMED),
        executor,
        publisher,
    ).run_once(record().identity)

    assert value.status is V2HostStatus.WAITING
    assert value.detail == "PACKET_TERMINAL"
    assert executor.calls == 0
    assert execution.starts == 0
    assert execution.lease_calls == 1
    assert publisher.calls == 1
    assert execution.releases == 1


def test_lease_conflict_blocks_recovery_without_effect() -> None:
    execution = ExecutionState(execution_packet(status="STARTED"), lease_acquired=False)
    executor = Executor()
    value = host(
        Resume(
            V2ResumeResult(
                V2ResumeStatus.FINALIZE_REQUIRED,
                "PACKET_FINALIZATION_REQUIRED",
                recovered(packet_status="STARTED", checkpoint_kind="EFFECT_PENDING"),
            )
        ),
        execution,
        WorkState(),
        Effects(EffectReadbackStatus.CONFIRMED),
        executor,
        Publisher(),
    ).run_once(record().identity)

    assert value.status is V2HostStatus.WAITING
    assert value.detail == "WORK_LEASE_HELD"
    assert executor.calls == 0
    assert execution.finalize_calls == 0
    assert execution.releases == 0
