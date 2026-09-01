from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from loop_engineering.v2_execution_state import V2ExecutionStateStore
from loop_engineering.v2_operations import (
    V2MigrationAndIssuanceService,
    V2OperationStatus,
)
from loop_engineering.v2_resume import (
    WorkDefinitionPort,
    WorkDefinitionResult,
    WorkDefinitionStatus,
)
from loop_engineering.work_state import WorkRecord


@dataclass
class Definitions:
    status: WorkDefinitionStatus

    def synchronize(self, record: WorkRecord) -> WorkDefinitionResult:
        return WorkDefinitionResult(self.status)


@dataclass
class State:
    def work_record(self, work_identity: str) -> WorkRecord | None:
        return None


def service(status: WorkDefinitionStatus) -> V2MigrationAndIssuanceService:
    return V2MigrationAndIssuanceService(
        repository="ktan514/loop-engineering",
        definitions=cast(WorkDefinitionPort, Definitions(status)),
        state=cast(V2ExecutionStateStore, State()),
    )


def test_migration_reports_missing_acceptance_criteria_separately() -> None:
    result = service(WorkDefinitionStatus.ACCEPTANCE_CRITERIA_MISSING).migrate_issue(67)

    assert result.status is V2OperationStatus.BLOCKED
    assert result.detail == "ACCEPTANCE_CRITERIA_DIGEST_MISSING"
    assert result.work_identity == "work:ktan514/loop-engineering:67"


def test_migration_keeps_provider_unavailable_distinct() -> None:
    result = service(WorkDefinitionStatus.UNAVAILABLE).migrate_issue(67)

    assert result.status is V2OperationStatus.BLOCKED
    assert result.detail == "MIGRATION_DEFINITION_UNAVAILABLE"
