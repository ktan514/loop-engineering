"""V2作業パケットの記録済み外部effectを1回だけ実行する。"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .config import LoopEngineConfig
from .models import WriteIntent
from .v2_resume import EffectReadbackPort, EffectReadbackStatus
from .work_state import EffectAttempt
from .write_gate import validate_preconditions


class V2EffectCommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str: ...


class V2EffectExecutionStatus(str, Enum):
    EXECUTED = "EXECUTED"
    ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
    BLOCKED = "BLOCKED"
    COMMAND_FAILED = "COMMAND_FAILED"


@dataclass(frozen=True, slots=True)
class V2EffectExecutionResult:
    status: V2EffectExecutionStatus
    detail: str


@dataclass(slots=True)
class SubprocessV2CommandRunner:
    """shellを介さず、指定WorkspaceでGit / GitHub CLIを実行する。"""

    root: Path
    environment: Mapping[str, str]

    def run(self, args: Sequence[str]) -> str:
        completed = subprocess.run(
            tuple(args),
            cwd=self.root,
            env=dict(self.environment),
            check=True,
            text=True,
            capture_output=True,
        )
        return completed.stdout


@dataclass(slots=True)
class GitHubV2EffectExecutor:
    """対象限定readbackとWrite Gateを通過したeffectだけを1回送信する。"""

    runner: V2EffectCommandRunner
    readback: EffectReadbackPort
    repository: str
    config: LoopEngineConfig

    def execute(self, attempt: EffectAttempt) -> V2EffectExecutionResult:
        if attempt.status != "INTENT_RECORDED":
            return V2EffectExecutionResult(
                V2EffectExecutionStatus.BLOCKED,
                "EFFECT_STATUS_NOT_EXECUTABLE",
            )
        before = _pairs(attempt.expected_preconditions)
        after = _pairs(attempt.expected_effect)
        if before is None or after is None or not before or not after:
            return V2EffectExecutionResult(
                V2EffectExecutionStatus.BLOCKED,
                "EFFECT_EXPECTATION_INVALID",
            )

        observed = self.readback.readback(attempt)
        if observed is EffectReadbackStatus.CONFIRMED:
            return V2EffectExecutionResult(
                V2EffectExecutionStatus.ALREADY_CONFIRMED,
                "EFFECT_ALREADY_CONFIRMED",
            )
        if observed is not EffectReadbackStatus.NO_EFFECT:
            return V2EffectExecutionResult(
                V2EffectExecutionStatus.BLOCKED,
                "EFFECT_PRECONDITION_UNPROVEN",
            )

        intent = _write_intent(attempt, before, after)
        if intent is None:
            return V2EffectExecutionResult(
                V2EffectExecutionStatus.BLOCKED,
                "EFFECT_TARGET_INVALID",
            )
        gate = validate_preconditions(intent, before, config=self.config)
        if not gate.allowed:
            detail = gate.conflict.value if gate.conflict is not None else "WRITE_GATE_BLOCKED"
            return V2EffectExecutionResult(V2EffectExecutionStatus.BLOCKED, detail)

        command = _command(attempt, self.repository, before, after)
        if command is None:
            return V2EffectExecutionResult(
                V2EffectExecutionStatus.BLOCKED,
                "EFFECT_COMMAND_INVALID",
            )
        try:
            self.runner.run(command)
        except (OSError, subprocess.SubprocessError, ValueError):
            return V2EffectExecutionResult(
                V2EffectExecutionStatus.COMMAND_FAILED,
                "EFFECT_COMMAND_FAILED",
            )
        return V2EffectExecutionResult(V2EffectExecutionStatus.EXECUTED, "EFFECT_SENT")


def production_environment(values: Mapping[str, str]) -> dict[str, str]:
    """実行に必要な環境を維持し、callerから渡された秘密値を表示せず継承する。"""
    environment = dict(values)
    environment.setdefault("PATH", os.defpath)
    return environment


def _write_intent(
    attempt: EffectAttempt,
    before: Mapping[str, str],
    after: Mapping[str, str],
) -> WriteIntent | None:
    if attempt.kind == "PUSH":
        target = _target_suffix(attempt.target_identity, "branch")
        target_kind = "branch"
        mutation = "push"
    elif attempt.kind in {"READY", "MERGE"}:
        number = _target_number(attempt.target_identity, "pr")
        target = str(number) if number is not None else None
        target_kind = "pull_request"
        mutation = attempt.kind.lower()
    elif attempt.kind == "ISSUE_UPDATE":
        number = _target_number(attempt.target_identity, "issue")
        target = str(number) if number is not None else None
        target_kind = "issue"
        mutation = "issue_update"
    else:
        return None
    if target is None:
        return None
    return WriteIntent(
        intent_id=attempt.idempotency_key,
        target_kind=target_kind,
        target_identity=target,
        mutation_kind=mutation,
        expected_preconditions=tuple(sorted(before.items())),
        expected_effect=tuple(sorted(after.items())),
        source_observation_id=attempt.idempotency_key,
    )


def _command(
    attempt: EffectAttempt,
    repository: str,
    before: Mapping[str, str],
    after: Mapping[str, str],
) -> tuple[str, ...] | None:
    if attempt.kind == "PUSH":
        branch = _target_suffix(attempt.target_identity, "branch")
        if branch is None or set(before) != {"head"} or set(after) != {"head"}:
            return None
        return ("git", "push", "origin", f"{after['head']}:refs/heads/{branch}")
    if attempt.kind == "READY":
        number = _target_number(attempt.target_identity, "pr")
        if number is None:
            return None
        return ("gh", "pr", "ready", str(number), "--repo", repository)
    if attempt.kind == "MERGE":
        number = _target_number(attempt.target_identity, "pr")
        if number is None or "head" not in before:
            return None
        return (
            "gh",
            "pr",
            "merge",
            str(number),
            "--repo",
            repository,
            "--merge",
            "--match-head-commit",
            before["head"],
        )
    if attempt.kind == "ISSUE_UPDATE":
        number = _target_number(attempt.target_identity, "issue")
        if number is None or set(before) != set(after) or not after:
            return None
        fields: list[str] = []
        for key in sorted(after):
            value = after[key]
            if key == "title":
                fields.extend(("--raw-field", f"title={value}"))
                continue
            if key == "state" and value in {"OPEN", "CLOSED"}:
                fields.extend(("--raw-field", f"state={value.lower()}"))
                continue
            return None
        return (
            "gh",
            "api",
            f"repos/{repository}/issues/{number}",
            "--method",
            "PATCH",
            *fields,
        )
    return None


def _pairs(values: tuple[tuple[str, str], ...]) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for key, value in values:
        if not key or key in result or "\x00" in key or "\x00" in value:
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
