from __future__ import annotations

from dataclasses import dataclass, field

from loop_engineering.host_runtime import HostTarget, HostTransitionResult, HostTransitionStatus
from loop_engineering.runtime_operational_state import (
    DurableHostTransitionCoordinator,
    OperationalStateUnavailable,
    UnfinishedRun,
)


def target(*, checkpoint: int = 100, head: str | None = None) -> HostTarget:
    return HostTarget(
        work_issue=339,
        issue_open=True,
        pr_number=500 if head is not None else None,
        head_sha=head,
        draft=False,
        merged=False,
        checkpoint_comment_id=checkpoint,
        checkpoint_head_sha=head,
    )


@dataclass
class FakeMission:
    value: HostTarget | None
    calls: int = 0

    def current_target(self) -> HostTarget | None:
        self.calls += 1
        return self.value


@dataclass
class FakeController:
    result: HostTransitionResult
    calls: int = 0

    def run_once(self) -> HostTransitionResult:
        self.calls += 1
        return self.result


@dataclass
class FakeStore:
    previous: UnfinishedRun | None = None
    lease_available: bool = True
    fail_on_latest: bool = False
    begun: list[str] = field(default_factory=list)
    finished: list[tuple[str, str]] = field(default_factory=list)
    reconciled: list[str] = field(default_factory=list)
    checkpoints: list[tuple[str, HostTarget | None]] = field(default_factory=list)
    transitions: list[HostTransitionResult] = field(default_factory=list)
    acquired: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)
    resolved: list[tuple[str, str, str]] = field(default_factory=list)
    blockers: list[HostTransitionResult] = field(default_factory=list)
    waits: list[HostTransitionResult] = field(default_factory=list)

    def latest_unfinished(self, project_key: str, repository: str) -> UnfinishedRun | None:
        del project_key, repository
        if self.fail_on_latest:
            raise OperationalStateUnavailable("read failed")
        return self.previous

    def begin_run(self, run_identity: str, project_key: str, repository: str) -> None:
        del project_key, repository
        self.begun.append(run_identity)

    def finish_run(self, run_identity: str, status: str) -> None:
        self.finished.append((run_identity, status))

    def mark_reconciled(self, run_identity: str) -> None:
        self.reconciled.append(run_identity)

    def record_checkpoint(
        self,
        run_identity: str,
        project_key: str,
        observed: HostTarget | None,
    ) -> None:
        del project_key
        self.checkpoints.append((run_identity, observed))

    def record_transition(
        self,
        run_identity: str,
        sequence_number: int,
        result: HostTransitionResult,
    ) -> None:
        del run_identity, sequence_number
        self.transitions.append(result)

    def acquire_lease(self, project_key: str, run_identity: str) -> bool:
        del project_key
        self.acquired.append(run_identity)
        return self.lease_available

    def release_lease(self, project_key: str, run_identity: str) -> None:
        del project_key
        self.released.append(run_identity)

    def resolve_open_states(
        self,
        project_key: str,
        repository: str,
        current_run_identity: str,
    ) -> None:
        self.resolved.append((project_key, repository, current_run_identity))

    def record_blocker(self, run_identity: str, result: HostTransitionResult) -> None:
        del run_identity
        self.blockers.append(result)

    def record_external_wait(self, run_identity: str, result: HostTransitionResult) -> None:
        del run_identity
        self.waits.append(result)


def coordinator(
    mission: FakeMission,
    controller: FakeController,
    store: FakeStore,
    *,
    required: bool = True,
) -> DurableHostTransitionCoordinator:
    return DurableHostTransitionCoordinator(
        project_key="ai-liver-yura",
        repository="ktan514/ai-liver-yura",
        mission=mission,
        controller=controller,
        store=store,
        required=required,
    )


def test_completed_transition_is_durably_recorded_and_lease_released() -> None:
    observed = target()
    result = HostTransitionResult(
        HostTransitionStatus.COMPLETED,
        "IMPLEMENTER_DISPATCHED",
        339,
    )
    mission = FakeMission(observed)
    controller = FakeController(result)
    store = FakeStore()

    actual = coordinator(mission, controller, store).run_once()

    assert actual == result
    assert controller.calls == 1
    assert len(store.begun) == 1
    assert store.checkpoints[0][1] == observed
    assert store.transitions == [result]
    assert store.resolved == [
        ("ai-liver-yura", "ktan514/ai-liver-yura", store.begun[0])
    ]
    assert store.finished[0][1] == "COMPLETED"
    assert store.released == store.acquired


def test_same_checkpoint_unfinished_run_blocks_duplicate_side_effect() -> None:
    observed = target(checkpoint=123, head="a" * 40)
    previous = UnfinishedRun(
        "old-run",
        "123",
        339,
        500,
        "a" * 40,
        None,
    )
    mission = FakeMission(observed)
    controller = FakeController(
        HostTransitionResult(
            HostTransitionStatus.COMPLETED,
            "SHOULD_NOT_RUN",
        )
    )
    store = FakeStore(previous=previous)

    actual = coordinator(mission, controller, store).run_once()

    assert actual.status is HostTransitionStatus.INTERVENTION_REQUIRED
    assert actual.detail == "OPERATIONAL_STATE_UNCERTAIN"
    assert controller.calls == 0
    assert store.begun == []
    assert store.resolved == []
    assert store.blockers[-1].detail == "OPERATIONAL_STATE_UNCERTAIN"


def test_advanced_github_checkpoint_reconciles_old_run_then_continues() -> None:
    previous = UnfinishedRun(
        "old-run",
        "123",
        339,
        500,
        "a" * 40,
        None,
    )
    observed = target(checkpoint=124, head="b" * 40)
    result = HostTransitionResult(
        HostTransitionStatus.YIELD_EXTERNAL,
        "CI_PENDING",
        339,
        500,
        "b" * 40,
    )
    mission = FakeMission(observed)
    controller = FakeController(result)
    store = FakeStore(previous=previous)

    actual = coordinator(mission, controller, store).run_once()

    assert actual == result
    assert store.reconciled == ["old-run"]
    assert "old-run" in store.released
    assert controller.calls == 1
    assert len(store.resolved) == 1
    assert store.waits == [result]


def test_terminal_transition_on_unfinished_run_is_closed_without_duplicate() -> None:
    previous = UnfinishedRun(
        "old-run",
        "123",
        339,
        500,
        "a" * 40,
        "YIELD_EXTERNAL",
    )
    result = HostTransitionResult(
        HostTransitionStatus.YIELD_EXTERNAL,
        "CI_PENDING",
        339,
        500,
        "a" * 40,
    )
    mission = FakeMission(target(checkpoint=123, head="a" * 40))
    controller = FakeController(result)
    store = FakeStore(previous=previous)

    actual = coordinator(mission, controller, store).run_once()

    assert actual == result
    assert ("old-run", "YIELD_EXTERNAL") in store.finished
    assert "old-run" in store.released
    assert controller.calls == 1
    assert len(store.resolved) == 1


def test_required_store_read_failure_fails_closed_before_controller() -> None:
    mission = FakeMission(target())
    controller = FakeController(
        HostTransitionResult(
            HostTransitionStatus.COMPLETED,
            "SHOULD_NOT_RUN",
        )
    )
    store = FakeStore(fail_on_latest=True)

    actual = coordinator(mission, controller, store).run_once()

    assert actual.status is HostTransitionStatus.INTERVENTION_REQUIRED
    assert actual.detail == "OPERATIONAL_STORE_UNAVAILABLE"
    assert controller.calls == 0


def test_optional_store_read_failure_uses_existing_controller_path() -> None:
    expected = HostTransitionResult(HostTransitionStatus.YIELD_EXTERNAL, "CI_PENDING")
    mission = FakeMission(target())
    controller = FakeController(expected)
    store = FakeStore(fail_on_latest=True)

    actual = coordinator(mission, controller, store, required=False).run_once()

    assert actual == expected
    assert controller.calls == 1
