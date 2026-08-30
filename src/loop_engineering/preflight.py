from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .config import LoopEngineConfig, LoopEngineeringSettings
from .mission_goal import inject_mission_goal_environment, read_mission_goal_identity
from .operational_config import inject_operational_store_environment
from .postgres_runtime import PostgreSQLCommandAdapter


class PreflightStatus(str, Enum):
    PASS = "PASS"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class CommandResult:
    succeeded: bool
    output: str = ""
    timed_out: bool = False
    error: str = ""


class CommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    """確認出力はローカル解析だけに保持し、人間向け出力へそのまま流さない。"""

    _TIMEOUT_SECONDS = 10

    def run(
        self,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=dict(environment) if environment is not None else None,
                timeout=self._TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(False, timed_out=True)
        except OSError:
            return CommandResult(False)
        return CommandResult(
            result.returncode == 0,
            result.stdout,
            error=result.stderr,
        )


class TrustedReviewerBrokerProbe(Protocol):
    """信頼済みホスト仲介器の秘密情報を含まない健全性接続口を確認する。"""

    def check(self, socket_path: str, timeout_seconds: float) -> bool: ...


class UnixSocketTrustedReviewerBrokerProbe:
    """ホスト側仲介器へ認証情報を含まない上限付き要求を送信する。"""

    _MAX_RESPONSE_BYTES = 4096

    def check(self, socket_path: str, timeout_seconds: float) -> bool:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout_seconds)
                connection.connect(socket_path)
                connection.sendall(b'{"action":"health"}\n')
                response = connection.recv(self._MAX_RESPONSE_BYTES)
        except (OSError, TimeoutError):
            return False
        try:
            payload = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload == {"availability": "PASS"}


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: PreflightStatus
    capabilities: Mapping[str, bool]
    blocking_for_loop_bootstrap: tuple[str, ...]
    work_scoped_unavailable: tuple[str, ...]
    diagnostics: tuple[str, ...]

    def as_json(self) -> str:
        payload = asdict(self)
        payload["status"] = self.status.value
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class EnvironmentCapabilityPreflight:
    def __init__(
        self,
        config: LoopEngineConfig,
        runner: CommandRunner,
        environment: Mapping[str, str] | None = None,
        *,
        reviewer_probe: TrustedReviewerBrokerProbe | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._config = config
        self._runner = runner
        self._environment = environment if environment is not None else os.environ
        self._reviewer_probe = reviewer_probe or UnixSocketTrustedReviewerBrokerProbe()
        self._project_root = (project_root or Path.cwd()).resolve(strict=False)
        self._timeouts: list[str] = []
        self._github_project_rate_limited = False

    def run(self) -> PreflightResult:
        capability = self._command_capabilities()
        capability.update(self._workspace_capabilities())
        capability["github_repo_write"] = self._repository_write_allowed()
        project_read, project_write = self._project_access()
        capability["github_project_read"] = project_read
        capability["github_project_write"] = project_write
        capability["mission_goal"] = self._mission_goal_matches()
        capability["project_venv"] = sys.prefix != sys.base_prefix
        capability["trusted_reviewer"] = self._reviewer_available()
        capability.update(self._postgresql_capabilities())

        blocking_names: tuple[str, ...] = (
            "workspace_path",
            "workspace_git_root",
            "workspace_repository_match",
            "workspace_head_readable",
            "workspace_state_readable",
            "github_cli",
            "github_repo_read",
            "github_repo_write",
            "github_project_read",
            "github_project_write",
            "mission_goal",
            "project_venv",
            "python",
            "pytest",
            "ruff",
            "mypy",
            "compileall",
            "codex_cli",
        )
        scoped_names: tuple[str, ...] = ("trusted_reviewer",)
        postgres_names = (
            "postgresql_client",
            "postgresql_server",
            "postgresql_database",
            "postgresql_migration",
        )
        postgres_required = (
            self._environment.get("LOOP_OPERATIONAL_STORE_REQUIRED", "false").strip().lower()
            == "true"
        )
        postgres_driver = self._environment.get("LOOP_POSTGRES_DRIVER", "host").strip()
        if postgres_required:
            blocking_names += postgres_names
            if postgres_driver == "docker":
                blocking_names += ("docker",)
        else:
            scoped_names += postgres_names
            if postgres_driver == "docker":
                scoped_names += ("docker",)

        blocking = tuple(name.upper() for name in blocking_names if not capability[name])
        scoped = tuple(name.upper() for name in scoped_names if not capability[name])
        status = (
            PreflightStatus.BLOCKED
            if blocking
            else PreflightStatus.DEGRADED
            if scoped
            else PreflightStatus.PASS
        )
        rate_limit_diagnostic = (
            ("GITHUB_PROJECT_RATE_LIMITED",)
            if self._github_project_rate_limited
            else ()
        )
        return PreflightResult(
            status,
            capability,
            blocking,
            scoped,
            blocking + scoped + tuple(self._timeouts) + rate_limit_diagnostic,
        )

    def _command_capabilities(self) -> dict[str, bool]:
        probes = {
            "github_cli": ("gh", "auth", "status"),
            "github_repo_read": ("gh", "repo", "view", self._config.repository),
            "python": (sys.executable, "--version"),
            "pytest": (sys.executable, "-m", "pytest", "--version"),
            "ruff": (sys.executable, "-m", "ruff", "--version"),
            "mypy": (sys.executable, "-m", "mypy", "--version"),
            "compileall": (sys.executable, "-m", "compileall", "--help"),
            "codex_cli": ("codex", "--version"),
            "docker": ("docker", "version"),
        }
        return {
            name: self._run(name, command).succeeded for name, command in probes.items()
        }

    def _workspace_capabilities(self) -> dict[str, bool]:
        root = self._project_root
        if not root.is_dir():
            return {
                "workspace_path": False,
                "workspace_git_root": False,
                "workspace_repository_match": False,
                "workspace_head_readable": False,
                "workspace_state_readable": False,
            }
        top = self._run(
            "workspace_git_root",
            ("git", "-C", str(root), "rev-parse", "--show-toplevel"),
        )
        remote = self._run(
            "workspace_repository",
            ("git", "-C", str(root), "remote", "get-url", "origin"),
        )
        head = self._run(
            "workspace_head",
            ("git", "-C", str(root), "rev-parse", "HEAD"),
        )
        state = self._run(
            "workspace_state",
            ("git", "-C", str(root), "status", "--porcelain"),
        )
        top_path = Path(top.output.strip()).resolve(strict=False) if top.succeeded else None
        repository = _repository_from_remote(remote.output.strip()) if remote.succeeded else None
        return {
            "workspace_path": True,
            "workspace_git_root": top_path == root,
            "workspace_repository_match": repository == self._config.repository,
            "workspace_head_readable": head.succeeded and len(head.output.strip()) == 40,
            "workspace_state_readable": state.succeeded,
        }

    def _repository_write_allowed(self) -> bool:
        result = self._run(
            "github_repo_write",
            ("gh", "api", f"repos/{self._config.repository}"),
        )
        try:
            return result.succeeded and bool(json.loads(result.output)["permissions"]["push"])
        except (KeyError, TypeError, json.JSONDecodeError):
            return False

    def _project_access(self) -> tuple[bool, bool]:
        owner = self._config.owner
        number = self._config.project_number
        query = (
            "query { "
            f'user(login: "{owner}") {{ projectV2(number: {number}) {{ viewerCanUpdate }} }} '
            f'organization(login: "{owner}") '
            f"{{ projectV2(number: {number}) {{ viewerCanUpdate }} }} "
            "}"
        )
        result = self._run(
            "github_project_access",
            ("gh", "api", "graphql", "-f", f"query={query}"),
        )
        if not result.succeeded:
            if _is_github_rate_limit(result):
                self._github_project_rate_limited = True
            return False, False
        try:
            data = json.loads(result.output)["data"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return False, False
        if not isinstance(data, dict):
            return False, False
        for owner_kind in ("user", "organization"):
            owner_value = data.get(owner_kind)
            if not isinstance(owner_value, dict):
                continue
            project = owner_value.get("projectV2")
            if isinstance(project, dict):
                return True, project.get("viewerCanUpdate") is True
        return False, False

    def _reviewer_available(self) -> bool:
        socket_path = self._environment.get("LOOP_TRUSTED_REVIEWER_SOCKET")
        return bool(socket_path and self._reviewer_probe.check(socket_path, 10.0))

    def _postgresql_capabilities(self) -> dict[str, bool]:
        capabilities = PostgreSQLCommandAdapter(self._runner, self._environment).probe()
        migration = capabilities.migration
        if (
            self._environment.get("LOOP_POSTGRES_MIGRATION_POLICY", "required").strip()
            == "ignore"
        ):
            migration = capabilities.database
        return {
            "postgresql_client": capabilities.client,
            "postgresql_server": capabilities.server,
            "postgresql_database": capabilities.database,
            "postgresql_migration": migration,
        }

    def _mission_goal_matches(self) -> bool:
        raw_path = self._environment.get("LOOP_MISSION_GOAL_PATH", "").strip()
        source = (
            Path(raw_path).expanduser().resolve(strict=False)
            if raw_path
            else self._project_root / "docs" / "operations" / "loop_mission_goal.md"
        )
        identity = read_mission_goal_identity(source)
        if identity is None:
            return False
        return (
            self._environment.get("CODEX_MISSION_GOAL_GENERATION") == identity.generation
            and self._environment.get("CODEX_MISSION_GOAL_VERSION") == identity.version
            and self._environment.get("CODEX_MISSION_GOAL_SHA256") == identity.sha256
        )

    def _run(
        self,
        name: str,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        result = self._runner.run(command, environment)
        if result.timed_out:
            self._timeouts.append(f"{name.upper()}_TIMEOUT")
        return result


def _is_github_rate_limit(result: CommandResult) -> bool:
    diagnostic = f"{result.output}\n{result.error}".lower()
    return (
        "api rate limit exceeded" in diagnostic
        or "secondary rate limit" in diagnostic
    )


def _repository_from_remote(value: str) -> str | None:
    if value.startswith("git@github.com:"):
        repository = value.removeprefix("git@github.com:")
    elif value.startswith("ssh://git@github.com/"):
        repository = value.removeprefix("ssh://git@github.com/")
    else:
        parsed = urlsplit(value)
        if parsed.hostname != "github.com":
            return None
        repository = parsed.path.lstrip("/")
    if repository.endswith(".git"):
        repository = repository[:-4]
    parts = repository.split("/")
    return repository if len(parts) == 2 and all(parts) else None


def main() -> None:
    platform_root = Path(__file__).resolve().parents[2]
    settings = LoopEngineeringSettings.load(platform_root)
    environment = inject_operational_store_environment(
        settings.config_path,
        settings.canonical_environment(),
    )
    environment = inject_mission_goal_environment(
        platform_root=platform_root,
        product_root=settings.workspace_path,
        repository=settings.engine.repository,
        environment=environment,
    )
    print(
        EnvironmentCapabilityPreflight(
            settings.engine,
            SubprocessCommandRunner(),
            environment,
            project_root=settings.workspace_path,
        ).run().as_json()
    )


if __name__ == "__main__":
    main()
