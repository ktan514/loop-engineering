"""Supervisor transitionを#85/#86と安全なIntegration/Completion effectへ接続する。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .config import ReviewLevelConfig
from .v2_autonomous_runner import TransitionExecutionResult, TransitionExecutionStatus
from .v2_development_lineage import (
    GitHubDevelopmentLineageEffects,
    LineageIdentity,
    LineageResult,
    LineageStatus,
    MaterializedProposal,
    TrustedProposalMaterializer,
)
from .v2_external_review import (
    ExternalReviewCoordinator,
    ExternalReviewState,
    ExternalReviewStatus,
    ExternalReviewTarget,
)
from .v2_goal_planning import BootstrapResult, PlannedWork, ProductDevelopmentRegistration
from .v2_implementer import (
    DevelopmentTaskPacket,
    ImplementerFinding,
    ImplementerStatus,
    ImplementerTransition,
    V2ImplementerPort,
    WorkspaceEffectReport,
)
from .v2_local_quality import (
    LocalQualityContext,
    LocalQualityCoordinator,
    LocalQualityStage,
    LocalQualityState,
    LocalQualityStatus,
    LocalQualityTarget,
    VerificationCommandDescriptor,
    clean_workspace_change_identity,
)
from .v2_supervisor import EvidenceState, V2SupervisorDecision, V2Transition, V2WorkObservation
from .work_state import EffectAttempt, RecoveredWork, WorkCheckpoint, WorkRecord

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_PR_RE = re.compile(r"pr:(\d+)")


class CommandResultLike(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def output(self) -> str: ...

    @property
    def succeeded(self) -> bool: ...


class AutonomousTransitionCommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> CommandResultLike: ...


class ExternalReviewStatePort(Protocol):
    def get(self, work_identity: str) -> ExternalReviewState | None: ...


class LocalQualityStatePort(Protocol):
    def get(self, work_identity: str) -> LocalQualityState | None: ...


class AutonomousWorkStatePort(Protocol):
    def recover(self, work_identity: str) -> RecoveredWork | None: ...

    def upsert_work(self, record: WorkRecord) -> None: ...

    def record_checkpoint(self, checkpoint: WorkCheckpoint) -> None: ...

    def record_effect_intent(self, attempt: EffectAttempt) -> bool: ...

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None: ...


@dataclass(slots=True)
class V2AutonomousTransitionExecutor:
    implementer: V2ImplementerPort
    materializer: TrustedProposalMaterializer
    lineage: GitHubDevelopmentLineageEffects
    work_state: AutonomousWorkStatePort
    runner: AutonomousTransitionCommandRunner
    environment: Mapping[str, str]
    local_quality: LocalQualityCoordinator
    local_quality_state: LocalQualityStatePort
    external_review: ExternalReviewCoordinator
    external_review_state: ExternalReviewStatePort
    review_levels: tuple[ReviewLevelConfig, ...]
    verification_commands: tuple[VerificationCommandDescriptor, ...]
    scope_paths: tuple[str, ...] = (".",)
    done_project_status: str = "Done"

    def execute(
        self,
        registration: ProductDevelopmentRegistration,
        bootstrap: BootstrapResult,
        work: V2WorkObservation,
        planned_work: PlannedWork,
        decision: V2SupervisorDecision,
    ) -> TransitionExecutionResult:
        transition = decision.transition
        if transition is None or decision.schedule_key is None:
            return _intervention("TRANSITION_DECISION_INVALID")
        if transition in {V2Transition.DESIGN, V2Transition.IMPLEMENT, V2Transition.REPAIR}:
            return self._develop(registration, work, planned_work, decision)
        if transition is V2Transition.VERIFY:
            return self._verify_local(registration, work, planned_work, decision)
        if transition is V2Transition.REVIEW:
            return self._review_external(registration, work, planned_work, decision)
        if transition is V2Transition.HUMAN_VERIFY:
            return _waiting("HUMAN_VERIFY_EVIDENCE_PENDING")
        if transition is V2Transition.INTEGRATE:
            return self._integrate(registration, work, decision.schedule_key)
        if transition is V2Transition.COMPLETE_WORK:
            return self._complete_work(registration, bootstrap, work, decision.schedule_key)
        return _intervention("TRANSITION_UNSUPPORTED")

    def _develop(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        planned: PlannedWork,
        decision: V2SupervisorDecision,
    ) -> TransitionExecutionResult:
        assert decision.transition is not None and decision.schedule_key is not None
        branch = _work_branch(registration.work_branch_template, work.issue_number)
        exact_base = work.exact_head_sha or self._trunk_head(registration)
        if exact_base is None:
            return _waiting("DEVELOPMENT_BASE_UNAVAILABLE")
        remote_ref = branch if work.exact_head_sha is not None else registration.trunk_branch
        if not self._ensure_commit_available(registration, exact_base, remote_ref):
            return _waiting("DEVELOPMENT_BASE_FETCH_UNPROVEN")
        if not self._prepare_workspace(registration, branch, exact_base):
            return _intervention("DEVELOPMENT_WORKSPACE_PREPARE_FAILED")

        approved_findings: tuple[ImplementerFinding, ...] = ()
        expected_change_identity: str | None = None
        if decision.transition is V2Transition.REPAIR:
            expected_change_identity = clean_workspace_change_identity(exact_base, branch)
            if work.ci_state is EvidenceState.FAIL:
                ci_findings = self._ci_repair_findings(registration, work)
                if not ci_findings:
                    return _intervention("CI_REPAIR_FINDINGS_UNAVAILABLE")
                approved_findings = ci_findings
            else:
                external = self.external_review_state.get(work.work_identity)
                if (
                    external is None
                    or external.status != "REQUEST_CHANGES"
                    or external.target_head_sha != exact_base
                    or external.change_identity != expected_change_identity
                    or not external.approved_findings
                ):
                    return _intervention("EXTERNAL_REPAIR_FINDINGS_UNAVAILABLE")
                approved_findings = external.approved_findings

        generation = _generation(decision.schedule_key)
        packet = DevelopmentTaskPacket(
            packet_identity=decision.schedule_key,
            work_identity=work.work_identity,
            generation=generation,
            transition=ImplementerTransition(decision.transition.value),
            repository_identity=registration.repository_identity,
            workspace_canonical_path=registration.workspace_canonical_path,
            exact_base_sha=exact_base,
            goal_revision=registration.goal_revision,
            issue_revision=work.issue_revision,
            scope_paths=self.scope_paths,
            acceptance_checks=planned.acceptance_criteria,
            canonical_design_identities=work.canonical_design_identities,
            canonical_design_targets=planned.canonical_design_targets,
            active_lineage_identity=work.active_lineage_identity,
            authority_refs=(
                registration.goal_definition_identity,
                f"Issue #{work.issue_number}",
            ),
            safety_constraints=(
                "main/trunkへ直接commit/pushしない",
                "GitHub mutationはTrusted Hostへ委譲する",
            ),
            expected_change_identity=expected_change_identity,
            approved_findings=approved_findings,
        )
        implemented = self.implementer.execute(packet)
        if implemented.status is ImplementerStatus.INCOMPLETE:
            return _waiting(implemented.detail)
        if implemented.status is ImplementerStatus.BLOCKED:
            return _intervention(implemented.detail)
        if implemented.status is not ImplementerStatus.SUCCESS:
            return _failed(implemented.detail)

        materialized: MaterializedProposal | None = None
        if implemented.proposal is not None:
            materialized = self.materializer.materialize(
                workspace=registration.workspace_canonical_path,
                repository=registration.repository_identity,
                proposal=implemented.proposal,
                commit_message=_commit_message(decision.transition, work.issue_number),
            )
        elif implemented.workspace_effect is not None:
            materialized = self._materialize_workspace_effect(
                registration,
                work,
                branch,
                exact_base,
                implemented.workspace_effect,
                _commit_message(decision.transition, work.issue_number),
            )
        if materialized is None:
            return _failed("DEVELOPMENT_MATERIALIZATION_FAILED")

        published = self._publish_materialized(
            registration,
            work,
            branch,
            generation,
            materialized,
        )
        if isinstance(published, TransitionExecutionResult):
            return published

        self._advance_work(
            work,
            lifecycle="RUNNING",
            selected_transition=decision.transition.value,
            active_lineage_identity=f"pr:{published.pull_request.number}",
            next_action=(
                "IMPLEMENT_SAME_LINEAGE"
                if decision.transition is V2Transition.DESIGN
                else "VERIFY_LOCAL"
            ),
            evidence=(f"head:{published.pull_request.head_sha}",),
            schedule_key=decision.schedule_key,
        )
        return _progressed(published.detail)

    def _verify_local(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        planned: PlannedWork,
        decision: V2SupervisorDecision,
    ) -> TransitionExecutionResult:
        if work.exact_head_sha is None or decision.schedule_key is None:
            return _intervention("LOCAL_QUALITY_TARGET_MISSING")
        branch = _work_branch(registration.work_branch_template, work.issue_number)
        if not self._prepare_workspace(registration, branch, work.exact_head_sha):
            return _intervention("LOCAL_QUALITY_WORKSPACE_PREPARE_FAILED")
        change_identity = clean_workspace_change_identity(work.exact_head_sha, branch)
        target = LocalQualityTarget(
            repository_identity=registration.repository_identity,
            work_identity=work.work_identity,
            exact_head_sha=work.exact_head_sha,
            change_identity=change_identity,
            active_lineage_identity=work.active_lineage_identity or "",
            canonical_design_identities=work.canonical_design_identities,
            acceptance_digest=work.acceptance_digest or "",
            scope_paths=self.scope_paths,
        )
        context = LocalQualityContext(
            target=target,
            workspace_canonical_path=registration.workspace_canonical_path,
            packet_identity=decision.schedule_key,
            generation=_generation(decision.schedule_key),
            goal_revision=registration.goal_revision,
            issue_revision=work.issue_revision,
            acceptance_checks=planned.acceptance_criteria,
            authority_refs=(
                registration.goal_definition_identity,
                f"Issue #{work.issue_number}",
            ),
            non_goals=(),
            safety_constraints=(
                "main/trunkへ直接commit/pushしない",
                "reviewerはread-only",
            ),
            verification_commands=self.verification_commands,
        )
        result = self.local_quality.run(context)
        if result.status is LocalQualityStatus.INCOMPLETE:
            return _waiting(result.detail)
        if result.status is LocalQualityStatus.BLOCKED:
            return _intervention(result.detail)
        if result.local_pass_identity is None:
            return _intervention("LOCAL_PASS_IDENTITY_MISSING")

        current_head = self._git_output(
            registration.workspace_canonical_path,
            ("rev-parse", "HEAD"),
        )
        dirty = self._workspace_changed_paths(registration.workspace_canonical_path)
        if current_head is None or dirty is None:
            return _intervention("LOCAL_QUALITY_READBACK_FAILED")

        if current_head != work.exact_head_sha or dirty:
            materialized = self._materialize_current_workspace(
                registration,
                work,
                branch,
                work.exact_head_sha,
                "fix: #"
                + str(work.issue_number)
                + " のLocal Quality指摘を反映する",
            )
            if materialized is None:
                return _intervention("LOCAL_REPAIR_MATERIALIZATION_FAILED")
            published = self._publish_materialized(
                registration,
                work,
                branch,
                _generation(decision.schedule_key),
                materialized,
            )
            if isinstance(published, TransitionExecutionResult):
                return published
            self._advance_work(
                work,
                lifecycle="RUNNING",
                selected_transition=V2Transition.REPAIR.value,
                active_lineage_identity=f"pr:{published.pull_request.number}",
                next_action="VERIFY_LOCAL",
                evidence=(f"head:{published.pull_request.head_sha}",),
                schedule_key=decision.schedule_key,
            )
            return _progressed("LOCAL_REPAIR_PUBLISHED")

        self._advance_work(
            work,
            lifecycle="RUNNING",
            selected_transition=V2Transition.VERIFY.value,
            active_lineage_identity=work.active_lineage_identity,
            next_action="EXTERNAL_REVIEW",
            evidence=(result.local_pass_identity,),
            schedule_key=decision.schedule_key,
        )
        return _progressed("LOCAL_PASS_CONFIRMED")

    def _review_external(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        planned: PlannedWork,
        decision: V2SupervisorDecision,
    ) -> TransitionExecutionResult:
        if work.exact_head_sha is None or decision.schedule_key is None:
            return _intervention("EXTERNAL_REVIEW_TARGET_MISSING")
        pr_number = _pr_number(work.active_lineage_identity)
        if pr_number is None:
            return _intervention("EXTERNAL_REVIEW_PR_MISSING")
        branch = _work_branch(registration.work_branch_template, work.issue_number)
        if not self._prepare_workspace(registration, branch, work.exact_head_sha):
            return _intervention("EXTERNAL_REVIEW_WORKSPACE_PREPARE_FAILED")
        change_identity = clean_workspace_change_identity(work.exact_head_sha, branch)
        local = self.local_quality_state.get(work.work_identity)
        if (
            local is None
            or local.stage is not LocalQualityStage.LOCAL_PASS
            or local.target_head_sha != work.exact_head_sha
            or local.change_identity != change_identity
            or local.local_pass_identity is None
        ):
            return _intervention("EXTERNAL_REVIEW_LOCAL_PASS_STALE")
        canonical_context = self._canonical_context(
            registration.workspace_canonical_path,
            planned.canonical_design_targets,
        )
        if canonical_context is None:
            return _intervention("EXTERNAL_REVIEW_CANONICAL_CONTEXT_UNAVAILABLE")
        target = ExternalReviewTarget(
            repository_identity=registration.repository_identity,
            work_identity=work.work_identity,
            issue_number=work.issue_number,
            pr_number=pr_number,
            exact_head_sha=work.exact_head_sha,
            change_identity=change_identity,
            active_lineage_identity=work.active_lineage_identity or "",
            canonical_design_identities=work.canonical_design_identities,
            acceptance_digest=work.acceptance_digest or "",
            scope_paths=self.scope_paths,
            local_pass_identity=local.local_pass_identity,
            acceptance_checks=planned.acceptance_criteria,
            canonical_context=canonical_context,
            verification_evidence=(
                local.verification_identity or local.local_pass_identity,
            ),
            non_goals=(),
        )
        result = self.external_review.run(target, self.review_levels)
        if result.status is ExternalReviewStatus.WAITING:
            return _waiting(result.detail)
        if result.status is ExternalReviewStatus.REQUEST_CHANGES:
            self._advance_work(
                work,
                lifecycle="RUNNING",
                selected_transition=V2Transition.REVIEW.value,
                active_lineage_identity=work.active_lineage_identity,
                next_action="REPAIR_EXTERNAL_FINDINGS",
                evidence=(
                    f"external-review-level:{result.level}:pass:{result.pass_index}",
                ),
                schedule_key=decision.schedule_key,
            )
            return _progressed("EXTERNAL_REVIEW_REQUEST_CHANGES")
        if result.status in {
            ExternalReviewStatus.ESCALATE,
            ExternalReviewStatus.BLOCKED,
        }:
            return _intervention(result.detail)
        if result.external_pass_identity is None:
            return _intervention("EXTERNAL_PASS_IDENTITY_MISSING")
        self._advance_work(
            work,
            lifecycle="RUNNING",
            selected_transition=V2Transition.REVIEW.value,
            active_lineage_identity=work.active_lineage_identity,
            next_action="INTEGRATE",
            evidence=(result.external_pass_identity,),
            schedule_key=decision.schedule_key,
        )
        return _progressed("EXTERNAL_PASS_CONFIRMED")


    def _ci_repair_findings(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
    ) -> tuple[ImplementerFinding, ...]:
        if work.ci_identity is None:
            return ()
        pr_number = _pr_number(work.active_lineage_identity)
        if pr_number is None:
            return ()
        changed = self._run(
            (
                "gh",
                "pr",
                "diff",
                str(pr_number),
                "--repo",
                registration.repository_identity,
                "--name-only",
            ),
            registration.workspace_canonical_path,
        )
        if not changed.succeeded:
            return ()
        paths = tuple(
            line.strip()
            for line in changed.output.splitlines()
            if line.strip() and _path_in_scope(line.strip(), self.scope_paths)
        )
        if not paths:
            return ()
        path = paths[0]
        identity = "ci-finding:" + hashlib.sha256(
            f"{work.work_identity}|{work.exact_head_sha}|{work.ci_identity}".encode()
        ).hexdigest()
        return (
            ImplementerFinding(
                finding_identity=identity,
                severity="BLOCKING",
                path=path,
                location=f"CI:{work.ci_identity}",
                problem="required exact-head CIが失敗した",
                basis="Production CI policy",
                evidence=work.ci_identity,
                impact="External Review / Integration Gateへ進めない",
                suggested_fix=(
                    "current exact HEADのCI failureを調査し、同一lineageで修正して"
                    "required CIをPASSさせる"
                ),
            ),
        )

    def _prepare_workspace(
        self,
        registration: ProductDevelopmentRegistration,
        branch: str,
        exact_head: str,
    ) -> bool:
        root = registration.workspace_canonical_path
        status = self._git_output(root, ("status", "--porcelain"))
        if status is None or status.strip():
            return False
        current_head = self._git_output(root, ("rev-parse", "HEAD"))
        current_branch = self._git_output(root, ("branch", "--show-current"))
        if current_head == exact_head and current_branch == branch:
            return True
        switched = self._run(
            ("git", "switch", "-C", branch, exact_head),
            root,
            timeout_seconds=180,
        )
        if not switched.succeeded:
            return False
        return (
            self._git_output(root, ("rev-parse", "HEAD")) == exact_head
            and self._git_output(root, ("branch", "--show-current")) == branch
            and self._git_output(root, ("status", "--porcelain")) == ""
        )

    def _materialize_workspace_effect(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        branch: str,
        exact_base: str,
        effect: WorkspaceEffectReport,
        commit_message: str,
    ) -> MaterializedProposal | None:
        if (
            effect.work_identity != work.work_identity
            or effect.input_target_identity != exact_base
        ):
            return None
        current_head = self._git_output(
            registration.workspace_canonical_path,
            ("rev-parse", "HEAD"),
        )
        current_branch = self._git_output(
            registration.workspace_canonical_path,
            ("branch", "--show-current"),
        )
        if current_head != effect.result_target_identity or current_branch != branch:
            return None
        return self._materialize_current_workspace(
            registration,
            work,
            branch,
            exact_base,
            commit_message,
        )

    def _materialize_current_workspace(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        branch: str,
        exact_base: str,
        commit_message: str,
    ) -> MaterializedProposal | None:
        root = registration.workspace_canonical_path
        current_branch = self._git_output(root, ("branch", "--show-current"))
        current_head = self._git_output(root, ("rev-parse", "HEAD"))
        if current_branch != branch or current_head is None:
            return None
        if not self._is_ancestor(root, exact_base, current_head):
            return None

        dirty = self._workspace_changed_paths(root)
        if dirty is None:
            return None
        if any(not _path_in_scope(path, self.scope_paths) for path in dirty):
            return None
        if dirty:
            add_args = ("add", "-A", "--", *self.scope_paths)
            if not self._run(("git", *add_args), root).succeeded:
                return None
            staged = self._git_output(root, ("diff", "--cached", "--name-only", "HEAD"))
            staged_paths = tuple(
                line.strip() for line in (staged or "").splitlines() if line.strip()
            )
            if not staged_paths or any(
                not _path_in_scope(path, self.scope_paths) for path in staged_paths
            ):
                return None
            if not self._run(
                ("git", "diff", "--cached", "--check", "HEAD"),
                root,
            ).succeeded:
                return None
            committed = self._run(
                ("git", "commit", "-m", commit_message),
                root,
                timeout_seconds=180,
            )
            if not committed.succeeded:
                return None

        candidate = self._git_output(root, ("rev-parse", "HEAD"))
        if candidate is None or candidate == exact_base:
            return None
        if not self._is_ancestor(root, exact_base, candidate):
            return None
        changed = self._git_output(
            root,
            ("diff", "--name-only", exact_base, candidate, "--"),
        )
        if changed is None:
            return None
        changed_paths = tuple(
            line.strip() for line in changed.splitlines() if line.strip()
        )
        if not changed_paths or any(
            not _path_in_scope(path, self.scope_paths) for path in changed_paths
        ):
            return None
        patch = self._git_output(
            root,
            ("diff", "--binary", "--no-ext-diff", exact_base, candidate, "--"),
        )
        if patch is None or not patch:
            return None
        return MaterializedProposal(
            work_identity=work.work_identity,
            exact_base_sha=exact_base,
            candidate_sha=candidate,
            changed_paths=changed_paths,
            patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
        )

    def _publish_materialized(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        branch: str,
        generation: int,
        materialized: MaterializedProposal,
    ) -> LineageResult | TransitionExecutionResult:
        published = self.lineage.publish(
            LineageIdentity(
                registration.repository_identity,
                work.work_identity,
                work.issue_number,
                branch,
                registration.trunk_branch,
                generation,
            ),
            materialized,
        )
        if published.status in {LineageStatus.UNCERTAIN, LineageStatus.BLOCKED}:
            return _intervention(published.detail)
        if published.status is not LineageStatus.CONFIRMED or published.pull_request is None:
            return _failed(published.detail)
        return published

    def _workspace_changed_paths(self, root: Path) -> tuple[str, ...] | None:
        staged = self._git_output(root, ("diff", "--cached", "--name-only", "HEAD"))
        unstaged = self._git_output(root, ("diff", "--name-only", "--"))
        untracked = self._git_output(
            root,
            ("ls-files", "--others", "--exclude-standard"),
        )
        if staged is None or unstaged is None or untracked is None:
            return None
        return tuple(
            sorted(
                {
                    line.strip()
                    for raw in (staged, unstaged, untracked)
                    for line in raw.splitlines()
                    if line.strip()
                }
            )
        )

    def _canonical_context(
        self,
        root: Path,
        targets: tuple[str, ...],
    ) -> tuple[tuple[str, str], ...] | None:
        if not targets:
            return None
        result: list[tuple[str, str]] = []
        for relative in targets:
            path = root / relative
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root.resolve(strict=False)):
                    return None
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError, ValueError):
                return None
            if not content.strip():
                return None
            result.append((relative, content))
        return tuple(result)

    def _is_ancestor(self, root: Path, older: str, newer: str) -> bool:
        return self._run(
            ("git", "merge-base", "--is-ancestor", older, newer),
            root,
        ).succeeded

    def _git_output(self, root: Path, arguments: Sequence[str]) -> str | None:
        result = self._run(("git", *arguments), root)
        return result.output.strip() if result.succeeded else None

    def _integrate(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        schedule_key: str,
    ) -> TransitionExecutionResult:
        if not _integration_evidence_valid(work):
            return _intervention("INTEGRATION_EVIDENCE_INVALID")
        assert work.exact_head_sha is not None
        pr_number = _pr_number(work.active_lineage_identity)
        if pr_number is None:
            return _intervention("INTEGRATION_PR_IDENTITY_INVALID")
        observed = self._pr_state(registration, pr_number)
        if observed is None:
            return _waiting("PR_READBACK_UNAVAILABLE")
        if observed[0] == "MERGED" and observed[1] == work.exact_head_sha:
            self._mark_integrated(work, pr_number, schedule_key)
            return _progressed("PR_ALREADY_MERGED")
        expected = ("OPEN", work.exact_head_sha, registration.trunk_branch)
        if observed[:3] != expected:
            return _intervention("INTEGRATION_PR_PRECONDITION_CONFLICT")
        if observed[3]:
            ready = self._ensure_pr_ready(
                registration,
                work,
                pr_number,
                schedule_key,
                observed,
            )
            if ready is not None:
                return ready
            observed = self._pr_state(registration, pr_number)
            if observed is None or observed[:3] != expected or observed[3]:
                return _intervention("PR_READY_READBACK_UNPROVEN")

        key = _effect_key(schedule_key, "MERGE", f"pr:{pr_number}")
        if _pending_effect(self.work_state.recover(work.work_identity), key) == "UNCERTAIN":
            return _intervention("MERGE_EFFECT_UNCERTAIN")
        attempt = EffectAttempt(
            idempotency_key=key,
            work_identity=work.work_identity,
            kind="MERGE",
            target_identity=f"pr:{pr_number}",
            status="INTENT_RECORDED",
            packet_generation=_generation(schedule_key),
            expected_preconditions=(
                ("head", work.exact_head_sha),
                ("base", registration.trunk_branch),
                ("state", "OPEN"),
                ("draft", "false"),
            ),
            expected_effect=(("state", "MERGED"),),
        )
        if not self.work_state.record_effect_intent(attempt):
            fresh = self._pr_state(registration, pr_number)
            if fresh is not None and fresh[:2] == ("MERGED", work.exact_head_sha):
                self._mark_integrated(work, pr_number, schedule_key)
                return _progressed("MERGE_EFFECT_ALREADY_CONFIRMED")
            return _intervention("MERGE_EFFECT_STATE_CONFLICT")
        if self._pr_state(registration, pr_number) != observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return _intervention("MERGE_PRECONDITION_CHANGED")

        sent = self._run(
            (
                "gh",
                "pr",
                "merge",
                str(pr_number),
                "--repo",
                registration.repository_identity,
                "--merge",
                "--match-head-commit",
                work.exact_head_sha,
            ),
            registration.workspace_canonical_path,
            timeout_seconds=300,
        )
        fresh = self._pr_state(registration, pr_number)
        if fresh is not None and fresh[:2] == ("MERGED", work.exact_head_sha):
            self.work_state.record_effect_outcome(key, "CONFIRMED")
            self._mark_integrated(work, pr_number, schedule_key)
            return _progressed("MERGE_CONFIRMED")
        if sent.succeeded and fresh == observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return _failed("MERGE_NO_EFFECT")
        self.work_state.record_effect_outcome(key, "UNCERTAIN")
        return _intervention("MERGE_READBACK_UNPROVEN")

    def _ensure_pr_ready(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        pr_number: int,
        schedule_key: str,
        observed: tuple[str, str, str, bool],
    ) -> TransitionExecutionResult | None:
        if not observed[3]:
            return None
        key = _effect_key(schedule_key, "READY", f"pr:{pr_number}")
        pending = _pending_effect(self.work_state.recover(work.work_identity), key)
        if pending == "UNCERTAIN":
            fresh = self._pr_state(registration, pr_number)
            if fresh is not None and fresh[:3] == observed[:3] and not fresh[3]:
                self.work_state.record_effect_outcome(key, "CONFIRMED")
                return None
            return _intervention("PR_READY_EFFECT_UNCERTAIN")

        attempt = EffectAttempt(
            idempotency_key=key,
            work_identity=work.work_identity,
            kind="READY",
            target_identity=f"pr:{pr_number}",
            status="INTENT_RECORDED",
            packet_generation=_generation(schedule_key),
            expected_preconditions=(
                ("head", observed[1]),
                ("base", observed[2]),
                ("state", observed[0]),
                ("draft", "true"),
            ),
            expected_effect=(("draft", "false"),),
        )
        if not self.work_state.record_effect_intent(attempt):
            fresh = self._pr_state(registration, pr_number)
            if fresh is not None and fresh[:3] == observed[:3] and not fresh[3]:
                return None
            return _intervention("PR_READY_EFFECT_STATE_CONFLICT")

        fresh_before = self._pr_state(registration, pr_number)
        if fresh_before != observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            if fresh_before is not None and fresh_before[:3] == observed[:3] and not fresh_before[3]:
                return None
            return _intervention("PR_READY_PRECONDITION_CHANGED")

        sent = self._run(
            (
                "gh",
                "pr",
                "ready",
                str(pr_number),
                "--repo",
                registration.repository_identity,
            ),
            registration.workspace_canonical_path,
        )
        fresh = self._pr_state(registration, pr_number)
        if fresh is not None and fresh[:3] == observed[:3] and not fresh[3]:
            self.work_state.record_effect_outcome(key, "CONFIRMED")
            return None
        if sent.succeeded and fresh == observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return _failed("PR_READY_NO_EFFECT")
        self.work_state.record_effect_outcome(key, "UNCERTAIN")
        return _intervention("PR_READY_READBACK_UNPROVEN")

    def _complete_work(
        self,
        registration: ProductDevelopmentRegistration,
        bootstrap: BootstrapResult,
        work: V2WorkObservation,
        schedule_key: str,
    ) -> TransitionExecutionResult:
        if not work.merged:
            return _intervention("WORK_COMPLETION_MERGE_REQUIRED")
        issue_state = self._issue_state(registration, work.issue_number)
        if issue_state is None:
            return _waiting("ISSUE_READBACK_UNAVAILABLE")
        if issue_state == "OPEN":
            close = self._close_issue(registration, work, schedule_key)
            if close is not None:
                return close
        elif issue_state != "CLOSED":
            return _intervention("ISSUE_STATE_INVALID")

        projected = next(
            (item for item in bootstrap.projection.works if item.issue_number == work.issue_number),
            None,
        )
        if projected is None:
            return _intervention("PROJECTED_WORK_MISSING")
        project = self._ensure_project_done(
            registration,
            work,
            projected.project_item_id,
            schedule_key,
        )
        if project is not None:
            return project
        self._advance_work(
            work,
            lifecycle="COMPLETED",
            selected_transition="COMPLETE_WORK",
            active_lineage_identity=work.active_lineage_identity,
            next_action="SELECT_NEXT_WORK",
            evidence=(f"work-completed:{work.issue_number}",),
            schedule_key=schedule_key,
        )
        return _progressed("WORK_COMPLETED")

    def _close_issue(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        schedule_key: str,
    ) -> TransitionExecutionResult | None:
        key = _effect_key(schedule_key, "ISSUE_UPDATE", f"issue:{work.issue_number}")
        if _pending_effect(self.work_state.recover(work.work_identity), key) == "UNCERTAIN":
            return _intervention("ISSUE_CLOSE_UNCERTAIN")
        attempt = EffectAttempt(
            idempotency_key=key,
            work_identity=work.work_identity,
            kind="ISSUE_UPDATE",
            target_identity=f"issue:{work.issue_number}",
            status="INTENT_RECORDED",
            packet_generation=_generation(schedule_key),
            expected_preconditions=(("state", "OPEN"),),
            expected_effect=(("state", "CLOSED"),),
        )
        if not self.work_state.record_effect_intent(attempt):
            if self._issue_state(registration, work.issue_number) == "CLOSED":
                return None
            return _intervention("ISSUE_CLOSE_STATE_CONFLICT")
        if self._issue_state(registration, work.issue_number) != "OPEN":
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return _intervention("ISSUE_CLOSE_PRECONDITION_CHANGED")

        sent = self._run(
            (
                "gh",
                "issue",
                "close",
                str(work.issue_number),
                "--repo",
                registration.repository_identity,
            ),
            registration.workspace_canonical_path,
        )
        state = self._issue_state(registration, work.issue_number)
        if state == "CLOSED":
            self.work_state.record_effect_outcome(key, "CONFIRMED")
            return None
        if sent.succeeded and state == "OPEN":
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return _failed("ISSUE_CLOSE_NO_EFFECT")
        self.work_state.record_effect_outcome(key, "UNCERTAIN")
        return _intervention("ISSUE_CLOSE_READBACK_UNPROVEN")

    def _ensure_project_done(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        item_id: str,
        schedule_key: str,
    ) -> TransitionExecutionResult | None:
        project_id = self._project_id(registration)
        option = self._project_status_option(
            registration,
            project_id,
            self.done_project_status,
        )
        if project_id is None or option is None:
            return _intervention("PROJECT_STATUS_AUTHORITY_UNAVAILABLE")
        field_id, option_id = option
        observed = self._project_item_status(registration, item_id)
        if observed == self.done_project_status:
            return None

        key = _effect_key(
            schedule_key,
            "PROJECT_FIELD_UPDATE",
            f"project-item:{item_id}:Status",
        )
        if _pending_effect(self.work_state.recover(work.work_identity), key) == "UNCERTAIN":
            return _intervention("PROJECT_STATUS_UNCERTAIN")
        attempt = EffectAttempt(
            idempotency_key=key,
            work_identity=work.work_identity,
            kind="PROJECT_FIELD_UPDATE",
            target_identity=f"project-item:{item_id}:Status",
            status="INTENT_RECORDED",
            packet_generation=_generation(schedule_key),
            expected_preconditions=(("value", observed or "<unset>"),),
            expected_effect=(("value", self.done_project_status),),
        )
        if not self.work_state.record_effect_intent(attempt):
            if self._project_item_status(registration, item_id) == self.done_project_status:
                return None
            return _intervention("PROJECT_STATUS_STATE_CONFLICT")
        if self._project_item_status(registration, item_id) != observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return _intervention("PROJECT_STATUS_PRECONDITION_CHANGED")

        sent = self._run(
            (
                "gh",
                "project",
                "item-edit",
                "--id",
                item_id,
                "--project-id",
                project_id,
                "--field-id",
                field_id,
                "--single-select-option-id",
                option_id,
            ),
            registration.workspace_canonical_path,
        )
        fresh = self._project_item_status(registration, item_id)
        if fresh == self.done_project_status:
            self.work_state.record_effect_outcome(key, "CONFIRMED")
            return None
        if sent.succeeded and fresh == observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return _failed("PROJECT_STATUS_NO_EFFECT")
        self.work_state.record_effect_outcome(key, "UNCERTAIN")
        return _intervention("PROJECT_STATUS_READBACK_UNPROVEN")

    def _mark_integrated(
        self,
        work: V2WorkObservation,
        pr_number: int,
        schedule_key: str,
    ) -> None:
        assert work.exact_head_sha is not None
        self._advance_work(
            work,
            lifecycle="RUNNING",
            selected_transition="INTEGRATE",
            active_lineage_identity=f"pr:{pr_number}",
            next_action="COMPLETE_WORK",
            evidence=(f"merged-pr:{pr_number}:{work.exact_head_sha}",),
            schedule_key=schedule_key,
        )

    def _advance_work(
        self,
        work: V2WorkObservation,
        *,
        lifecycle: str,
        selected_transition: str,
        active_lineage_identity: str | None,
        next_action: str,
        evidence: tuple[str, ...],
        schedule_key: str,
    ) -> None:
        recovered = self.work_state.recover(work.work_identity)
        if recovered is None:
            raise RuntimeError("WORK_STATE_RECOVERY_MISSING")
        self.work_state.upsert_work(
            replace(
                recovered.record,
                lifecycle=lifecycle,
                selected_transition=selected_transition,
                active_lineage_identity=active_lineage_identity,
            )
        )
        digest = hashlib.sha256(
            f"{schedule_key}|{lifecycle}|{next_action}".encode()
        ).hexdigest()
        self.work_state.record_checkpoint(
            WorkCheckpoint(
                identity=f"autonomous-checkpoint:{digest}",
                work_identity=work.work_identity,
                run_identity=f"autonomous:{schedule_key}",
                checkpoint_kind="SAFE_POINT",
                resumable_state=lifecycle,
                next_action=next_action,
                external_target_identities=(active_lineage_identity,)
                if active_lineage_identity is not None
                else (),
                evidence_identities=evidence,
            )
        )

    def _ensure_commit_available(
        self,
        registration: ProductDevelopmentRegistration,
        sha: str,
        remote_ref: str,
    ) -> bool:
        local = self._run(
            ("git", "cat-file", "-e", f"{sha}^{{commit}}"),
            registration.workspace_canonical_path,
        )
        if local.succeeded:
            return True
        fetched = self._run(
            ("git", "fetch", "origin", f"refs/heads/{remote_ref}"),
            registration.workspace_canonical_path,
            timeout_seconds=180,
        )
        if not fetched.succeeded:
            return False
        readback = self._run(
            ("git", "cat-file", "-e", f"{sha}^{{commit}}"),
            registration.workspace_canonical_path,
        )
        return readback.succeeded

    def _trunk_head(self, registration: ProductDevelopmentRegistration) -> str | None:
        result = self._run(
            (
                "git",
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{registration.trunk_branch}",
            ),
            registration.workspace_canonical_path,
        )
        if not result.succeeded:
            return None
        lines = [line for line in result.output.splitlines() if line.strip()]
        if len(lines) != 1:
            return None
        sha = lines[0].split(maxsplit=1)[0]
        return sha if _SHA_RE.fullmatch(sha) is not None else None

    def _pr_state(
        self,
        registration: ProductDevelopmentRegistration,
        pr_number: int,
    ) -> tuple[str, str, str, bool] | None:
        result = self._run(
            (
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                registration.repository_identity,
                "--json",
                "number,state,headRefOid,baseRefName,isDraft",
            ),
            registration.workspace_canonical_path,
        )
        if not result.succeeded:
            return None
        payload = _json_mapping(result.output)
        if payload is None or payload.get("number") != pr_number:
            return None
        state = payload.get("state")
        head = payload.get("headRefOid")
        base = payload.get("baseRefName")
        draft = payload.get("isDraft")
        if (
            not isinstance(state, str)
            or not isinstance(head, str)
            or not isinstance(base, str)
            or not isinstance(draft, bool)
        ):
            return None
        return state, head, base, draft

    def _issue_state(
        self,
        registration: ProductDevelopmentRegistration,
        issue_number: int,
    ) -> str | None:
        result = self._run(
            (
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--repo",
                registration.repository_identity,
                "--json",
                "number,state",
            ),
            registration.workspace_canonical_path,
        )
        payload = _json_mapping(result.output) if result.succeeded else None
        if payload is None or payload.get("number") != issue_number:
            return None
        state = payload.get("state")
        return state if isinstance(state, str) else None

    def _project_id(self, registration: ProductDevelopmentRegistration) -> str | None:
        result = self._run(
            (
                "gh",
                "project",
                "view",
                str(registration.project_number),
                "--owner",
                registration.project_owner,
                "--format",
                "json",
            ),
            registration.workspace_canonical_path,
        )
        payload = _json_mapping(result.output) if result.succeeded else None
        value = payload.get("id") if payload is not None else None
        return value if isinstance(value, str) else None

    def _project_status_option(
        self,
        registration: ProductDevelopmentRegistration,
        project_id: str | None,
        status_name: str,
    ) -> tuple[str, str] | None:
        if project_id is None:
            return None
        query = (
            "query($id:ID!){node(id:$id){... on ProjectV2{fields(first:100){nodes{"
            "... on ProjectV2SingleSelectField{id name options{id name}}}"
            "pageInfo{hasNextPage}}}}}"
        )
        result = self._run(
            (
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"id={project_id}",
            ),
            registration.workspace_canonical_path,
        )
        payload = _json_mapping(result.output) if result.succeeded else None
        if payload is None:
            return None
        fields = _mapping(_mapping(_mapping(payload, "data"), "node"), "fields")
        if _mapping(fields, "pageInfo").get("hasNextPage") is True:
            return None
        nodes = fields.get("nodes")
        if not isinstance(nodes, list):
            return None
        for raw in nodes:
            if not isinstance(raw, dict) or raw.get("name") != "Status":
                continue
            field_id = raw.get("id")
            options = raw.get("options")
            if not isinstance(field_id, str) or not isinstance(options, list):
                return None
            for option in options:
                if not isinstance(option, dict) or option.get("name") != status_name:
                    continue
                option_id = option.get("id")
                if isinstance(option_id, str):
                    return field_id, option_id
        return None

    def _project_item_status(
        self,
        registration: ProductDevelopmentRegistration,
        item_id: str,
    ) -> str | None:
        query = (
            "query($id:ID!){node(id:$id){... on ProjectV2Item{"
            "fieldValues(first:100){nodes{... on ProjectV2ItemFieldSingleSelectValue{"
            "field{... on ProjectV2FieldCommon{name}} name}}pageInfo{hasNextPage}}}}}"
        )
        result = self._run(
            (
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"id={item_id}",
            ),
            registration.workspace_canonical_path,
        )
        payload = _json_mapping(result.output) if result.succeeded else None
        if payload is None:
            return None
        values = _mapping(_mapping(_mapping(payload, "data"), "node"), "fieldValues")
        if _mapping(values, "pageInfo").get("hasNextPage") is True:
            return None
        nodes = values.get("nodes")
        if not isinstance(nodes, list):
            return None
        for raw in nodes:
            if not isinstance(raw, dict):
                continue
            if _mapping(raw, "field").get("name") != "Status":
                continue
            value = raw.get("name")
            return value if isinstance(value, str) else None
        return None

    def _run(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        timeout_seconds: int = 120,
    ) -> CommandResultLike:
        try:
            return self.runner.run(
                command,
                cwd=cwd,
                environment=self.environment,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return _FailedResult()


@dataclass(frozen=True, slots=True)
class _FailedResult:
    returncode: int = 127
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return False


def _integration_evidence_valid(work: V2WorkObservation) -> bool:
    return (
        work.exact_head_sha is not None
        and work.verification_state is EvidenceState.PASS
        and work.ci_state is EvidenceState.PASS
        and work.review_state is EvidenceState.PASS
        and (
            not work.human_verification_required
            or work.human_verification_state is EvidenceState.PASS
        )
    )


def _generation(schedule_key: str) -> int:
    digest = hashlib.sha256(schedule_key.encode()).hexdigest()
    return int(digest[:15], 16) + 1


def _effect_key(schedule_key: str, kind: str, target: str) -> str:
    raw = f"{schedule_key}|{kind}|{target}"
    return "autonomous-effect:" + hashlib.sha256(raw.encode()).hexdigest()


def _pending_effect(recovered: RecoveredWork | None, key: str) -> str | None:
    if recovered is None:
        return None
    for effect in recovered.pending_effects:
        if effect.idempotency_key == key:
            return effect.status
    return None


def _pr_number(identity: str | None) -> int | None:
    if identity is None:
        return None
    match = _PR_RE.fullmatch(identity)
    if match is None:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def _path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    for scope in scopes:
        normalized = scope.rstrip("/")
        if normalized in {"", "."}:
            return True
        if path == normalized or path.startswith(normalized + "/"):
            return True
    return False


def _work_branch(template: str, issue_number: int) -> str:
    if template.count("{issue}") != 1:
        raise RuntimeError("WORK_BRANCH_TEMPLATE_INVALID")
    branch = template.replace("{issue}", str(issue_number))
    if not branch or branch in {"main", "master"} or ".." in branch or " " in branch:
        raise RuntimeError("WORK_BRANCH_TEMPLATE_INVALID")
    return branch


def _commit_message(transition: V2Transition, issue_number: int) -> str:
    prefix = "docs" if transition is V2Transition.DESIGN else "feat"
    if transition is V2Transition.REPAIR:
        prefix = "fix"
    return f"{prefix}: #{issue_number} の{transition.value}を反映する"


def _json_mapping(raw: str) -> dict[str, object] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    return nested if isinstance(nested, dict) else {}


def _progressed(detail: str) -> TransitionExecutionResult:
    return TransitionExecutionResult(TransitionExecutionStatus.PROGRESSED, detail)


def _waiting(detail: str) -> TransitionExecutionResult:
    return TransitionExecutionResult(TransitionExecutionStatus.WAITING, detail)


def _failed(detail: str) -> TransitionExecutionResult:
    return TransitionExecutionResult(TransitionExecutionStatus.FAILED, detail)


def _intervention(detail: str) -> TransitionExecutionResult:
    return TransitionExecutionResult(TransitionExecutionStatus.INTERVENTION_REQUIRED, detail)
