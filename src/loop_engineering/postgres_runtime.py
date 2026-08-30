"""Dockerまたはホスト経由でPostgreSQL管理基盤を検査・移行する。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlsplit


class CommandResultLike(Protocol):
    @property
    def succeeded(self) -> bool: ...

    @property
    def output(self) -> str: ...


class PostgreSQLCommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> CommandResultLike: ...


@dataclass(frozen=True, slots=True)
class PostgreSQLCapabilities:
    client: bool
    server: bool
    database: bool
    migration: bool


@dataclass(frozen=True, slots=True)
class MigrationApplyResult:
    succeeded: bool
    applied: tuple[str, ...]
    detail: str


class PostgreSQLCommandAdapter:
    """秘密値をargvへ埋め込まずPostgreSQLを操作する接続層。"""

    _DRIVERS = frozenset({"docker", "host"})

    def __init__(
        self,
        runner: PostgreSQLCommandRunner,
        environment: Mapping[str, str],
        *,
        migration_dir: Path | None = None,
    ) -> None:
        self._runner = runner
        self._environment = environment
        self._driver = environment.get("LOOP_POSTGRES_DRIVER", "host").strip() or "host"
        self._container = environment.get("LOOP_POSTGRES_CONTAINER", "").strip()
        self._dsn = (
            environment.get("LOOP_POSTGRES_DSN", "").strip()
            or environment.get("LOOP_DATABASE_URL", "").strip()
        )
        self._migration_dir = migration_dir or Path(__file__).with_name("migrations")

    def probe(self) -> PostgreSQLCapabilities:
        parsed = self._parsed_dsn()
        if parsed is None or self._driver not in self._DRIVERS:
            return PostgreSQLCapabilities(False, False, False, False)
        if self._driver == "docker" and not self._container:
            return PostgreSQLCapabilities(False, False, False, False)

        client = self._run_client(("psql", "--version"), parsed).succeeded
        server = client and self._run_client(("pg_isready",), parsed).succeeded
        database = server and self._run_client(
            ("psql", "-Atqc", "SELECT 1"), parsed
        ).succeeded
        migration = database and self._migrations_current(parsed)
        return PostgreSQLCapabilities(client, server, database, migration)

    def apply_migrations(self) -> MigrationApplyResult:
        parsed = self._parsed_dsn()
        if parsed is None:
            return MigrationApplyResult(False, (), "POSTGRES_DSN_INVALID")
        if self._driver not in self._DRIVERS:
            return MigrationApplyResult(False, (), "POSTGRES_DRIVER_INVALID")
        if self._driver == "docker" and not self._container:
            return MigrationApplyResult(False, (), "POSTGRES_CONTAINER_UNSET")
        if not self._run_client(("psql", "--version"), parsed).succeeded:
            return MigrationApplyResult(False, (), "POSTGRES_CLIENT_UNAVAILABLE")
        if not self._run_client(("pg_isready",), parsed).succeeded:
            return MigrationApplyResult(False, (), "POSTGRES_SERVER_UNAVAILABLE")
        if not self._run_client(("psql", "-Atqc", "SELECT 1"), parsed).succeeded:
            return MigrationApplyResult(False, (), "POSTGRES_DATABASE_UNAVAILABLE")

        registry_sql = (
            "CREATE TABLE IF NOT EXISTS loop_schema_migrations ("
            "filename TEXT PRIMARY KEY, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ");"
        )
        if not self.execute_sql(registry_sql):
            return MigrationApplyResult(False, (), "MIGRATION_REGISTRY_UNAVAILABLE")

        applied = self._applied_migrations(parsed)
        if applied is None:
            return MigrationApplyResult(False, (), "MIGRATION_STATE_UNAVAILABLE")

        completed: list[str] = []
        for path in self._migration_files():
            if path.name in applied:
                continue
            try:
                sql = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return MigrationApplyResult(False, tuple(completed), "MIGRATION_SOURCE_UNREADABLE")
            filename = path.name.replace("'", "''")
            transaction = (
                "BEGIN;\n"
                f"{sql}\n"
                "INSERT INTO loop_schema_migrations (filename) "
                f"VALUES ('{filename}') ON CONFLICT (filename) DO NOTHING;\n"
                "COMMIT;"
            )
            if not self.execute_sql(transaction):
                return MigrationApplyResult(False, tuple(completed), "MIGRATION_APPLY_FAILED")
            completed.append(path.name)
        return MigrationApplyResult(True, tuple(completed), "MIGRATION_CURRENT")

    def execute_sql(self, sql: str) -> bool:
        """秘密を含まない内部SQLを1回実行する。"""
        parsed = self._parsed_dsn()
        if parsed is None or self._driver not in self._DRIVERS:
            return False
        if self._driver == "docker" and not self._container:
            return False
        return self._run_client(
            ("psql", "-v", "ON_ERROR_STOP=1", "-q", "-c", sql),
            parsed,
        ).succeeded

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None:
        """秘密を含まない内部SELECTをJSON配列として読み戻す。"""
        parsed = self._parsed_dsn()
        if parsed is None or self._driver not in self._DRIVERS:
            return None
        if self._driver == "docker" and not self._container:
            return None
        statement = (
            "SELECT COALESCE(json_agg(row_to_json(loop_query)), '[]'::json)::text "
            f"FROM ({select_sql}) AS loop_query"
        )
        result = self._run_client(("psql", "-Atqc", statement), parsed)
        if not result.succeeded:
            return None
        try:
            payload = json.loads(result.output.strip() or "[]")
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            return None
        return [cast(dict[str, object], item) for item in payload]

    def _migrations_current(self, parsed: _ParsedDSN) -> bool:
        expected = {path.name for path in self._migration_files()}
        if not expected:
            return False
        applied = self._applied_migrations(parsed)
        return applied is not None and expected.issubset(applied)

    def _applied_migrations(self, parsed: _ParsedDSN) -> set[str] | None:
        result = self._run_client(
            (
                "psql",
                "-Atqc",
                "SELECT filename FROM loop_schema_migrations ORDER BY filename",
            ),
            parsed,
        )
        if not result.succeeded:
            return None
        return {line.strip() for line in result.output.splitlines() if line.strip()}

    def _migration_files(self) -> tuple[Path, ...]:
        if not self._migration_dir.is_dir():
            return ()
        return tuple(sorted(self._migration_dir.glob("*.sql"), key=lambda path: path.name))

    def _run_client(self, command: Sequence[str], parsed: _ParsedDSN) -> CommandResultLike:
        environment = self._database_environment(parsed)
        if self._driver == "docker":
            docker_command = (
                "docker",
                "exec",
                "-e",
                "PGUSER",
                "-e",
                "PGPASSWORD",
                "-e",
                "PGDATABASE",
                self._container,
                *command,
            )
            return self._runner.run(docker_command, environment)
        return self._runner.run(command, environment)

    def _database_environment(self, parsed: _ParsedDSN) -> dict[str, str]:
        names = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
        values = {name: self._environment[name] for name in names if self._environment.get(name)}
        values.setdefault("PATH", os.defpath)
        values["PGUSER"] = parsed.username
        values["PGPASSWORD"] = parsed.password
        values["PGDATABASE"] = parsed.database
        if self._driver == "host":
            values["PGHOST"] = parsed.hostname
            values["PGPORT"] = str(parsed.port)
        return values

    def _parsed_dsn(self) -> _ParsedDSN | None:
        if not self._dsn:
            return None
        parsed = urlsplit(self._dsn)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            return None
        try:
            port = parsed.port or 5432
        except ValueError:
            return None
        database = parsed.path.lstrip("/")
        if not database:
            return None
        return _ParsedDSN(
            parsed.hostname,
            port,
            parsed.username or "",
            parsed.password or "",
            database,
        )


@dataclass(frozen=True, slots=True)
class _ParsedDSN:
    hostname: str
    port: int
    username: str
    password: str
    database: str
