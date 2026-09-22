"""Controlled E2EのPostgreSQL durable evidenceを監査する。"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .config import LoopEngineeringSettings
from .operational_config import inject_operational_store_environment
from .postgres_runtime import PostgreSQLCommandAdapter
from .preflight import SubprocessCommandRunner


class E2EAuditCapabilities(Protocol):
    database: bool
    migration: bool


class E2EAuditDatabase(Protocol):
    def probe(self) -> E2EAuditCapabilities: ...

    def query_json_rows(
        self,
        select_sql: str,
    ) -> list[dict[str, object]] | None: ...


def audit_controlled_e2e(
    *,
    settings: LoopEngineeringSettings,
    environment: Mapping[str, str],
    database: E2EAuditDatabase,
) -> dict[str, object]:
    del environment
    capabilities = database.probe()
    if not capabilities.database or not capabilities.migration:
        raise RuntimeError("E2E_DATABASE_NOT_READY")

    runtime_rows = database.query_json_rows(
        "SELECT runtime_identity, goal_revision, status, current_work_identity "
        "FROM loop_autonomous_runtimes "
        f"WHERE product_key = {_literal(settings.project_key)} "
        f"AND repository = {_literal(settings.engine.repository)} "
        "ORDER BY updated_at DESC LIMIT 1"
    )
    if not runtime_rows or len(runtime_rows) != 1:
        raise RuntimeError("E2E_RUNTIME_MISSING")
    runtime = runtime_rows[0]
    runtime_identity = _required_string(runtime, "runtime_identity")
    goal_revision = _required_string(runtime, "goal_revision")
    if runtime.get("status") != "COMPLETED" or runtime.get("current_work_identity") is not None:
        raise RuntimeError("E2E_RUNTIME_NOT_COMPLETED")

    plan_rows = database.query_json_rows(
        "SELECT projection_json FROM loop_goal_plans "
        f"WHERE runtime_identity = {_literal(runtime_identity)} LIMIT 1"
    )
    if not plan_rows or len(plan_rows) != 1:
        raise RuntimeError("E2E_PLAN_MISSING")
    projection = plan_rows[0].get("projection_json")
    if not isinstance(projection, dict):
        raise RuntimeError("E2E_PLAN_INVALID")
    raw_works = projection.get("works")
    if not isinstance(raw_works, list) or not raw_works:
        raise RuntimeError("E2E_WORKS_MISSING")

    work_identities: list[str] = []
    expected_review_passes = sum(
        level.passes_required for level in settings.review_levels if level.required
    )
    if expected_review_passes < 1:
        raise RuntimeError("E2E_REQUIRED_REVIEW_PASS_MISSING")

    for raw in raw_works:
        if not isinstance(raw, dict):
            raise RuntimeError("E2E_WORK_PROJECTION_INVALID")
        issue_number = raw.get("issue_number")
        if not isinstance(issue_number, int) or issue_number < 1:
            raise RuntimeError("E2E_WORK_PROJECTION_INVALID")
        work_identity = (
            f"work:{settings.engine.repository}:{issue_number}"
        )
        work_identities.append(work_identity)

        work_rows = database.query_json_rows(
            "SELECT lifecycle FROM loop_work_records "
            f"WHERE identity = {_literal(work_identity)} LIMIT 1"
        )
        if not work_rows or work_rows[0].get("lifecycle") != "COMPLETED":
            raise RuntimeError("E2E_WORK_NOT_COMPLETED")

        local_rows = database.query_json_rows(
            "SELECT stage, local_pass_identity FROM loop_local_quality_state "
            f"WHERE work_identity = {_literal(work_identity)} LIMIT 1"
        )
        if not local_rows or local_rows[0].get("stage") != "LOCAL_PASS":
            raise RuntimeError("E2E_LOCAL_PASS_MISSING")
        if not _nonempty_string(local_rows[0].get("local_pass_identity")):
            raise RuntimeError("E2E_LOCAL_PASS_IDENTITY_MISSING")

        external_rows = database.query_json_rows(
            "SELECT status, completed_evidence FROM loop_external_review_state "
            f"WHERE work_identity = {_literal(work_identity)} LIMIT 1"
        )
        if not external_rows or external_rows[0].get("status") != "PASS":
            raise RuntimeError("E2E_EXTERNAL_PASS_MISSING")
        evidence = external_rows[0].get("completed_evidence")
        if not isinstance(evidence, list) or len(evidence) != expected_review_passes:
            raise RuntimeError("E2E_EXTERNAL_FRESH_PASS_COUNT_MISMATCH")
        if any(not _nonempty_string(item) for item in evidence):
            raise RuntimeError("E2E_EXTERNAL_EVIDENCE_INVALID")

    unresolved_dispatch = database.query_json_rows(
        "SELECT schedule_key, status FROM loop_autonomous_dispatches "
        f"WHERE runtime_identity = {_literal(runtime_identity)} "
        "AND status NOT IN ('COMPLETED', 'SUPERSEDED')"
    )
    if unresolved_dispatch is None or unresolved_dispatch:
        raise RuntimeError("E2E_UNRESOLVED_DISPATCH")

    work_list = ", ".join(_literal(item) for item in work_identities)
    pending_effects = database.query_json_rows(
        "SELECT idempotency_key, status FROM loop_effect_attempts "
        f"WHERE work_identity IN ({work_list}) "
        "AND status IN ('INTENT_RECORDED', 'UNCERTAIN')"
    )
    if pending_effects is None or pending_effects:
        raise RuntimeError("E2E_PENDING_WORK_EFFECT")

    pending_bootstrap = database.query_json_rows(
        "SELECT idempotency_key, status FROM loop_bootstrap_effects "
        f"WHERE product_key = {_literal(settings.project_key)} "
        f"AND repository = {_literal(settings.engine.repository)} "
        f"AND goal_revision = {_literal(goal_revision)} "
        "AND status IN ('INTENT_RECORDED', 'UNCERTAIN')"
    )
    if pending_bootstrap is None or pending_bootstrap:
        raise RuntimeError("E2E_PENDING_BOOTSTRAP_EFFECT")

    return {
        "status": "PASS",
        "runtime_identity": runtime_identity,
        "goal_revision": goal_revision,
        "work_count": len(work_identities),
        "required_external_fresh_passes_per_work": expected_review_passes,
        "pending_dispatches": 0,
        "pending_work_effects": 0,
        "pending_bootstrap_effects": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)

    platform_root = Path(__file__).resolve().parents[2]
    try:
        settings = LoopEngineeringSettings.load(
            platform_root,
            os.environ,
            config_path=args.config,
        )
        environment = inject_operational_store_environment(
            settings.config_path,
            settings.runtime_environment(os.environ),
        )
        result = audit_controlled_e2e(
            settings=settings,
            environment=environment,
            database=PostgreSQLCommandAdapter(
                SubprocessCommandRunner(),
                environment,
            ),
        )
    except (RuntimeError, ValueError) as error:
        print(
            json.dumps(
                {"status": "FAIL", "detail": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 3

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _literal(value: str) -> str:
    if "\x00" in value or len(value) > 4096:
        raise RuntimeError("E2E_SQL_VALUE_INVALID")
    return "'" + value.replace("'", "''") + "'"


def _required_string(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not _nonempty_string(value):
        raise RuntimeError("E2E_DATABASE_ROW_INVALID")
    assert isinstance(value, str)
    return value


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


if __name__ == "__main__":
    raise SystemExit(main())
