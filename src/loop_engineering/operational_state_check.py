"""ProductやGitHubを変更せずOperational Storeのwrite/readbackを確認する。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .postgres_runtime import PostgreSQLCommandAdapter


@dataclass(frozen=True, slots=True)
class OperationalStateCheckResult:
    succeeded: bool
    detail: str


def check_operational_state_round_trip(
    database: PostgreSQLCommandAdapter,
    *,
    project_key: str,
    repository: str,
) -> OperationalStateCheckResult:
    """synthetic terminal runを1件だけ記録し、同一値をreadbackする。"""
    identity = f"health:{uuid.uuid4().hex}"
    insert = (
        "INSERT INTO loop_runs (identity, project_key, repository, status) VALUES ("
        f"{_literal(identity)}, {_literal(project_key)}, {_literal(repository)}, 'RUNNING')"
    )
    if not database.execute_sql(insert):
        return OperationalStateCheckResult(False, "WRITE_FAILED")

    running = database.query_json_rows(
        "SELECT identity, project_key, repository, status FROM loop_runs "
        f"WHERE identity = {_literal(identity)} LIMIT 1"
    )
    if not _matches(running, identity, project_key, repository, "RUNNING"):
        return OperationalStateCheckResult(False, "RUNNING_READBACK_FAILED")

    completed = database.execute_sql(
        "UPDATE loop_runs SET status = 'COMPLETED', finished_at = now() "
        f"WHERE identity = {_literal(identity)}"
    )
    if not completed:
        return OperationalStateCheckResult(False, "FINALIZE_FAILED")

    terminal = database.query_json_rows(
        "SELECT identity, project_key, repository, status FROM loop_runs "
        f"WHERE identity = {_literal(identity)} LIMIT 1"
    )
    if not _matches(terminal, identity, project_key, repository, "COMPLETED"):
        return OperationalStateCheckResult(False, "TERMINAL_READBACK_FAILED")
    return OperationalStateCheckResult(True, "ROUND_TRIP_PASS")


def _matches(
    rows: list[dict[str, object]] | None,
    identity: str,
    project_key: str,
    repository: str,
    status: str,
) -> bool:
    if rows is None or len(rows) != 1:
        return False
    row = rows[0]
    return (
        row.get("identity") == identity
        and row.get("project_key") == project_key
        and row.get("repository") == repository
        and row.get("status") == status
    )


def _literal(value: str) -> str:
    if "\x00" in value or len(value) > 1024:
        raise ValueError("Operational State確認値が不正です")
    return "'" + value.replace("'", "''") + "'"
