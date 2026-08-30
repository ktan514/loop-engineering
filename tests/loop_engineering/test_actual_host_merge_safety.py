from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from loop_engineering.actual_host_merge_safety import (
    ReviewAwareHostLoopController,
    SafeActualHostMissionPort,
)
from loop_engineering.ci_gate import CIGateStatus
from loop_engineering.host_entrypoint import PilotAwareMissionPort, PilotPlanningImplementer
from loop_engineering.host_runtime import (
    HostTarget,
    HostTransitionStatus,
    LocalCommandResult,
)

from .conftest import config


class NoopRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> LocalCommandResult:
        del command, cwd, environment, timeout_seconds, capture_output
        return LocalCommandResult(0, "")


class FakePilotMission(PilotAwareMissionPort):
    def __init__(self, target: HostTarget) -> None:
        self.target = target
        self.merge_calls = 0
        self.checkpoints: list[str] = []

    def current_target(self) -> HostTarget | None:
        return self.target

    def ci_status(self, target: HostTarget) -> CIGateStatus:
        del target
        return CIGateStatus.PASS

    def merge_current(self, target: HostTarget) -> bool:
        del target
        self.merge_calls += 1
        return True

    def merge_requires_reconciliation(self, target: HostTarget) -> bool:
        del target
        return False

    def complete_work(self, target: HostTarget) -> bool:
        del target
        return True

    def publish_checkpoint(self, body: str) -> bool:
        self.checkpoints.append(body)
        return True


class NoopPilotImplementer(PilotPlanningImplementer):
    def __init__(self) -> None:
        self.plan_calls: list[int | None] = []

    def continue_work(self, target: HostTarget, *, repair: bool) -> bool:
        del target, repair
        return False

    def plan_next_work(self, completed_work: int | None) -> bool:
        self.plan_calls.append(completed_work)
        return True


class FakeReviewPort(SafeActualHostMissionPort):
    def __init__(self, *, ready_result: bool = True, reviewed: bool = False) -> None:
        self.ready_result = ready_result
        self.reviewed = reviewed
        self.ready_calls = 0
        self.review_calls = 0

    def make_ready_for_review(self, target: HostTarget) -> bool:
        del target
        self.ready_calls += 1
        return self.ready_result

    def has_current_head_review(self, target: HostTarget) -> bool:
        del target
        self.review_calls += 1
        return self.reviewed


class RecordingSafePort(SafeActualHostMissionPort):
    def __init__(self, target: HostTarget) -> None:
        super().__init__(config(), NoopRunner(), {"PATH": "/usr/bin"})
        self.target = target
        self.commands: list[tuple[str, ...]] = []
        self.pull_reads = 0

    def current_target(self) -> HostTarget | None:
        return self.target

    def _pull_requires_reconciliation(self, target: HostTarget) -> bool:
        del target
        return False

    def _api_json(self, endpoint: str) -> dict[str, object]:
        del endpoint
        self.pull_reads += 1
        if self.pull_reads == 1:
            return {
                "state": "open",
                "head": {"sha": self.target.head_sha},
                "base": {"ref": "rebuild/v2-foundation"},
            }
        return {"merged": True}

    def _run_gh(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: int = 60,
    ) -> LocalCommandResult:
        del timeout_seconds
        self.commands.append(tuple(arguments))
        return LocalCommandResult(0, "")


def test_draft_ci_success_only_moves_to_review_pending() -> None:
    target = HostTarget(384, True, 441, "a" * 40, True, False, 1, "a" * 40)
    mission = FakePilotMission(target)
    implementer = NoopPilotImplementer()
    review = FakeReviewPort(ready_result=True)

    result = ReviewAwareHostLoopController(
        config(), mission, implementer, review
    ).run_once()

    assert result.status is HostTransitionStatus.YIELD_EXTERNAL
    assert result.detail == "REVIEW_PENDING"
    assert review.ready_calls == 1
    assert review.review_calls == 0
    assert mission.merge_calls == 0


def test_ready_pr_without_current_head_review_stays_pending() -> None:
    target = HostTarget(384, True, 441, "b" * 40, False, False, 1, "b" * 40)
    mission = FakePilotMission(target)
    implementer = NoopPilotImplementer()
    review = FakeReviewPort(reviewed=False)

    result = ReviewAwareHostLoopController(
        config(), mission, implementer, review
    ).run_once()

    assert result.status is HostTransitionStatus.YIELD_EXTERNAL
    assert result.detail == "REVIEW_PENDING"
    assert review.ready_calls == 0
    assert review.review_calls == 1
    assert mission.merge_calls == 0


def test_draft_merge_is_refused_without_ready_side_effect() -> None:
    target = HostTarget(384, True, 441, "c" * 40, True, False, 1, "c" * 40)
    port = RecordingSafePort(target)

    assert not port.merge_current(target)
    assert port.commands == []


def test_ready_merge_uses_japanese_subject_and_body() -> None:
    target = HostTarget(384, True, 441, "d" * 40, False, False, 1, "d" * 40)
    port = RecordingSafePort(target)

    assert port.merge_current(target)
    assert len(port.commands) == 1
    command = port.commands[0]
    assert "--match-head-commit" in command
    assert "--subject" in command
    assert "PR #441 を rebuild/v2-foundation へ統合する" in command
    assert "--body" in command
    assert "Work #384 の変更を通常マージで統合する。" in command
