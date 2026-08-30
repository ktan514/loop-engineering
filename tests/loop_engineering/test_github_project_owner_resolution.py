import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from loop_engineering.preflight import (
    CommandResult,
    EnvironmentCapabilityPreflight,
    PreflightResult,
)

from .conftest import config


class OwnerProbeRunner:
    def __init__(
        self,
        root: Path,
        *,
        owner: object,
        rate_limited: bool = False,
    ) -> None:
        self.root = root
        self.owner = owner
        self.rate_limited = rate_limited
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        del environment
        call = tuple(command)
        self.calls.append(call)
        if call[:3] == ("gh", "api", "graphql"):
            if self.rate_limited:
                return CommandResult(False, error="GraphQL: API rate limit exceeded")
            return CommandResult(
                True,
                json.dumps({"data": {"repositoryOwner": self.owner}}),
            )
        if call == ("gh", "api", "repos/ktan514/ai-liver-yura"):
            return CommandResult(True, json.dumps({"permissions": {"push": True}}))
        if call[-2:] == ("rev-parse", "--show-toplevel"):
            return CommandResult(True, str(self.root))
        if call[-3:] == ("remote", "get-url", "origin"):
            return CommandResult(True, "git@github.com:ktan514/ai-liver-yura.git")
        if call[-2:] == ("rev-parse", "HEAD"):
            return CommandResult(True, "a" * 40)
        return CommandResult(True)


class ReviewerProbe:
    def check(self, socket_path: str, timeout_seconds: float) -> bool:
        del socket_path, timeout_seconds
        return True


def _environment(root: Path) -> dict[str, str]:
    goal = root / "docs" / "operations" / "loop_mission_goal.md"
    goal.parent.mkdir(parents=True, exist_ok=True)
    content = b"version: 2\ngeneration: 7\nmission\n"
    goal.write_bytes(content)
    return {
        "LOOP_TRUSTED_REVIEWER_SOCKET": "/tmp/reviewer.sock",
        "CODEX_MISSION_GOAL_GENERATION": "7",
        "CODEX_MISSION_GOAL_VERSION": "2",
        "CODEX_MISSION_GOAL_SHA256": hashlib.sha256(content).hexdigest(),
    }


def _run(tmp_path: Path, owner: object) -> tuple[OwnerProbeRunner, PreflightResult]:
    runner = OwnerProbeRunner(tmp_path, owner=owner)
    result = EnvironmentCapabilityPreflight(
        config(),
        runner,
        _environment(tmp_path),
        reviewer_probe=ReviewerProbe(),
        project_root=tmp_path,
    ).run()
    return runner, result


def test_project_probe_uses_repository_owner_interface(tmp_path: Path) -> None:
    runner, result = _run(
        tmp_path,
        {"projectV2": {"viewerCanUpdate": True}},
    )

    graphql_calls = [call for call in runner.calls if call[:3] == ("gh", "api", "graphql")]
    assert len(graphql_calls) == 1
    query_argument = next(item for item in graphql_calls[0] if item.startswith("query="))
    assert "repositoryOwner" in query_argument
    assert "... on ProjectV2Owner" in query_argument
    assert "user(login:" not in query_argument
    assert "organization(login:" not in query_argument
    assert result.capabilities["github_project_read"]
    assert result.capabilities["github_project_write"]


def test_project_owner_without_write_permission_is_read_only(tmp_path: Path) -> None:
    _, result = _run(
        tmp_path,
        {"projectV2": {"viewerCanUpdate": False}},
    )

    assert result.capabilities["github_project_read"]
    assert not result.capabilities["github_project_write"]


def test_missing_owner_or_project_is_fail_closed(tmp_path: Path) -> None:
    _, missing_owner = _run(tmp_path, None)
    _, missing_project = _run(tmp_path, {"projectV2": None})

    assert not missing_owner.capabilities["github_project_read"]
    assert not missing_owner.capabilities["github_project_write"]
    assert not missing_project.capabilities["github_project_read"]
    assert not missing_project.capabilities["github_project_write"]


def test_repository_owner_probe_preserves_rate_limit_typing(tmp_path: Path) -> None:
    runner = OwnerProbeRunner(tmp_path, owner=None, rate_limited=True)
    result = EnvironmentCapabilityPreflight(
        config(),
        runner,
        _environment(tmp_path),
        reviewer_probe=ReviewerProbe(),
        project_root=tmp_path,
    ).run()

    assert "GITHUB_PROJECT_RATE_LIMITED" in result.diagnostics
