from collections.abc import Mapping, Sequence
from pathlib import Path

from loop_engineering.preflight import (
    CommandResult,
    EnvironmentCapabilityPreflight,
    PreflightStatus,
)

from .conftest import config


class NoopRunner:
    def run(
        self,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        del command, environment
        return CommandResult(True)


class PolicyPreflight(EnvironmentCapabilityPreflight):
    def __init__(self, environment: Mapping[str, str], *, postgres_ok: bool) -> None:
        super().__init__(
            config(),
            NoopRunner(),
            environment,
            project_root=Path("."),
        )
        self._postgres_ok = postgres_ok

    def _command_capabilities(self) -> dict[str, bool]:
        return {
            "github_cli": True,
            "github_repo_read": True,
            "python": True,
            "pytest": True,
            "ruff": True,
            "mypy": True,
            "compileall": True,
            "codex_cli": True,
            "docker": True,
        }

    def _workspace_capabilities(self) -> dict[str, bool]:
        return {
            "workspace_path": True,
            "workspace_git_root": True,
            "workspace_repository_match": True,
            "workspace_head_readable": True,
            "workspace_state_readable": True,
        }

    def _repository_write_allowed(self) -> bool:
        return True

    def _project_access(self) -> tuple[bool, bool]:
        return True, True

    def _mission_goal_matches(self) -> bool:
        return True

    def _reviewer_available(self) -> bool:
        return True

    def _postgresql_capabilities(self) -> dict[str, bool]:
        return {
            "postgresql_client": self._postgres_ok,
            "postgresql_server": self._postgres_ok,
            "postgresql_database": self._postgres_ok,
            "postgresql_migration": self._postgres_ok,
        }


def test_required_postgres_failure_blocks_bootstrap() -> None:
    result = PolicyPreflight(
        {
            "LOOP_OPERATIONAL_STORE_REQUIRED": "true",
            "LOOP_POSTGRES_DRIVER": "docker",
        },
        postgres_ok=False,
    ).run()

    assert result.status is PreflightStatus.BLOCKED
    assert "POSTGRESQL_CLIENT" in result.blocking_for_loop_bootstrap
    assert "POSTGRESQL_MIGRATION" in result.blocking_for_loop_bootstrap
    assert "POSTGRESQL_CLIENT" not in result.work_scoped_unavailable


def test_optional_postgres_failure_is_degraded_only() -> None:
    result = PolicyPreflight(
        {
            "LOOP_OPERATIONAL_STORE_REQUIRED": "false",
            "LOOP_POSTGRES_DRIVER": "docker",
        },
        postgres_ok=False,
    ).run()

    assert result.status is PreflightStatus.DEGRADED
    assert "POSTGRESQL_CLIENT" not in result.blocking_for_loop_bootstrap
    assert "POSTGRESQL_CLIENT" in result.work_scoped_unavailable


def test_required_postgres_passes_when_database_and_migration_are_current() -> None:
    result = PolicyPreflight(
        {
            "LOOP_OPERATIONAL_STORE_REQUIRED": "true",
            "LOOP_POSTGRES_DRIVER": "docker",
        },
        postgres_ok=True,
    ).run()

    assert result.status is PreflightStatus.PASS
    assert result.blocking_for_loop_bootstrap == ()
