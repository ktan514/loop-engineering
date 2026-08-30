import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pytest import MonkeyPatch

from loop_engineering import host_entrypoint
from loop_engineering.durable_host_entrypoint import (
    _project_rate_limit_is_only_blocker,
    _record_preflight_external_wait,
)
from loop_engineering.host_runtime import HostTransitionResult, HostTransitionStatus
from loop_engineering.preflight import (
    CommandResult,
    EnvironmentCapabilityPreflight,
    PreflightResult,
    PreflightStatus,
)
from loop_engineering.runtime_operational_state import OperationalStateUnavailable

from .conftest import config


class ProjectProbeRunner:
    def __init__(self, root: Path, *, rate_limited: bool = False) -> None:
        self.root = root
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
                return CommandResult(
                    False,
                    error="GraphQL: API rate limit exceeded for user ID 1.",
                )
            return CommandResult(
                True,
                json.dumps(
                    {
                        "data": {
                            "user": {
                                "projectV2": {
                                    "viewerCanUpdate": True,
                                }
                            }
                        }
                    }
                ),
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


class WaitStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[tuple[str, str]] = []

    def begin_run(self, run_identity: str, project_key: str, repository: str) -> None:
        del project_key, repository
        self.events.append(("begin", run_identity))
        if self.fail:
            raise OperationalStateUnavailable("test")

    def record_transition(
        self,
        run_identity: str,
        sequence_number: int,
        result: HostTransitionResult,
    ) -> None:
        assert sequence_number == 1
        self.events.append(("transition", f"{run_identity}:{result.detail}"))

    def record_external_wait(
        self, run_identity: str, result: HostTransitionResult
    ) -> None:
        self.events.append(("wait", f"{run_identity}:{result.detail}"))

    def finish_run(self, run_identity: str, status: str) -> None:
        self.events.append(("finish", f"{run_identity}:{status}"))


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


def _rate_limited_preflight() -> PreflightResult:
    return PreflightResult(
        PreflightStatus.BLOCKED,
        {},
        ("GITHUB_PROJECT_READ", "GITHUB_PROJECT_WRITE"),
        (),
        (
            "GITHUB_PROJECT_READ",
            "GITHUB_PROJECT_WRITE",
            "GITHUB_PROJECT_RATE_LIMITED",
        ),
    )


def test_project_capability_uses_one_small_graphql_probe(tmp_path: Path) -> None:
    runner = ProjectProbeRunner(tmp_path)
    result = EnvironmentCapabilityPreflight(
        config(),
        runner,
        _environment(tmp_path),
        reviewer_probe=ReviewerProbe(),
        project_root=tmp_path,
    ).run()

    graphql_calls = [call for call in runner.calls if call[:3] == ("gh", "api", "graphql")]
    assert len(graphql_calls) == 1
    assert result.capabilities["github_project_read"]
    assert result.capabilities["github_project_write"]
    assert not any(
        len(call) >= 3
        and call[:2] == ("gh", "project")
        and call[2] in {"view", "field-list", "item-list"}
        for call in runner.calls
    )


def test_project_rate_limit_is_typed_without_exposing_raw_error(tmp_path: Path) -> None:
    runner = ProjectProbeRunner(tmp_path, rate_limited=True)
    result = EnvironmentCapabilityPreflight(
        config(),
        runner,
        _environment(tmp_path),
        reviewer_probe=ReviewerProbe(),
        project_root=tmp_path,
    ).run()

    assert not result.capabilities["github_project_read"]
    assert not result.capabilities["github_project_write"]
    assert "GITHUB_PROJECT_RATE_LIMITED" in result.diagnostics
    assert "user ID 1" not in result.as_json()


def test_only_project_rate_limit_blockers_are_external_wait_eligible() -> None:
    rate_limited = _rate_limited_preflight()
    mixed = PreflightResult(
        PreflightStatus.BLOCKED,
        {},
        ("GITHUB_PROJECT_READ", "GITHUB_REPO_READ"),
        (),
        (
            "GITHUB_PROJECT_READ",
            "GITHUB_REPO_READ",
            "GITHUB_PROJECT_RATE_LIMITED",
        ),
    )

    assert _project_rate_limit_is_only_blocker(rate_limited)
    assert not _project_rate_limit_is_only_blocker(mixed)


def test_non_durable_host_maps_project_rate_limit_to_external_wait(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    class RateLimitedPreflight:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def run(self) -> PreflightResult:
            return _rate_limited_preflight()

    monkeypatch.setattr(host_entrypoint, "EnvironmentCapabilityPreflight", RateLimitedPreflight)
    monkeypatch.setattr(
        host_entrypoint,
        "inject_mission_goal_environment",
        lambda **kwargs: dict(kwargs["environment"]),
    )

    result = host_entrypoint.run_actual_host_transition(
        root=tmp_path,
        environment={},
        config=config(),
    )

    assert result.status is HostTransitionStatus.YIELD_EXTERNAL
    assert result.detail == "GITHUB_PROJECT_RATE_LIMIT"


def test_preflight_external_wait_is_persisted_as_terminal_yield() -> None:
    store = WaitStore()
    result = HostTransitionResult(
        HostTransitionStatus.YIELD_EXTERNAL,
        "GITHUB_PROJECT_RATE_LIMIT",
    )

    assert _record_preflight_external_wait(
        store,
        project_key="ai-liver-yura",
        repository="ktan514/ai-liver-yura",
        result=result,
        required=True,
    )
    assert [kind for kind, _ in store.events] == ["begin", "transition", "wait", "finish"]
    assert store.events[-1][1].endswith(":YIELD_EXTERNAL")


def test_required_store_failure_blocks_preflight_wait_recording() -> None:
    result = HostTransitionResult(
        HostTransitionStatus.YIELD_EXTERNAL,
        "GITHUB_PROJECT_RATE_LIMIT",
    )

    assert not _record_preflight_external_wait(
        WaitStore(fail=True),
        project_key="ai-liver-yura",
        repository="ktan514/ai-liver-yura",
        result=result,
        required=True,
    )
