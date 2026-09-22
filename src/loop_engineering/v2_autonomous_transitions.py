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

from .v2_autonomous_runner import TransitionExecutionResult, TransitionExecutionStatus
from .v2_development_lineage import (
    GitHubDevelopmentLineageEffects,
    LineageIdentity,
    LineageStatus,
    TrustedProposalMaterializer,
)
from .v2_goal_planning import BootstrapResult, PlannedWork, ProductDevelopmentRegistration
from .v2_implementer import (
    CodexProposalImplementer,
    DevelopmentTaskPacket,
    ImplementerStatus,
    ImplementerTransition,
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


class AutonomousWorkStatePort(Protocol):
    def recover(self, work_identity: str) -> RecoveredWork | None: ...

    def upsert_work(self, record: WorkRecord) -> None: ...

    def record_checkpoint(self, checkpoint: WorkCheckpoint) -> None: ...

    def record_effect_intent(self, attempt: EffectAttempt) -> bool: ...

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None: ...


@dataclass(slots=True)
class V2AutonomousTransitionExecutor:
    implementer: CodexProposalImplementer
    materializer: TrustedProposalMaterializer
    lineage: GitHubDevelopmentLineageEffects
    work_state: AutonomousWorkStatePort
    runner: AutonomousTransitionCommandRunner
    environment: Mapping[str, str]
    scope_paths: tuple[str, ...]
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
        if transition in {V2Transition.VERIFY, V2Transition.REVIEW, V2Transition.HUMAN_VERIFY}:
            return _waiting(f"{transition.value}_EVIDENCE_PENDING")
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
        )
        implemented = self.implementer.execute(packet)
        if implemented.status is ImplementerStatus.BLOCKED:
            return _intervention(implemented.detail)
        if implemented.status is not ImplementerStatus.SUCCESS or implemented.proposal is None:
            return _failed(implemented.detail)

        materialized = self.materializer.materialize(
            workspace=registration.workspace_canonical_path,
            repository=registration.repository_identity,
            proposal=implemented.proposal,
            commit_message=_commit_message(decision.transition, work.issue_number),
        )
        if materialized is None:
            return _failed("PROPOSAL_MATERIALIZATION_FAILED")

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

        self._advance_work(
            work,
            lifecycle="RUNNING",
            selected_transition=decision.transition.value,
            active_lineage_identity=f"pr:{published.pull_request.number}",
            next_action=(
                "IMPLEMENT_SAME_LINEAGE"
                if decision.transition is V2Transition.DESIGN
                else "OBSERVE_EXACT_HEAD_EVIDENCE"
            ),
            evidence=(f"head:{published.pull_request.head_sha}",),
            schedule_key=decision.schedule_key,
        )
        return _progressed(published.detail)

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
        if observed != expected:
            return _intervention("INTEGRATION_PR_PRECONDITION_CONFLICT")

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
    ) -> tuple[str, str, str] | None:
        result = self._run(
            (
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                registration.repository_identity,
                "--json",
                "number,state,headRefOid,baseRefName",
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
        if not isinstance(state, str) or not isinstance(head, str) or not isinstance(base, str):
            return None
        return state, head, base

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
