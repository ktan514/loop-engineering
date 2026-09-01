"""V2の明示CLI操作と旧Host切替境界を提供する。"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import LoopEngineeringSettings
from .postgres_runtime import PostgreSQLCommandAdapter
from .preflight import SubprocessCommandRunner
from .v2_effect_executor import (
    GitHubV2EffectExecutor,
    SubprocessV2CommandRunner,
    production_environment,
)
from .v2_effect_readback import GitHubEffectReadbackAdapter, GitHubIssueReportPublisher
from .v2_execution_state import V2ExecutionStateStore, build_packet_plan
from .v2_host_entrypoint import V2Host, V2HostStatus
from .v2_operations import (
    V2MigrationAndIssuanceService,
    V2OperationResult,
    V2OperationStatus,
)
from .v2_resume import V2ResumeCoordinator
from .v2_work_definition import GitHubWorkDefinitionAdapter
from .work_state import PostgreSQLWorkStateStore, WorkStateUnavailable


@dataclass(frozen=True, slots=True)
class V2CliComponents:
    database: PostgreSQLCommandAdapter
    execution_state: V2ExecutionStateStore
    operations: V2MigrationAndIssuanceService
    host: V2Host


def add_v2_arguments(parser: argparse.ArgumentParser) -> None:
    """V2専用の明示CLI引数を追加する。"""
    parser.add_argument(
        "--migrate-v2-work-state",
        type=int,
        metavar="ISSUE_NUMBER",
        help="指定Issueを型付き定義からV2 Workへ移行し、RepositoryをV2へ切り替える。",
    )
    parser.add_argument(
        "--issue-v2-packet",
        metavar="WORK_IDENTITY",
        help="移行済みWorkへ型付き作業パケットを明示発行する。",
    )
    parser.add_argument(
        "--v2-once",
        metavar="WORK_IDENTITY",
        help="指定済みV2 Workの既存packetを最大1遷移だけ処理する。",
    )
    parser.add_argument("--v2-generation", type=int, help="発行するpacket generation。1以上を明示する。")
    parser.add_argument("--v2-transition", help="発行するpacketの遷移識別子。")
    parser.add_argument(
        "--v2-effect-kind",
        choices=("PUSH", "READY", "MERGE", "ISSUE_UPDATE"),
        help="発行するpacketの外部effect種別。",
    )
    parser.add_argument("--v2-target", help="branch:/pr:/issue:形式の型付き外部対象identity。")
    parser.add_argument(
        "--v2-before",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="外部effect前の期待値。複数指定できる。",
    )
    parser.add_argument(
        "--v2-after",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="外部effect後の期待値。複数指定できる。",
    )
    parser.add_argument(
        "--v2-design",
        action="append",
        default=[],
        metavar="IDENTITY",
        help="packetへ結び付けるcanonical design identity。複数指定できる。",
    )


def v2_requested(arguments: argparse.Namespace) -> bool:
    return any(
        (
            arguments.migrate_v2_work_state is not None,
            arguments.issue_v2_packet is not None,
            arguments.v2_once is not None,
        )
    )


def run_v2_command(
    arguments: argparse.Namespace,
    *,
    settings: LoopEngineeringSettings,
    environment: Mapping[str, str],
) -> int | None:
    """明示されたV2操作だけを実行し、旧Hostへ暗黙委譲しない。"""
    if not v2_requested(arguments):
        return None
    selected = sum(
        value is not None
        for value in (
            arguments.migrate_v2_work_state,
            arguments.issue_v2_packet,
            arguments.v2_once,
        )
    )
    if selected != 1:
        return _print_blocked("V2_COMMAND_CONFLICT")
    if not _database_configured(environment):
        return _print_blocked("V2_DATABASE_UNCONFIGURED")

    try:
        components = _components(settings, environment)
    except (ValueError, WorkStateUnavailable):
        return _print_blocked("V2_COMPONENT_CONFIGURATION_INVALID")
    capabilities = components.database.probe()
    if not capabilities.database:
        return _print_blocked("V2_DATABASE_UNAVAILABLE")
    if not capabilities.migration:
        return _print_blocked("V2_SCHEMA_MIGRATION_REQUIRED")

    if arguments.migrate_v2_work_state is not None:
        if _packet_arguments_present(arguments):
            return _print_blocked("V2_PACKET_ARGUMENT_WITH_MIGRATION")
        result = components.operations.migrate_issue(arguments.migrate_v2_work_state)
        return _print_operation(result)

    work_identity = arguments.issue_v2_packet or arguments.v2_once
    if not isinstance(work_identity, str) or not _valid_work_identity(
        settings.engine.repository, work_identity
    ):
        return _print_blocked("V2_WORK_IDENTITY_INVALID")

    if arguments.issue_v2_packet is not None:
        try:
            generation = _required_generation(arguments.v2_generation)
            transition = _required_text(arguments.v2_transition, "V2_TRANSITION_REQUIRED")
            effect_kind = _required_text(arguments.v2_effect_kind, "V2_EFFECT_KIND_REQUIRED")
            target = _required_text(arguments.v2_target, "V2_TARGET_REQUIRED")
            before = _parse_pairs(arguments.v2_before)
            after = _parse_pairs(arguments.v2_after)
            designs = _parse_designs(arguments.v2_design)
            plan = build_packet_plan(
                work_identity=work_identity,
                generation=generation,
                transition=transition,
                effect_kind=effect_kind,
                target_identity=target,
                expected_preconditions=before,
                expected_effect=after,
                canonical_design_identities=designs,
            )
        except WorkStateUnavailable as error:
            return _print_blocked(str(error))
        result = components.operations.issue_packet(
            work_identity=work_identity,
            generation=generation,
            plan=plan,
            run_identity=f"run:packet-issue:{uuid.uuid4().hex}",
        )
        return _print_operation(result)

    if _packet_arguments_present(arguments):
        return _print_blocked("V2_PACKET_ARGUMENT_WITH_ONCE")
    result = components.host.run_once(work_identity)
    print(result.as_json())
    if result.status in {V2HostStatus.TRANSITION_COMPLETED, V2HostStatus.WORK_COMPLETED}:
        return 0
    if result.status is V2HostStatus.WAITING:
        return 2
    return 3


def legacy_host_block_reason(
    *,
    settings: LoopEngineeringSettings,
    environment: Mapping[str, str],
) -> str | None:
    """RepositoryがV2切替済みなら旧Host入口をfail-closedで拒否する。"""
    if not _database_configured(environment):
        return None
    database = PostgreSQLCommandAdapter(SubprocessCommandRunner(), environment)
    table_rows = database.query_json_rows(
        "SELECT to_regclass('public.loop_v2_cutovers')::text AS table_name"
    )
    if table_rows is None:
        return "V2_CUTOVER_STATE_UNAVAILABLE"
    if not table_rows or table_rows[0].get("table_name") is None:
        return None
    try:
        if V2ExecutionStateStore(database).is_cutover(settings.engine.repository):
            return "V2_REPOSITORY_CUTOVER_LEGACY_HOST_FORBIDDEN"
    except WorkStateUnavailable:
        return "V2_CUTOVER_STATE_UNAVAILABLE"
    return None


def _components(
    settings: LoopEngineeringSettings,
    environment: Mapping[str, str],
) -> V2CliComponents:
    database = PostgreSQLCommandAdapter(SubprocessCommandRunner(), environment)
    execution_state = V2ExecutionStateStore(database)
    work_state = PostgreSQLWorkStateStore(database)
    command_runner = SubprocessV2CommandRunner(
        settings.workspace_path,
        production_environment(environment),
    )
    definitions = GitHubWorkDefinitionAdapter(command_runner, settings.engine.project_number)
    readback = GitHubEffectReadbackAdapter(command_runner, settings.engine.repository)
    operations = V2MigrationAndIssuanceService(
        settings.engine.repository,
        definitions,
        execution_state,
    )
    resume = V2ResumeCoordinator(work_state, definitions, readback)
    executor = GitHubV2EffectExecutor(
        command_runner,
        readback,
        settings.engine.repository,
        settings.engine,
    )
    publisher = GitHubIssueReportPublisher(command_runner, work_state)
    host = V2Host(resume, execution_state, work_state, readback, executor, publisher)
    return V2CliComponents(database, execution_state, operations, host)


def _database_configured(environment: Mapping[str, str]) -> bool:
    return bool(
        environment.get("LOOP_POSTGRES_DSN", "").strip()
        or environment.get("LOOP_DATABASE_URL", "").strip()
    )


def _packet_arguments_present(arguments: argparse.Namespace) -> bool:
    return any(
        (
            arguments.v2_generation is not None,
            arguments.v2_transition is not None,
            arguments.v2_effect_kind is not None,
            arguments.v2_target is not None,
            bool(arguments.v2_before),
            bool(arguments.v2_after),
            bool(arguments.v2_design),
        )
    )


def _required_generation(value: int | None) -> int:
    if value is None or value < 1:
        raise WorkStateUnavailable("V2_GENERATION_REQUIRED")
    return value


def _required_text(value: str | None, detail: str) -> str:
    if value is None or not value.strip() or value.strip() != value or "\x00" in value:
        raise WorkStateUnavailable(detail)
    return value


def _parse_pairs(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    parsed: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise WorkStateUnavailable("V2_EXPECTATION_INVALID")
        key, value = raw.split("=", maxsplit=1)
        if (
            not key
            or key in parsed
            or not value
            or key.strip() != key
            or "\x00" in key
            or "\x00" in value
        ):
            raise WorkStateUnavailable("V2_EXPECTATION_INVALID")
        parsed[key] = value
    if not parsed:
        raise WorkStateUnavailable("V2_EXPECTATION_REQUIRED")
    return tuple(sorted(parsed.items()))


def _parse_designs(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if (
            not value
            or value in seen
            or value.strip() != value
            or "\x00" in value
            or len(value) > 1024
        ):
            raise WorkStateUnavailable("V2_DESIGN_IDENTITY_INVALID")
        result.append(value)
        seen.add(value)
    return tuple(result)


def _valid_work_identity(repository: str, work_identity: str) -> bool:
    prefix = f"work:{repository}:"
    if not work_identity.startswith(prefix):
        return False
    issue = work_identity[len(prefix) :]
    return issue.isdigit() and int(issue) > 0


def _print_operation(result: V2OperationResult) -> int:
    print(
        json.dumps(
            {
                "status": result.status.value,
                "detail": result.detail,
                "work_identity": result.work_identity,
                "packet_identity": result.packet_identity,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if result.status in {
        V2OperationStatus.MIGRATED_PACKET_REQUIRED,
        V2OperationStatus.PACKET_ISSUED,
        V2OperationStatus.PACKET_ALREADY_ISSUED,
    }:
        return 0
    if result.status is V2OperationStatus.WAITING:
        return 2
    return 3


def _print_blocked(detail: str) -> int:
    print(json.dumps({"status": "BLOCKED", "detail": detail}, sort_keys=True))
    return 3
