from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from loop_engineering.v2_autonomous_runner import (
    TransitionExecutionStatus,
)
from loop_engineering.v2_autonomous_transitions import (
    V2AutonomousTransitionExecutor,
)
from loop_engineering.v2_development_lineage import (
    GitHubDevelopmentLineageEffects,
    LineageIdentity,
    LineageResult,
    LineageStatus,
    MaterializedProposal,
    PullRequestIdentity,
    TrustedProposalMaterializer,
)
from loop_engineering.v2_external_review import (
    ExternalReviewCoordinator,
    ExternalReviewState,
)
from loop_engineering.v2_goal_planning import (
    BootstrapResult,
    PlannedWork,
    ProductDevelopmentRegistration,
    ProjectedPlan,
    ProjectedWork,
    WorkPlanProposal,
)
from loop_engineering.v2_implementer import (
    DevelopmentTaskPacket,
    ImplementerResult,
    ImplementerStatus,
    V2ImplementerPort,
    WorkspaceEffectReport,
)
from loop_engineering.v2_local_quality import (
    LocalQualityCoordinator,
    LocalQualityState,
)
from loop_engineering.v2_supervisor import (
    V2SupervisorDecision,
    V2SupervisorDisposition,
    V2Transition,
    V2WorkObservation,
)
from loop_engineering.work_state import (
    EffectAttempt,
    RecoveredWork,
    WorkCheckpoint,
    WorkRecord,
    WorkTaskPacket,
)


class Result:
    def __init__(self, returncode: int, output: str = "") -> None:
        self.returncode = returncode
        self.output = output

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class SubprocessRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> Result:
        completed = subprocess.run(
            tuple(command),
            cwd=cwd,
            env=dict(environment) if environment else None,
            check=False,
            capture_output=capture_output,
            text=True,
            timeout=timeout_seconds,
        )
        return Result(completed.returncode, completed.stdout or "")


class DesignImplementer(V2ImplementerPort):
    def execute(self, packet: DevelopmentTaskPacket) -> ImplementerResult:
        assert packet.transition.value == "DESIGN"
        target = packet.workspace_canonical_path / "docs" / "design.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "# Design\n\nslugifyは英数字以外の連続部分を単一hyphenへ正規化する。\n",
            encoding="utf-8",
        )
        return ImplementerResult(
            ImplementerStatus.SUCCESS,
            "DESIGN_READY",
            workspace_effect=WorkspaceEffectReport(
                request_identity="request:design",
                packet_identity=packet.packet_identity,
                work_identity=packet.work_identity,
                transition=packet.transition,
                input_target_identity=packet.exact_base_sha,
                result_target_identity=packet.exact_base_sha,
                change_identity="sha256:" + "1" * 64,
                changed_paths=("docs/design.md",),
                verification_evidence=(),
            ),
        )


class FakeLineage:
    def publish(
        self,
        lineage: LineageIdentity,
        materialized: MaterializedProposal,
    ) -> LineageResult:
        return LineageResult(
            LineageStatus.CONFIRMED,
            "LINEAGE_PUBLISHED",
            materialized.candidate_sha,
            PullRequestIdentity(
                7,
                "https://example.test/pr/7",
                materialized.candidate_sha,
                lineage.branch_name,
                lineage.base_branch,
                True,
            ),
        )


class MemoryWorkState:
    def __init__(self, record: WorkRecord) -> None:
        self.record = record
        self.packet: WorkTaskPacket | None = None
        self.checkpoint: WorkCheckpoint | None = None

    def recover(self, work_identity: str) -> RecoveredWork | None:
        assert work_identity == self.record.identity
        return RecoveredWork(self.record, self.packet, self.checkpoint, ())

    def upsert_work(self, record: WorkRecord) -> None:
        self.record = record

    def record_checkpoint(self, checkpoint: WorkCheckpoint) -> None:
        self.checkpoint = checkpoint
        self.record = replace(
            self.record,
            latest_checkpoint_identity=checkpoint.identity,
        )

    def record_task_packet(self, packet: WorkTaskPacket) -> None:
        self.packet = packet
        self.record = replace(
            self.record,
            latest_task_packet_identity=packet.identity,
        )

    def record_effect_intent(self, attempt: EffectAttempt) -> bool:
        del attempt
        return True

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None:
        del idempotency_key, status


class EmptyLocalState:
    def get(self, work_identity: str) -> LocalQualityState | None:
        del work_identity
        return None


class EmptyExternalState:
    def get(self, work_identity: str) -> ExternalReviewState | None:
        del work_identity
        return None


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_design_publish_persists_canonical_identity_for_next_implement(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "product"
    workspace.mkdir()
    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.name", "Loop E2E")
    _git(workspace, "config", "user.email", "loop-e2e@example.test")
    (workspace / "README.md").write_text("# product\n", encoding="utf-8")
    _git(workspace, "add", "README.md")
    _git(workspace, "commit", "-m", "initial")
    base = _git(workspace, "rev-parse", "HEAD")

    registration = ProductDevelopmentRegistration(
        product_key="sample",
        workspace_canonical_path=workspace,
        repository_identity="owner/product",
        project_owner="owner",
        project_number=10,
        trunk_branch="main",
        goal_definition_identity="goal:1",
        goal_revision="rev-1",
        goal_text="slugifyを完成する",
        acceptance_criteria=("tests pass",),
        work_branch_template="loop/work-{issue}",
        ci_workflow_name="CI",
        initial_project_status="Backlog",
    )
    planned = PlannedWork(
        logical_key="goal-implementation",
        title="slugify",
        purpose="slugifyを完成する",
        acceptance_criteria=("tests pass",),
        canonical_design_targets=("docs/design.md",),
    )
    proposal = WorkPlanProposal(
        "proposal:1",
        "rev-1",
        (planned,),
        ("tests pass",),
    )
    bootstrap = BootstrapResult(
        proposal,
        ProjectedPlan(
            1,
            "https://example.test/issues/1",
            "goal-item",
            (
                ProjectedWork(
                    "goal-implementation",
                    2,
                    "https://example.test/issues/2",
                    "work-item",
                    "digest",
                    (),
                ),
            ),
        ),
    )
    work = V2WorkObservation(
        work_identity="work:owner/product:2",
        issue_number=2,
        issue_revision="issue-rev",
        issue_state="OPEN",
        lifecycle="PLANNED",
        project_status="Backlog",
        priority="P1",
        dependency_states=(),
        acceptance_digest="digest",
        exact_head_sha=base,
    )
    decision = V2SupervisorDecision(
        V2SupervisorDisposition.CONTINUE,
        work.work_identity,
        V2Transition.DESIGN,
        "schedule:design",
        "ACTIONABLE_WORK_SELECTED",
    )
    state = MemoryWorkState(
        WorkRecord(
            work.work_identity,
            registration.repository_identity,
            2,
            work.issue_revision,
            "PLANNED",
        )
    )
    runner = SubprocessRunner()
    executor = V2AutonomousTransitionExecutor(
        implementer=DesignImplementer(),
        materializer=TrustedProposalMaterializer(runner, {}),
        lineage=cast(GitHubDevelopmentLineageEffects, FakeLineage()),
        work_state=state,
        runner=runner,
        environment={},
        local_quality=cast(LocalQualityCoordinator, object()),
        local_quality_state=EmptyLocalState(),
        external_review=cast(ExternalReviewCoordinator, object()),
        external_review_state=EmptyExternalState(),
        review_levels=(),
        verification_commands=(),
        scope_paths=(".",),
    )

    result = executor.execute(
        registration,
        bootstrap,
        work,
        planned,
        decision,
    )

    assert result.status is TransitionExecutionStatus.PROGRESSED
    assert state.packet is not None
    assert state.packet.transition == "DESIGN"
    assert state.packet.status == "COMPLETED"
    assert len(state.packet.canonical_design_identities) == 1
    assert state.packet.canonical_design_identities[0].startswith("design:")
    expected = hashlib.sha256(
        (
            "docs/design.md"
            + "\0"
            + (workspace / "docs" / "design.md").read_text(encoding="utf-8")
        ).encode("utf-8")
    ).hexdigest()
    assert state.packet.canonical_design_identities == ("design:" + expected,)
    assert state.record.latest_task_packet_identity == "schedule:design"
    assert state.checkpoint is not None
    assert state.checkpoint.task_packet_identity == "schedule:design"
