from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from loop_engineering.postgres_runtime import PostgreSQLCommandAdapter


@dataclass(frozen=True)
class Result:
    succeeded: bool
    output: str = ""


class RecordingRunner:
    def __init__(self, migration_output: str = "") -> None:
        self.calls: list[tuple[tuple[str, ...], Mapping[str, str] | None]] = []
        self.migration_output = migration_output

    def run(
        self,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> Result:
        call = tuple(command)
        self.calls.append((call, environment))
        if "SELECT filename FROM loop_schema_migrations" in call:
            return Result(True, self.migration_output)
        return Result(True, "1\n")


def _environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "HOME": "/Users/test",
        "LOOP_POSTGRES_DSN": "postgresql://loop:secret-password@127.0.0.1:5432/loop_db",
        "LOOP_POSTGRES_DRIVER": "docker",
        "LOOP_POSTGRES_CONTAINER": "local-postgres",
    }


def test_docker_probe_uses_container_client_without_password_in_argv(tmp_path: Path) -> None:
    (tmp_path / "0001.sql").write_text("SELECT 1;", encoding="utf-8")
    runner = RecordingRunner("0001.sql\n")

    capabilities = PostgreSQLCommandAdapter(
        runner,
        _environment(),
        migration_dir=tmp_path,
    ).probe()

    assert capabilities.client
    assert capabilities.server
    assert capabilities.database
    assert capabilities.migration
    assert all(call[:2] == ("docker", "exec") for call, _ in runner.calls)
    assert all("secret-password" not in part for call, _ in runner.calls for part in call)
    database_environment = runner.calls[-1][1]
    assert database_environment is not None
    assert database_environment["PGPASSWORD"] == "secret-password"
    assert "OPENAI_API_KEY" not in database_environment


def test_host_driver_remains_supported_for_compatibility(tmp_path: Path) -> None:
    (tmp_path / "0001.sql").write_text("SELECT 1;", encoding="utf-8")
    runner = RecordingRunner("0001.sql\n")
    environment = _environment() | {"LOOP_POSTGRES_DRIVER": "host"}

    capabilities = PostgreSQLCommandAdapter(
        runner,
        environment,
        migration_dir=tmp_path,
    ).probe()

    assert capabilities.migration
    assert runner.calls[0][0] == ("psql", "--version")
    assert runner.calls[0][1] is not None
    assert runner.calls[0][1]["PGHOST"] == "127.0.0.1"


def test_legacy_database_url_is_accepted_as_compatibility_alias(tmp_path: Path) -> None:
    (tmp_path / "0001.sql").write_text("SELECT 1;", encoding="utf-8")
    runner = RecordingRunner("0001.sql\n")
    environment = _environment()
    environment["LOOP_DATABASE_URL"] = environment.pop("LOOP_POSTGRES_DSN")

    capabilities = PostgreSQLCommandAdapter(
        runner,
        environment,
        migration_dir=tmp_path,
    ).probe()

    assert capabilities.database


def test_apply_migrations_records_only_unapplied_files(tmp_path: Path) -> None:
    (tmp_path / "0001.sql").write_text("CREATE TABLE one (id INT);", encoding="utf-8")
    (tmp_path / "0002.sql").write_text("CREATE TABLE two (id INT);", encoding="utf-8")
    runner = RecordingRunner("0001.sql\n")

    result = PostgreSQLCommandAdapter(
        runner,
        _environment(),
        migration_dir=tmp_path,
    ).apply_migrations()

    assert result.succeeded
    assert result.applied == ("0002.sql",)
    migration_commands = [call for call, _ in runner.calls if "BEGIN;" in " ".join(call)]
    assert len(migration_commands) == 1
    assert "0002.sql" in " ".join(migration_commands[0])
    assert "0001.sql" not in " ".join(migration_commands[0])


def test_invalid_dsn_fails_without_running_commands(tmp_path: Path) -> None:
    runner = RecordingRunner()
    environment = _environment() | {"LOOP_POSTGRES_DSN": "not-a-postgres-url"}

    result = PostgreSQLCommandAdapter(
        runner,
        environment,
        migration_dir=tmp_path,
    ).apply_migrations()

    assert not result.succeeded
    assert result.detail == "POSTGRES_DSN_INVALID"
    assert runner.calls == []
