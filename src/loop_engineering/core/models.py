"""Immutable provider-independent Core state models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .evidence import Evidence
from .identities import ExecutionTarget, SourceIdentity


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_optional_text(value: str | None, field_name: str) -> None:
    if value is not None:
        _require_text(value, field_name)


def _require_text_tuple(values: tuple[str, ...], field_name: str) -> None:
    for value in values:
        _require_text(value, field_name)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class RunGoalKind(str, Enum):
    SINGLE_WORK = "SINGLE_WORK"
    PROJECT_QUEUE = "PROJECT_QUEUE"
    MILESTONE = "MILESTONE"
    MIGRATION = "MIGRATION"
    MISSION = "MISSION"


@dataclass(frozen=True, slots=True)
class RunGoal:
    goal_id: str
    kind: RunGoalKind
    authority_refs: tuple[SourceIdentity, ...]
    completion_policy: str
    scope: str

    def __post_init__(self) -> None:
        _require_text(self.goal_id, "goal_id")
        _require_text(self.completion_policy, "completion_policy")
        _require_text(self.scope, "scope")


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_id: str
    source_identity: SourceIdentity
    work_type: str
    status: str
    priority: str | None = None
    dependencies: tuple[str, ...] = ()
    canonical_design_refs: tuple[SourceIdentity, ...] = ()
    verification_policy: str | None = None
    lineage_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.work_id, "work_id")
        _require_text(self.work_type, "work_type")
        _require_text(self.status, "status")
        _require_optional_text(self.priority, "priority")
        _require_optional_text(self.verification_policy, "verification_policy")
        _require_text_tuple(self.dependencies, "dependencies")
        _require_text_tuple(self.lineage_refs, "lineage_refs")


class LineageClassification(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    VALIDATION_ONLY = "VALIDATION_ONLY"
    CI_ONLY = "CI_ONLY"
    ABANDONED = "ABANDONED"
    MERGED = "MERGED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Lineage:
    lineage_id: str
    work_id: str
    classification: LineageClassification
    repository_identity: SourceIdentity
    branch_identity: SourceIdentity | None = None
    pr_identity: SourceIdentity | None = None
    base_identity: SourceIdentity | None = None
    head_identity: SourceIdentity | None = None
    created_from: SourceIdentity | None = None
    supersession: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.lineage_id, "lineage_id")
        _require_text(self.work_id, "work_id")
        _require_optional_text(self.supersession, "supersession")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    goal_id: str
    last_observation_id: str
    created_at: datetime
    work_id: str | None = None
    lineage_id: str | None = None
    expected_target_identity: ExecutionTarget | None = None
    last_confirmed_effects: tuple[str, ...] = ()
    last_verification: tuple[Evidence, ...] = ()
    pending_external: tuple[SourceIdentity, ...] = ()
    next_expected_transition: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.checkpoint_id, "checkpoint_id")
        _require_text(self.goal_id, "goal_id")
        _require_text(self.last_observation_id, "last_observation_id")
        _require_optional_text(self.work_id, "work_id")
        _require_optional_text(self.lineage_id, "lineage_id")
        _require_optional_text(self.next_expected_transition, "next_expected_transition")
        _require_text_tuple(self.last_confirmed_effects, "last_confirmed_effects")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ObservationEpoch:
    observation_id: str
    observed_at: datetime
    run_goal_snapshot: RunGoal
    project_profile_snapshot: SourceIdentity | None = None
    source_control_snapshots: tuple[SourceIdentity, ...] = ()
    planning_snapshots: tuple[SourceIdentity, ...] = ()
    work_snapshots: tuple[WorkItem, ...] = ()
    lineage_snapshots: tuple[Lineage, ...] = ()
    ci_snapshots: tuple[Evidence, ...] = ()
    review_snapshots: tuple[Evidence, ...] = ()
    verification_snapshots: tuple[Evidence, ...] = ()
    canonical_design_snapshots: tuple[SourceIdentity, ...] = ()
    runtime_snapshots: tuple[SourceIdentity, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.observation_id, "observation_id")
        _require_text_tuple(self.diagnostics, "diagnostics")
        _require_aware(self.observed_at, "observed_at")
