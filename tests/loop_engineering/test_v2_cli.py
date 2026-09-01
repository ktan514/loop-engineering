from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import cast

import pytest

import loop_engineering.v2_cli as v2_cli
from loop_engineering.config import LoopEngineeringSettings
from loop_engineering.v2_cli import add_v2_arguments, legacy_host_block_reason, run_v2_command
from loop_engineering.v2_host_entrypoint import V2HostResult, V2HostStatus
from loop_engineering.v2_operations import V2OperationResult, V2OperationStatus

from .conftest import config


@dataclass(frozen=True)
class Settings:
    engine: object = field(default_factory=config)


@dataclass(frozen=True)
class Capabilities:
    database: bool = True
    migration: bool = True


@dataclass
class Database:
    capabilities: Capabilities = field(default_factory=Capabilities)

    def probe(self) -> Capabilities:
        return self.capabilities


@dataclass
class Operations:
    migration_result: V2OperationResult = field(
        default_factory=lambda: V2OperationResult(
            V2OperationStatus.MIGRATED_PACKET_REQUIRED,
            "MIGRATION_RECORDED_PACKET_REQUIRED",
            "work:ktan514/ai-liver-yura:67",
        )
    )
    issue_result: V2OperationResult = field(
        default_factory=lambda: V2OperationResult(
            V2OperationStatus.PACKET_ISSUED,
            "PACKET_ISSUED",
            "work:ktan514/ai-liver-yura:67",
            "packet:1",
        )
    )
    migrations: list[int] = field(default_factory=list)
    issues: list[tuple[str, int]] = field(default_factory=list)

    def migrate_issue(self, issue_number: int) -> V2OperationResult:
        self.migrations.append(issue_number)
        return self.migration_result

    def issue_packet(self, **kwargs: object) -> V2OperationResult:
        self.issues.append((str(kwargs["work_identity"]), int(kwargs["generation"])))
        return self.issue_result


@dataclass
class Host:
    result: V2HostResult = field(
        default_factory=lambda: V2HostResult(
            V2HostStatus.TRANSITION_COMPLETED,
            "CONFIRMED",
            "work:ktan514/ai-liver-yura:67",
            "packet:1",
        )
    )
    calls: list[str] = field(default_factory=list)

    def run_once(self, work_identity: str) -> V2HostResult:
        self.calls.append(work_identity)
        return self.result


@dataclass
class Components:
    database: Database = field(default_factory=Database)
    operations: Operations = field(default_factory=Operations)
    host: Host = field(default_factory=Host)


def settings() -> LoopEngineeringSettings:
    return cast(LoopEngineeringSettings, Settings())


def arguments(*values: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_v2_arguments(parser)
    return parser.parse_args(values)


def test_packet_generation_is_explicit_and_same_command_does_not_infer_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = Components()
    monkeypatch.setattr(v2_cli, "_components", lambda *_args, **_kwargs: components)
    args = arguments(
        "--issue-v2-packet",
        "work:ktan514/ai-liver-yura:67",
        "--v2-generation",
        "4",
        "--v2-transition",
        "READY_PR",
        "--v2-effect-kind",
        "READY",
        "--v2-target",
        "pr:70",
        "--v2-before",
        "head=abc",
        "--v2-before",
        "draft=true",
        "--v2-after",
        "draft=false",
    )

    result = run_v2_command(
        args,
        settings=settings(),
        environment={"LOOP_POSTGRES_DSN": "postgresql://configured"},
    )

    assert result == 0
    assert components.operations.issues == [("work:ktan514/ai-liver-yura:67", 4)]


def test_packet_generation_omission_blocks_before_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = Components()
    monkeypatch.setattr(v2_cli, "_components", lambda *_args, **_kwargs: components)
    args = arguments(
        "--issue-v2-packet",
        "work:ktan514/ai-liver-yura:67",
        "--v2-transition",
        "READY_PR",
        "--v2-effect-kind",
        "READY",
        "--v2-target",
        "pr:70",
        "--v2-before",
        "head=abc",
        "--v2-before",
        "draft=true",
        "--v2-after",
        "draft=false",
    )

    result = run_v2_command(
        args,
        settings=settings(),
        environment={"LOOP_POSTGRES_DSN": "postgresql://configured"},
    )

    assert result == 3
    assert components.operations.issues == []


def test_v2_once_rejects_packet_issuance_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = Components()
    monkeypatch.setattr(v2_cli, "_components", lambda *_args, **_kwargs: components)
    args = arguments(
        "--v2-once",
        "work:ktan514/ai-liver-yura:67",
        "--v2-generation",
        "1",
    )

    result = run_v2_command(
        args,
        settings=settings(),
        environment={"LOOP_POSTGRES_DSN": "postgresql://configured"},
    )

    assert result == 3
    assert components.host.calls == []


def test_v2_once_runs_only_explicit_work(monkeypatch: pytest.MonkeyPatch) -> None:
    components = Components()
    monkeypatch.setattr(v2_cli, "_components", lambda *_args, **_kwargs: components)
    args = arguments("--v2-once", "work:ktan514/ai-liver-yura:67")

    result = run_v2_command(
        args,
        settings=settings(),
        environment={"LOOP_POSTGRES_DSN": "postgresql://configured"},
    )

    assert result == 0
    assert components.host.calls == ["work:ktan514/ai-liver-yura:67"]


def test_migration_rejects_packet_plan_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    components = Components()
    monkeypatch.setattr(v2_cli, "_components", lambda *_args, **_kwargs: components)
    args = arguments("--migrate-v2-work-state", "67", "--v2-generation", "1")

    result = run_v2_command(
        args,
        settings=settings(),
        environment={"LOOP_POSTGRES_DSN": "postgresql://configured"},
    )

    assert result == 3
    assert components.operations.migrations == []


@dataclass
class CutoverDatabase:
    rows: list[list[dict[str, object]]]

    def query_json_rows(self, _sql: str) -> list[dict[str, object]] | None:
        return self.rows.pop(0)

    def execute_sql(self, _sql: str) -> bool:
        return True

    def execute_transaction_json(self, _sql: str) -> dict[str, object] | None:
        return {"ok": True}


def test_legacy_host_is_rejected_after_repository_cutover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = CutoverDatabase(
        [
            [{"table_name": "loop_v2_cutovers"}],
            [{"repository": "ktan514/ai-liver-yura"}],
        ]
    )
    monkeypatch.setattr(
        v2_cli,
        "PostgreSQLCommandAdapter",
        lambda *_args, **_kwargs: database,
    )

    reason = legacy_host_block_reason(
        settings=settings(),
        environment={"LOOP_POSTGRES_DSN": "postgresql://configured"},
    )

    assert reason == "V2_REPOSITORY_CUTOVER_LEGACY_HOST_FORBIDDEN"


def test_legacy_host_remains_available_before_cutover_table_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = CutoverDatabase([[{"table_name": None}]])
    monkeypatch.setattr(
        v2_cli,
        "PostgreSQLCommandAdapter",
        lambda *_args, **_kwargs: database,
    )

    reason = legacy_host_block_reason(
        settings=settings(),
        environment={"LOOP_POSTGRES_DSN": "postgresql://configured"},
    )

    assert reason is None
