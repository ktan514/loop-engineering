"""Goal配下のGitHub Workをtyped snapshotで列挙し、V2 DB状態へ同期する。"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from .v2_execution_state import V2ExecutionStateStore
from .v2_goal_planning import ProductDevelopmentRegistration
from .v2_supervisor import EvidenceState, V2WorkObservation
from .v2_work_definition import GitHubWorkDefinitionAdapter, WorkDefinitionSnapshot
from .work_state import PostgreSQLWorkStateStore, WorkRecord, WorkStateUnavailable


class WorkQueueUnavailable(RuntimeError):
    """Work Queueを安全に観測・同期できない。"""


class WorkQueueCommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str: ...


@dataclass(frozen=True, slots=True)
class V2WorkQueueSnapshot:
    works: tuple[V2WorkObservation, ...]
    current_work_identity: str | None
    pending_effect: bool


class GitHubV2WorkQueue:
    """bootstrap markerでGoal配下Issueだけを発見し、typed Project定義を読む。"""

    def __init__(
        self,
        runner: WorkQueueCommandRunner,
        definitions: GitHubWorkDefinitionAdapter,
        execution_state: V2ExecutionStateStore,
        work_state: PostgreSQLWorkStateStore,
    ) -> None:
        self._runner = runner
        self._definitions = definitions
        self._execution_state = execution_state
        self._work_state = work_state

    def synchronize(self, registration: ProductDevelopmentRegistration) -> V2WorkQueueSnapshot:
        issue_keys = self._discover(registration)
        observations: list[V2WorkObservation] = []
        current: list[str] = []
        pending_effect = False

        for logical_key, issue_number in issue_keys:
            definition = self._definitions.snapshot(
                registration.repository_identity,
                issue_number,
            )
            if definition is None:
                raise WorkQueueUnavailable("WORK_DEFINITION_UNAVAILABLE")
            if definition.issue_state != "CLOSED" and not definition.acceptance_criteria_digest:
                raise WorkQueueUnavailable("WORK_ACCEPTANCE_CRITERIA_MISSING")
            work_identity = f"work:{registration.repository_identity}:{issue_number}"
            record = self._execution_state.work_record(work_identity)
            if record is None:
                candidate = WorkRecord(
                    identity=work_identity,
                    repository=registration.repository_identity,
                    issue_number=issue_number,
                    issue_revision=definition.revision,
                    lifecycle="PLANNED",
                )
                if not self._execution_state.migrate_candidate(candidate):
                    raise WorkQueueUnavailable("WORK_MIGRATION_FAILED")
                record = self._execution_state.work_record(work_identity)
                if record is None:
                    raise WorkQueueUnavailable("WORK_MIGRATION_READBACK_FAILED")
            elif record.issue_revision != definition.revision:
                updated = replace(record, issue_revision=definition.revision)
                self._work_state.upsert_work(updated)
                record = updated

            recovered = self._work_state.recover(work_identity)
            if recovered is None:
                raise WorkQueueUnavailable("WORK_STATE_RECOVERY_FAILED")
            record = recovered.record
            if record.lifecycle in {"SELECTED", "RUNNING"}:
                current.append(record.identity)
            if recovered.pending_effects:
                pending_effect = True

            observations.append(
                _observation(
                    logical_key=logical_key,
                    definition=definition,
                    record=record,
                    canonical_design_identities=(
                        recovered.task_packet.canonical_design_identities
                        if recovered.task_packet is not None
                        else ()
                    ),
                    unresolved_conflict=(
                        definition.issue_state == "CLOSED"
                        and record.lifecycle != "COMPLETED"
                    ),
                )
            )

        if len(current) > 1:
            raise WorkQueueUnavailable("MULTIPLE_CURRENT_WORKS")
        return V2WorkQueueSnapshot(
            works=tuple(sorted(observations, key=lambda item: item.issue_number)),
            current_work_identity=current[0] if current else None,
            pending_effect=pending_effect,
        )

    def _discover(
        self,
        registration: ProductDevelopmentRegistration,
    ) -> tuple[tuple[str, int], ...]:
        marker = re.compile(
            r"<!-- loop-engineering-work:"
            + re.escape(registration.product_key)
            + r":"
            + re.escape(registration.goal_revision)
            + r":([a-z0-9][a-z0-9-]{0,62}) -->"
        )
        try:
            raw = self._runner.run(
                (
                    "gh",
                    "issue",
                    "list",
                    "--repo",
                    registration.repository_identity,
                    "--state",
                    "all",
                    "--limit",
                    "1000",
                    "--json",
                    "number,body",
                )
            )
            payload = json.loads(raw)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
            raise WorkQueueUnavailable("WORK_DISCOVERY_FAILED") from error
        if not isinstance(payload, list):
            raise WorkQueueUnavailable("WORK_DISCOVERY_INVALID")
        found: dict[str, int] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            body = item.get("body")
            number = item.get("number")
            if not isinstance(body, str) or not isinstance(number, int):
                continue
            matches = marker.findall(body)
            if len(matches) > 1:
                raise WorkQueueUnavailable("WORK_MARKER_INVALID")
            if not matches:
                continue
            logical_key = matches[0]
            if logical_key in found:
                raise WorkQueueUnavailable("WORK_LOGICAL_IDENTITY_CONFLICT")
            found[logical_key] = number
        if not found:
            raise WorkQueueUnavailable("GOAL_WORKS_NOT_FOUND")
        return tuple(sorted(found.items(), key=lambda item: item[1]))


def _observation(
    *,
    logical_key: str,
    definition: WorkDefinitionSnapshot,
    record: WorkRecord,
    canonical_design_identities: tuple[str, ...],
    unresolved_conflict: bool,
) -> V2WorkObservation:
    del logical_key
    return V2WorkObservation(
        work_identity=record.identity,
        issue_number=record.issue_number,
        issue_revision=definition.revision,
        issue_state=definition.issue_state,
        lifecycle=record.lifecycle,
        project_status=definition.project_status,
        priority=definition.priority,
        dependency_states=definition.dependency_states,
        acceptance_digest=definition.acceptance_criteria_digest,
        canonical_design_identities=canonical_design_identities,
        active_lineage_identity=record.active_lineage_identity,
        latest_packet_identity=record.latest_task_packet_identity,
        latest_checkpoint_identity=record.latest_checkpoint_identity,
        verification_state=EvidenceState.NOT_RUN,
        review_state=EvidenceState.NOT_RUN,
        human_verification_state=EvidenceState.NOT_REQUIRED,
        unresolved_conflict=unresolved_conflict,
    )
