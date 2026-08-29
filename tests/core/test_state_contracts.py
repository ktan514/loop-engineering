from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from loop_engineering.core import (
    CanonicalGeneration,
    Conflict,
    ConflictKind,
    ConflictScope,
    ConflictSeverity,
    Evidence,
    ExecutionTarget,
    Lineage,
    LineageClassification,
    ObservationEpoch,
    RunGoal,
    RunGoalKind,
    SourceIdentity,
    WorkItem,
    canonical_digest,
    canonical_json,
)

T0 = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=5)


def source(
    *,
    object_kind: str = "commit",
    stable_id: str = "repo:example/ref:main",
    revision_id: str = "sha-a",
    observed_at: datetime = T0,
) -> SourceIdentity:
    return SourceIdentity(
        provider_kind="scm",
        object_kind=object_kind,
        stable_id=stable_id,
        revision_id=revision_id,
        observed_at=observed_at,
    )


def test_source_identity_reobservation_preserves_exact_identity() -> None:
    first = source(observed_at=T0)
    later = source(observed_at=T1)

    assert first == later
    assert hash(first) == hash(later)
    assert first.identity_digest == later.identity_digest
    assert canonical_json(first) != canonical_json(later)


def test_source_identity_rejects_blank_and_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="stable_id"):
        source(stable_id="   ")

    with pytest.raises(ValueError, match="タイムゾーン付き日時"):
        source(observed_at=datetime(2026, 8, 28, 10, 0))


def test_core_contracts_are_frozen() -> None:
    identity = source()
    field_name = "revision_id"

    with pytest.raises(FrozenInstanceError):
        setattr(identity, field_name, "sha-b")


def test_execution_target_digest_uses_identity_not_observation_time() -> None:
    first = ExecutionTarget(
        repository_identity=source(observed_at=T0),
        head_identity=source(observed_at=T0),
    )
    later = ExecutionTarget(
        repository_identity=source(observed_at=T1),
        head_identity=source(observed_at=T1),
    )
    changed = ExecutionTarget(
        repository_identity=source(observed_at=T1),
        head_identity=source(revision_id="sha-b", observed_at=T1),
    )

    assert first.identity_digest == later.identity_digest
    assert first.identity_digest != changed.identity_digest


def test_canonical_generation_is_order_and_observation_time_independent() -> None:
    design_a_early = source(
        object_kind="blob",
        stable_id="design:a",
        revision_id="blob-a",
        observed_at=T0,
    )
    design_a_late = source(
        object_kind="blob",
        stable_id="design:a",
        revision_id="blob-a",
        observed_at=T1,
    )
    design_b = source(
        object_kind="blob",
        stable_id="design:b",
        revision_id="blob-b",
        observed_at=T0,
    )

    first = CanonicalGeneration.from_refs((design_a_late, design_b, design_a_early))
    second = CanonicalGeneration.from_refs((design_b, design_a_late))

    assert first == second
    assert first.normalized_digest == second.normalized_digest
    assert len(first.refs) == 2
    assert first.refs[0].observed_at == T0


def test_canonical_generation_rejects_empty_refs() -> None:
    with pytest.raises(ValueError, match="1件以上"):
        CanonicalGeneration.from_refs(())


def test_conflict_and_evidence_are_provider_neutral_values() -> None:
    repository = source(
        object_kind="repository",
        stable_id="repo:example",
        revision_id="repo-rev",
    )
    head = source()
    target = ExecutionTarget(repository_identity=repository, head_identity=head)
    evidence_source = source(
        object_kind="check",
        stable_id="ci:1",
        revision_id="run:1",
    )
    evidence = Evidence(
        evidence_id="evidence:1",
        kind="CI",
        target_identity=target,
        source_identity=evidence_source,
        result="PASS",
        observed_at=T0,
    )
    conflict = Conflict(
        kind=ConflictKind.HEAD_IDENTITY_MISMATCH,
        scope=ConflictScope.TRANSITION,
        severity=ConflictSeverity.BLOCKING,
        subject_identity=head,
        evidence_refs=(evidence_source,),
        resolution_policy="fresh-observe",
    )

    assert evidence.target_identity.head_identity == head
    assert conflict.kind is ConflictKind.HEAD_IDENTITY_MISMATCH


def test_observation_epoch_nested_serialization_is_deterministic() -> None:
    repository = source(
        object_kind="repository",
        stable_id="repo:example",
        revision_id="repo-rev",
    )
    issue = source(
        object_kind="work",
        stable_id="work:10",
        revision_id="issue-rev",
    )
    canonical_design = source(
        object_kind="blob",
        stable_id="design:core",
        revision_id="blob-1",
    )
    branch = source(
        object_kind="branch",
        stable_id="branch:core-state",
        revision_id="sha-a",
    )
    goal = RunGoal(
        goal_id="goal:phase-1",
        kind=RunGoalKind.PROJECT_QUEUE,
        authority_refs=(repository,),
        completion_policy="all-selected-work-complete",
        scope="project",
    )
    work = WorkItem(
        work_id="10",
        source_identity=issue,
        work_type="implementation",
        status="in-progress",
        priority="P0",
        canonical_design_refs=(canonical_design,),
    )
    lineage = Lineage(
        lineage_id="lineage:10",
        work_id="10",
        classification=LineageClassification.ACTIVE,
        repository_identity=repository,
        branch_identity=branch,
        head_identity=source(),
    )
    epoch = ObservationEpoch(
        observation_id="obs:1",
        observed_at=T0,
        run_goal_snapshot=goal,
        source_control_snapshots=(repository,),
        work_snapshots=(work,),
        lineage_snapshots=(lineage,),
        canonical_design_snapshots=work.canonical_design_refs,
        diagnostics=("fresh",),
    )

    first_json = canonical_json(epoch)
    second_json = canonical_json(epoch)

    assert first_json == second_json
    assert canonical_digest(epoch) == canonical_digest(epoch)
    assert '"observation_id":"obs:1"' in first_json


def test_canonical_serialization_rejects_unordered_or_unknown_values() -> None:
    with pytest.raises(TypeError, match="順序を持たないcollection"):
        canonical_json({"a", "b"})

    with pytest.raises(TypeError, match="未対応の正規直列化型"):
        canonical_json(object())


def test_invalid_model_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="work_id"):
        WorkItem(
            work_id=" ",
            source_identity=source(
                object_kind="work",
                stable_id="work:blank",
                revision_id="1",
            ),
            work_type="implementation",
            status="ready",
        )
