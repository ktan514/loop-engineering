from __future__ import annotations

from datetime import date

from loop_engineering import (
    CanonicalDesignSnapshot,
    ConflictKind,
    LineageClassification,
    LineageSnapshot,
    LoopEngineConfig,
    LoopHealthEvent,
    LoopHealthKind,
    MissionSnapshot,
    MissionSupervisor,
    ObservationEpoch,
    RunDisposition,
    SourceIdentity,
    WorkSnapshot,
    WriteIntent,
    decode_health_state,
    encode_health_state,
    plan_improvements,
)


def config() -> LoopEngineConfig:
    return LoopEngineConfig(
        repository="example/repo",
        owner="example",
        project_number=7,
        trunk_branch="main",
        authority_refs=("operations:#207", "mission:#450"),
    )


def source(kind: str, stable: str, revision: str) -> SourceIdentity:
    return SourceIdentity(kind, stable, revision)


def base_epoch(*, project_number: int = 7) -> ObservationEpoch:
    mission = MissionSnapshot(source("issue", "mission:450", "r1"), current_work_id=10)
    work = WorkSnapshot(
        identity=source("issue", "work:10", "r1"),
        issue_number=10,
        issue_open=True,
        project_status="In progress",
        priority="P0",
        dependencies_satisfied=True,
        canonical_design_resolved=True,
        actionable=True,
    )
    lineage = LineageSnapshot(
        identity=source("pr", "pr:10", "r1"),
        work_issue=10,
        classification=LineageClassification.CANONICAL,
        branch_ref="feature/work-10",
        base_ref="main",
        base_sha="base-a",
        head_sha="head-a",
    )
    design = CanonicalDesignSnapshot(
        identity=source("blob", "design:10", "blob-a"),
        path="docs/design.md",
        expected_blob_sha="blob-a",
        live_blob_sha="blob-a",
        authority_owner=10,
    )
    return ObservationEpoch(
        observation_id="obs-1",
        repository="example/repo",
        canonical_trunk_ref="main",
        canonical_trunk_sha="base-a",
        project_number=project_number,
        project_available=True,
        authorities_available=True,
        mission=mission,
        works=(work,),
        lineages=(lineage,),
        canonical_designs=(design,),
    )


def test_supervisor_selects_dependency_ready_work() -> None:
    decision = MissionSupervisor(config()).decide(base_epoch(), planning_date=date(2026, 8, 28))

    assert decision.disposition is RunDisposition.CONTINUE
    assert decision.selected_work_id == 10
    assert decision.resume_certificate.gate == "PASS"
    assert decision.task_packet is not None
    assert "operations:#207" in decision.task_packet.authority


def test_project_number_is_configuration_not_yura_constant() -> None:
    custom = LoopEngineConfig(
        repository="example/repo",
        owner="example",
        project_number=42,
    )
    decision = MissionSupervisor(custom).decide(base_epoch(project_number=42))

    assert decision.disposition is RunDisposition.CONTINUE


def test_wrong_project_fails_closed() -> None:
    decision = MissionSupervisor(config()).decide(base_epoch(project_number=6))

    assert decision.disposition is RunDisposition.INTERVENTION_REQUIRED
    assert ConflictKind.FORBIDDEN_PROJECT_IDENTITY in decision.resume_certificate.conflicts


def test_duplicate_schedule_yields_instead_of_busy_polling() -> None:
    supervisor = MissionSupervisor(config())
    first = supervisor.decide(base_epoch())
    assert first.task_packet is not None

    epoch = base_epoch()
    duplicate_epoch = ObservationEpoch(
        observation_id=epoch.observation_id,
        repository=epoch.repository,
        canonical_trunk_ref=epoch.canonical_trunk_ref,
        canonical_trunk_sha=epoch.canonical_trunk_sha,
        project_number=epoch.project_number,
        project_available=epoch.project_available,
        authorities_available=epoch.authorities_available,
        mission=epoch.mission,
        works=epoch.works,
        lineages=epoch.lineages,
        canonical_designs=epoch.canonical_designs,
        checkpoint_schedule_keys=(first.task_packet.schedule_key,),
    )
    second = supervisor.decide(duplicate_epoch)

    assert second.disposition is RunDisposition.YIELD_EXTERNAL
    assert second.duplicate_suppressed is True


def test_write_gate_uses_configured_project_and_trunk() -> None:
    supervisor = MissionSupervisor(config())
    project_intent = WriteIntent(
        intent_id="project-write",
        target_kind="project",
        target_identity="7",
        mutation_kind="edit",
        expected_preconditions=(("project_id", "P1"),),
        expected_effect=(),
        source_observation_id="obs-1",
    )
    trunk_intent = WriteIntent(
        intent_id="trunk-write",
        target_kind="branch",
        target_identity="main",
        mutation_kind="content",
        expected_preconditions=(
            ("branch_ref", "main"),
            ("pr_number", "22"),
            ("head_sha", "abc"),
        ),
        expected_effect=(),
        source_observation_id="obs-1",
    )

    assert supervisor.validate_write_gate(project_intent, {"project_id": "P1"}).allowed is True
    result = supervisor.validate_write_gate(
        trunk_intent,
        {"branch_ref": "main", "pr_number": "22", "head_sha": "abc"},
    )
    assert result.allowed is False
    assert result.conflict is ConflictKind.DIRECT_TRUNK_WRITE_FORBIDDEN


def test_health_state_round_trip_is_restart_safe() -> None:
    event = LoopHealthEvent(
        kind=LoopHealthKind.STALE_STATE_RECURRENCE,
        fingerprint="head mismatch",
        occurrence_count=2,
        affected_work_ids=(10,),
        source_refs=("provider text",),
    )

    encoded = encode_health_state((event,))
    decoded = decode_health_state(encoded)

    assert decoded[0].fingerprint.startswith("sha256:")
    assert decoded[0].source_refs[0].startswith("sha256:")
    assert encode_health_state(decoded) == encoded


def test_repeated_health_event_generates_bounded_improvement() -> None:
    event = LoopHealthEvent(
        kind=LoopHealthKind.DUPLICATE_SCHEDULING,
        fingerprint="same-schedule-key",
        occurrence_count=2,
        affected_work_ids=(10,),
        source_refs=("scheduler",),
    )

    candidates = plan_improvements(
        (event,),
        existing_issues=(),
        checkpoint_keys=(),
        planning_date=date(2026, 8, 28),
    )

    assert len(candidates) == 1
    assert candidates[0].start_date == "2026-08-28"
    assert candidates[0].target_date
