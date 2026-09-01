from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field

from loop_engineering.v2_effect_executor import (
    GitHubV2EffectExecutor,
    V2EffectExecutionStatus,
)
from loop_engineering.v2_resume import EffectReadbackStatus
from loop_engineering.work_state import EffectAttempt

from .conftest import config


@dataclass
class Runner:
    fail: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, args: Sequence[str]) -> str:
        command = tuple(args)
        self.calls.append(command)
        if self.fail:
            raise subprocess.CalledProcessError(1, command)
        return ""


@dataclass
class Readback:
    status: EffectReadbackStatus
    calls: list[EffectAttempt] = field(default_factory=list)

    def readback(self, attempt: EffectAttempt) -> EffectReadbackStatus:
        self.calls.append(attempt)
        return self.status


def attempt(
    kind: str,
    target: str,
    before: tuple[tuple[str, str], ...],
    after: tuple[tuple[str, str], ...],
) -> EffectAttempt:
    return EffectAttempt(
        idempotency_key="effect:key",
        work_identity="work:1",
        kind=kind,
        target_identity=target,
        status="INTENT_RECORDED",
        packet_generation=1,
        expected_preconditions=before,
        expected_effect=after,
    )


def executor(runner: Runner, readback: Readback) -> GitHubV2EffectExecutor:
    return GitHubV2EffectExecutor(
        runner=runner,
        readback=readback,
        repository="ktan514/ai-liver-yura",
        config=config(),
    )


def test_ready_executes_only_after_before_state_is_proven() -> None:
    value = attempt(
        "READY",
        "pr:70",
        (("draft", "true"), ("head", "abc")),
        (("draft", "false"),),
    )
    runner = Runner()
    readback = Readback(EffectReadbackStatus.NO_EFFECT)

    result = executor(runner, readback).execute(value)

    assert result.status is V2EffectExecutionStatus.EXECUTED
    assert runner.calls == [
        ("gh", "pr", "ready", "70", "--repo", "ktan514/ai-liver-yura")
    ]
    assert readback.calls == [value]


def test_already_confirmed_effect_is_never_sent_again() -> None:
    value = attempt(
        "READY",
        "pr:70",
        (("draft", "true"), ("head", "abc")),
        (("draft", "false"),),
    )
    runner = Runner()

    result = executor(runner, Readback(EffectReadbackStatus.CONFIRMED)).execute(value)

    assert result.status is V2EffectExecutionStatus.ALREADY_CONFIRMED
    assert runner.calls == []


def test_unproven_precondition_blocks_without_command() -> None:
    value = attempt(
        "MERGE",
        "pr:70",
        (("base", "main"), ("head", "abc"), ("state", "OPEN")),
        (("state", "MERGED"),),
    )
    runner = Runner()

    result = executor(runner, Readback(EffectReadbackStatus.UNKNOWN)).execute(value)

    assert result.status is V2EffectExecutionStatus.BLOCKED
    assert result.detail == "EFFECT_PRECONDITION_UNPROVEN"
    assert runner.calls == []


def test_push_to_trunk_is_rejected_by_write_gate() -> None:
    value = attempt(
        "PUSH",
        "branch:rebuild/v2-foundation",
        (("head", "before"),),
        (("head", "after"),),
    )
    runner = Runner()

    result = executor(runner, Readback(EffectReadbackStatus.NO_EFFECT)).execute(value)

    assert result.status is V2EffectExecutionStatus.BLOCKED
    assert result.detail == "DIRECT_TRUNK_WRITE_FORBIDDEN"
    assert runner.calls == []


def test_merge_binds_expected_head_and_uses_merge_commit() -> None:
    value = attempt(
        "MERGE",
        "pr:72",
        (("base", "main"), ("head", "exact-head"), ("state", "OPEN")),
        (("state", "MERGED"),),
    )
    runner = Runner()

    result = executor(runner, Readback(EffectReadbackStatus.NO_EFFECT)).execute(value)

    assert result.status is V2EffectExecutionStatus.EXECUTED
    assert runner.calls == [
        (
            "gh",
            "pr",
            "merge",
            "72",
            "--repo",
            "ktan514/ai-liver-yura",
            "--merge",
            "--match-head-commit",
            "exact-head",
        )
    ]


def test_issue_update_is_one_patch_request() -> None:
    value = attempt(
        "ISSUE_UPDATE",
        "issue:67",
        (("state", "OPEN"), ("title", "旧タイトル")),
        (("state", "CLOSED"), ("title", "新タイトル")),
    )
    runner = Runner()

    result = executor(runner, Readback(EffectReadbackStatus.NO_EFFECT)).execute(value)

    assert result.status is V2EffectExecutionStatus.EXECUTED
    assert len(runner.calls) == 1
    command = runner.calls[0]
    assert command[:5] == (
        "gh",
        "api",
        "repos/ktan514/ai-liver-yura/issues/67",
        "--method",
        "PATCH",
    )
    assert "title=新タイトル" in command
    assert "state=closed" in command


def test_command_failure_returns_uncertain_path_without_retry() -> None:
    value = attempt(
        "READY",
        "pr:70",
        (("draft", "true"), ("head", "abc")),
        (("draft", "false"),),
    )
    runner = Runner(fail=True)

    result = executor(runner, Readback(EffectReadbackStatus.NO_EFFECT)).execute(value)

    assert result.status is V2EffectExecutionStatus.COMMAND_FAILED
    assert len(runner.calls) == 1
