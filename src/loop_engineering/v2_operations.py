"""V2切替移行と作業パケット明示発行を、Host実行から分離して提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .v2_execution_state import (
    V2ExecutionStateStore,
    V2PacketIssueResult,
    V2PacketPlan,
)
from .v2_resume import WorkDefinitionPort, WorkDefinitionStatus
from .work_state import WorkRecord, WorkStateUnavailable


class V2OperationStatus(str, Enum):
    MIGRATED_PACKET_REQUIRED = "MIGRATED_PACKET_REQUIRED"
    PACKET_ISSUED = "PACKET_ISSUED"
    PACKET_ALREADY_ISSUED = "PACKET_ALREADY_ISSUED"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class V2OperationResult:
    status: V2OperationStatus
    detail: str
    work_identity: str | None = None
    packet_identity: str | None = None


@dataclass(slots=True)
class V2MigrationAndIssuanceService:
    repository: str
    definitions: WorkDefinitionPort
    state: V2ExecutionStateStore

    def migrate_issue(self, issue_number: int) -> V2OperationResult:
        if issue_number < 1:
            return V2OperationResult(V2OperationStatus.BLOCKED, "MIGRATION_ISSUE_INVALID")
        work_identity = f"work:{self.repository}:{issue_number}"
        try:
            existing = self.state.work_record(work_identity)
            if existing is not None:
                if (
                    existing.repository == self.repository
                    and existing.issue_number == issue_number
                    and self.state.is_cutover(self.repository)
                ):
                    return V2OperationResult(
                        V2OperationStatus.MIGRATED_PACKET_REQUIRED,
                        "MIGRATION_ALREADY_RECORDED",
                        work_identity,
                    )
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "MIGRATION_WORK_CONFLICT",
                    work_identity,
                )
            candidate = WorkRecord(
                identity=work_identity,
                repository=self.repository,
                issue_number=issue_number,
                issue_revision="migration:pending",
                lifecycle="PLANNED",
            )
            definition = self.definitions.synchronize(candidate)
            if definition.status is WorkDefinitionStatus.DEPENDENCY_PENDING:
                return V2OperationResult(
                    V2OperationStatus.WAITING,
                    "DEPENDENCY_PENDING",
                    work_identity,
                )
            if definition.status is WorkDefinitionStatus.CLOSED_BEFORE_COMPLETION:
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "WORK_CLOSED_BEFORE_MIGRATION",
                    work_identity,
                )
            if definition.status is not WorkDefinitionStatus.READY or definition.record is None:
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "MIGRATION_DEFINITION_UNAVAILABLE",
                    work_identity,
                )
            synchronized = definition.record
            if (
                synchronized.identity != candidate.identity
                or synchronized.repository != candidate.repository
                or synchronized.issue_number != candidate.issue_number
                or not synchronized.issue_revision
            ):
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "MIGRATION_DEFINITION_CONFLICT",
                    work_identity,
                )
            migrated = self.state.migrate_candidate(synchronized)
            recorded = self.state.work_record(work_identity)
            cutover = self.state.is_cutover(self.repository)
            if not migrated and (recorded is None or not cutover):
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "MIGRATION_TRANSACTION_REJECTED",
                    work_identity,
                )
            if (
                recorded is None
                or recorded.issue_revision != synchronized.issue_revision
                or not cutover
            ):
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "MIGRATION_READBACK_MISMATCH",
                    work_identity,
                )
            return V2OperationResult(
                V2OperationStatus.MIGRATED_PACKET_REQUIRED,
                "MIGRATION_RECORDED_PACKET_REQUIRED",
                work_identity,
            )
        except WorkStateUnavailable as error:
            return V2OperationResult(V2OperationStatus.BLOCKED, str(error), work_identity)

    def issue_packet(
        self,
        *,
        work_identity: str,
        generation: int,
        plan: V2PacketPlan,
        run_identity: str,
    ) -> V2OperationResult:
        try:
            if not self.state.is_cutover(self.repository):
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "V2_REPOSITORY_NOT_CUTOVER",
                    work_identity,
                )
            current = self.state.work_record(work_identity)
            if current is None or current.repository != self.repository:
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "WORK_NOT_MIGRATED",
                    work_identity,
                )
            definition = self.definitions.synchronize(current)
            if definition.status is WorkDefinitionStatus.DEPENDENCY_PENDING:
                return V2OperationResult(
                    V2OperationStatus.WAITING,
                    "DEPENDENCY_PENDING",
                    work_identity,
                )
            if definition.status is not WorkDefinitionStatus.READY or definition.record is None:
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "WORK_DEFINITION_UNAVAILABLE",
                    work_identity,
                )
            synchronized = definition.record
            if not _same_definition(current, synchronized):
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "WORK_DEFINITION_CONFLICT",
                    work_identity,
                )
            issued = self.state.issue_packet(
                record=synchronized,
                generation=generation,
                plan=plan,
                run_identity=run_identity,
            )
            if issued is None:
                return V2OperationResult(
                    V2OperationStatus.BLOCKED,
                    "PACKET_ISSUE_TRANSACTION_REJECTED",
                    work_identity,
                )
            return _packet_result(issued)
        except WorkStateUnavailable as error:
            return V2OperationResult(V2OperationStatus.BLOCKED, str(error), work_identity)


def _packet_result(issued: V2PacketIssueResult) -> V2OperationResult:
    if issued.already_issued:
        return V2OperationResult(
            V2OperationStatus.PACKET_ALREADY_ISSUED,
            "PACKET_ALREADY_ISSUED",
            issued.packet.work_identity,
            issued.packet.identity,
        )
    return V2OperationResult(
        V2OperationStatus.PACKET_ISSUED,
        "PACKET_ISSUED",
        issued.packet.work_identity,
        issued.packet.identity,
    )


def _same_definition(before: WorkRecord, after: WorkRecord) -> bool:
    return (
        before.identity == after.identity
        and before.repository == after.repository
        and before.issue_number == after.issue_number
        and before.issue_revision == after.issue_revision
    )
