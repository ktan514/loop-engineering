from loop_engineering.config import LoopEngineConfig
from loop_engineering.models import (
    CanonicalDesignSnapshot,
    LineageClassification,
    LineageSnapshot,
    MissionSnapshot,
    ObservationEpoch,
    SourceIdentity,
    WorkSnapshot,
)


def config() -> LoopEngineConfig:
    return LoopEngineConfig(
        repository="ktan514/ai-liver-yura",
        owner="ktan514",
        project_number=7,
        mission_issue=450,
        label="loop-engineering",
        trunk_branch="rebuild/v2-foundation",
        authority_refs=("#207", "#317", "#450", "#462"),
        improvement_area="Subsystem/Development Tooling",
        issue_level="Work",
        root_issue=317,
        parent_issue=462,
        integration_work=471,
        ci_workflow_name="V2 Deterministic CI",
    )


def identity(kind: str, stable_id: str) -> SourceIdentity:
    return SourceIdentity(kind, stable_id, "rev-1")


def work(
    number: int = 465,
    *,
    priority: str = "P0",
    actionable: bool = True,
    wait_only: bool = False,
    wait_reason: str | None = None,
    status: str = "In progress",
) -> WorkSnapshot:
    return WorkSnapshot(
        identity("issue", str(number)),
        number,
        True,
        status,
        priority,
        True,
        True,
        actionable,
        wait_only,
        wait_reason,
    )


def lineage(
    *,
    classification: LineageClassification = LineageClassification.CANONICAL,
    head: str = "head-1",
) -> LineageSnapshot:
    return LineageSnapshot(
        identity("branch", "feature/supervisor"),
        465,
        classification,
        "feature/supervisor",
        "rebuild/v2-foundation",
        "base-1",
        head,
        "base-1",
        head,
        head,
        head,
    )


def epoch(
    *,
    observation_id: str = "epoch-1",
    project_number: int = 7,
    project_available: bool = True,
    mission: MissionSnapshot | None = None,
    works: tuple[WorkSnapshot, ...] | None = None,
    lineages: tuple[LineageSnapshot, ...] | None = None,
    canonical_designs: tuple[CanonicalDesignSnapshot, ...] | None = None,
    checkpoint_schedule_keys: tuple[str, ...] = (),
) -> ObservationEpoch:
    return ObservationEpoch(
        observation_id,
        "ktan514/ai-liver-yura",
        "rebuild/v2-foundation",
        "base-1",
        project_number,
        project_available,
        True,
        mission or MissionSnapshot(identity("issue", "450"), 465),
        works if works is not None else (work(),),
        lineages if lineages is not None else (lineage(),),
        canonical_designs
        if canonical_designs is not None
        else (
            CanonicalDesignSnapshot(
                identity("blob", "design"), "design.md", "blob-1", "blob-1", 465
            ),
        ),
        checkpoint_schedule_keys,
    )
