import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from loop_engineering.v2_development_lineage import (
    GitHubDevelopmentLineageEffects,
    LineageIdentity,
    LineageStatus,
    MaterializedProposal,
    TrustedProposalMaterializer,
)
from loop_engineering.v2_implementer import ChangeProposal, ImplementerTransition
from loop_engineering.work_state import EffectAttempt, RecoveredWork, WorkRecord


@dataclass(frozen=True)
class Result:
    returncode: int = 0
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class MemoryState:
    def __init__(self, work_identity: str) -> None:
        self.work_identity = work_identity
        self.effects: dict[str, EffectAttempt] = {}
        self.outcomes: dict[str, str] = {}

    def recover(self, work_identity: str) -> RecoveredWork | None:
        if work_identity != self.work_identity:
            return None
        pending = tuple(
            replace(effect, status=self.outcomes.get(key, effect.status))
            for key, effect in self.effects.items()
            if self.outcomes.get(key, effect.status) in {"INTENT_RECORDED", "UNCERTAIN"}
        )
        record = WorkRecord(
            identity=work_identity,
            repository="owner/sample",
            issue_number=1,
            issue_revision="rev",
            lifecycle="RUNNING",
        )
        return RecoveredWork(record, None, None, pending)

    def record_effect_intent(self, attempt: EffectAttempt) -> bool:
        existing = self.effects.get(attempt.idempotency_key)
        if existing is None:
            self.effects[attempt.idempotency_key] = attempt
            return True
        return self.outcomes.get(attempt.idempotency_key, existing.status) == "INTENT_RECORDED"

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None:
        self.outcomes[idempotency_key] = status


class RemoteRunner:
    def __init__(self) -> None:
        self.branch_head: str | None = None
        self.pulls: list[dict[str, object]] = []
        self.commands: list[tuple[str, ...]] = []
        self.fail_push_after_effect = False
        self.fail_pr_after_effect = False

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> Result:
        del cwd, environment, timeout_seconds, capture_output
        values = tuple(command)
        self.commands.append(values)
        if values[:3] == ("git", "ls-remote", "--heads"):
            if self.branch_head is None:
                return Result()
            return Result(output=f"{self.branch_head}\trefs/heads/feature/work-1\n")
        if values[:2] == ("git", "push"):
            spec = values[-1]
            self.branch_head = spec.split(":", 1)[0]
            return Result(returncode=1 if self.fail_push_after_effect else 0)
        if values[:3] == ("gh", "pr", "list"):
            return Result(output=json.dumps(self.pulls))
        if values[:3] == ("gh", "pr", "create"):
            assert self.branch_head is not None
            self.pulls.append(
                {
                    "number": 7,
                    "url": "https://github.com/owner/sample/pull/7",
                    "headRefOid": self.branch_head,
                    "headRefName": "feature/work-1",
                    "baseRefName": "main",
                    "isDraft": True,
                }
            )
            return Result(returncode=1 if self.fail_pr_after_effect else 0)
        raise AssertionError(values)


class NoCommandRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> Result:
        del command, cwd, environment, timeout_seconds, capture_output
        raise AssertionError("command must not run")


def lineage() -> LineageIdentity:
    return LineageIdentity(
        repository="owner/sample",
        work_identity="work:owner/sample:1",
        issue_number=1,
        branch_name="feature/work-1",
        base_branch="main",
        packet_generation=1,
    )


def materialized(base: str = "a" * 40, candidate: str = "b" * 40) -> MaterializedProposal:
    return MaterializedProposal(
        work_identity="work:owner/sample:1",
        exact_base_sha=base,
        candidate_sha=candidate,
        changed_paths=("src/app.py",),
        patch_sha256="patch",
    )


def service(runner: RemoteRunner, state: MemoryState) -> GitHubDevelopmentLineageEffects:
    return GitHubDevelopmentLineageEffects(
        runner,
        state,
        Path("/tmp/product"),
        {"PATH": "/usr/bin"},
    )


def test_new_branch_and_pr_are_created_once() -> None:
    runner = RemoteRunner()
    state = MemoryState(lineage().work_identity)

    first = service(runner, state).publish(lineage(), materialized())
    second = service(runner, state).publish(lineage(), materialized())

    assert first.status is LineageStatus.CONFIRMED
    assert second.status is LineageStatus.CONFIRMED
    assert runner.branch_head == "b" * 40
    assert len(runner.pulls) == 1
    assert sum(command[:2] == ("git", "push") for command in runner.commands) == 1
    assert sum(command[:3] == ("gh", "pr", "create") for command in runner.commands) == 1


def test_existing_exact_base_branch_is_normal_push() -> None:
    runner = RemoteRunner()
    runner.branch_head = "a" * 40
    state = MemoryState(lineage().work_identity)

    result = service(runner, state).publish(lineage(), materialized())

    assert result.status is LineageStatus.CONFIRMED
    kinds = {effect.kind for effect in state.effects.values()}
    assert "PUSH" in kinds
    assert "BRANCH_CREATE" not in kinds


def test_stale_remote_branch_is_blocked_without_push() -> None:
    runner = RemoteRunner()
    runner.branch_head = "c" * 40
    state = MemoryState(lineage().work_identity)

    result = service(runner, state).publish(lineage(), materialized())

    assert result.status is LineageStatus.BLOCKED
    assert result.detail == "STALE_REMOTE_BRANCH"
    assert not any(command[:2] == ("git", "push") for command in runner.commands)


def test_push_failure_after_remote_effect_is_confirmed_by_readback() -> None:
    runner = RemoteRunner()
    runner.fail_push_after_effect = True
    state = MemoryState(lineage().work_identity)

    result = service(runner, state).publish(lineage(), materialized())

    assert result.status is LineageStatus.CONFIRMED
    assert state.outcomes
    assert "CONFIRMED" in state.outcomes.values()


def test_pr_create_failure_after_effect_is_confirmed_by_readback() -> None:
    runner = RemoteRunner()
    runner.fail_pr_after_effect = True
    state = MemoryState(lineage().work_identity)

    result = service(runner, state).publish(lineage(), materialized())

    assert result.status is LineageStatus.CONFIRMED
    assert result.pull_request is not None
    assert result.pull_request.number == 7


def test_competing_open_prs_are_blocked() -> None:
    runner = RemoteRunner()
    runner.branch_head = "b" * 40
    pull = {
        "number": 7,
        "url": "https://github.com/owner/sample/pull/7",
        "headRefOid": "b" * 40,
        "headRefName": "feature/work-1",
        "baseRefName": "main",
        "isDraft": True,
    }
    runner.pulls = [pull, {**pull, "number": 8, "url": "https://github.com/x/8"}]
    state = MemoryState(lineage().work_identity)

    result = service(runner, state).publish(lineage(), materialized())

    assert result.status is LineageStatus.BLOCKED
    assert result.detail == "COMPETING_LINEAGE"


def test_uncertain_branch_effect_is_not_resent() -> None:
    runner = RemoteRunner()
    state = MemoryState(lineage().work_identity)
    first = service(runner, state).publish(lineage(), materialized())
    assert first.status is LineageStatus.CONFIRMED
    key = next(key for key, effect in state.effects.items() if effect.kind == "BRANCH_CREATE")
    state.outcomes[key] = "UNCERTAIN"
    runner.branch_head = None
    before = len(runner.commands)

    result = service(runner, state).publish(lineage(), materialized())

    assert result.status is LineageStatus.UNCERTAIN
    assert not any(command[:2] == ("git", "push") for command in runner.commands[before:])


def test_materializer_rejects_raw_patch_hash_mismatch_before_git(tmp_path: Path) -> None:
    workspace = tmp_path / "product"
    workspace.mkdir()
    patch = "diff --git a/src/app.py b/src/app.py\n+ok\n"
    proposal = ChangeProposal(
        packet_identity="packet:1",
        work_identity="work:owner/sample:1",
        transition=ImplementerTransition.IMPLEMENT,
        exact_base_sha="a" * 40,
        changed_paths=("src/app.py",),
        patch_sha256=hashlib.sha256(b"different").hexdigest(),
        patch_text=patch,
        design_targets_changed=(),
    )

    result = TrustedProposalMaterializer(NoCommandRunner(), {}).materialize(
        workspace=workspace,
        repository="owner/sample",
        proposal=proposal,
        commit_message="feat: test",
    )

    assert result is None
