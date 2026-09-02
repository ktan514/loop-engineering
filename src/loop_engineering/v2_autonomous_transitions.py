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

from .v2_autonomous_runner import (
    TransitionExecutionResult,
    TransitionExecutionStatus,
)
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
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "TRANSITION_DECISION_INVALID",
            )
        if transition in {V2Transition.DESIGN, V2Transition.IMPLEMENT, V2Transition.REPAIR}:
            return self._develop(registration, work, planned_work, decision)
        if transition in {V2Transition.VERIFY, V2Transition.REVIEW, V2Transition.HUMAN_VERIFY}:
            return TransitionExecutionResult(
                TransitionExecutionStatus.WAITING,
                f"{transition.value}_EVIDENCE_PENDING",
            )
        if transition is V2Transition.INTEGRATE:
            return self._integrate(registration, work, decision.schedule_key)
        if transition is V2Transition.COMPLETE_WORK:
            return self._complete_work(registration, bootstrap, work, decision.schedule_key)
        return TransitionExecutionResult(
            TransitionExecutionStatus.INTERVENTION_REQUIRED,
            "TRANSITION_UNSUPPORTED",
        )

    def _develop(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        planned: PlannedWork,
        decision: V2SupervisorDecision,
    ) -> TransitionExecutionResult:
        assert decision.transition is not None and decision.schedule_key is not None
        exact_base = work.exact_head_sha or self._trunk_head(registration)
        if exact_base is None:
            return TransitionExecutionResult(
                TransitionExecutionStatus.WAITING,
                "TRUNK_HEAD_UNAVAILABLE",
            )
        implementer_transition = ImplementerTransition(decision.transition.value)
        generation = _generation(decision.schedule_key)
        packet = DevelopmentTaskPacket(
            packet_identity=decision.schedule_key,
            work_identity=work.work_identity,
            generation=generation,
            transition=implementer_transition,
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
            authority_refs=(registration.goal_definition_identity, f"Issue #{work.issue_number}"),
            non_goals=(),
            safety_constraints=(
                "main/trunkへ直接commit/pushしない",
                "GitHub mutationはTrusted Hostへ委譲する",
            ),
        )
        implemented = self.implementer.execute(packet)
        if implemented.status is ImplementerStatus.BLOCKED:
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                implemented.detail,
            )
        if implemented.status is not ImplementerStatus.SUCCESS or implemented.proposal is None:
            return TransitionExecutionResult(TransitionExecutionStatus.FAILED, implemented.detail)
        materialized = self.materializer.materialize(
            workspace=registration.workspace_canonical_path,
            repository=registration.repository_identity,
            proposal=implemented.proposal,
            commit_message=_commit_message(decision.transition, work.issue_number),
        )
        if materialized is None:
            return TransitionExecutionResult(
                TransitionExecutionStatus.FAILED,
                "PROPOSAL_MATERIALIZATION_FAILED",
            )
        branch = _work_branch(registration.work_branch_template, work.issue_number)
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
        if published.status is LineageStatus.UNCERTAIN:
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                published.detail,
            )
        if published.status is LineageStatus.BLOCKED:
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                published.detail,
            )
        if published.status is not LineageStatus.CONFIRMED or published.pull_request is None:
            return TransitionExecutionResult(TransitionExecutionStatus.FAILED, published.detail)
        self._advance_work(
            work,
            lifecycle="RUNNING",
            selected_transition=decision.transition.value,
            active_lineage_identity=f"pr:{published.pull_request.number}",
            next_action="OBSERVE_EXACT_HEAD_EVIDENCE",
            evidence=(f"head:{published.pull_request.head_sha}",),
            schedule_key=decision.schedule_key,
        )
        return TransitionExecutionResult(
            TransitionExecutionStatus.PROGRESSED,
            published.detail,
        )

    def _integrate(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        schedule_key: str,
    ) -> TransitionExecutionResult:
        if (
            work.exact_head_sha is None
            or work.verification_state is not EvidenceState.PASS
            or work.review_state is not EvidenceState.PASS
            or (
                work.human_verification_required
                and work.human_verification_state is not EvidenceState.PASS
            )
        ):
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "INTEGRATION_EVIDENCE_INVALID",
            )
        pr_number = _pr_number(work.active_lineage_identity)
        if pr_number is None:
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "INTEGRATION_PR_IDENTITY_INVALID",
            )
        observed = self._pr_state(registration, pr_number)
        if observed is None:
            return TransitionExecutionResult(TransitionExecutionStatus.WAITING, "PR_READBACK_UNAVAILABLE")
        if observed[0] == "MERGED" and observed[1] == work.exact_head_sha:
            self._advance_work(
                work,
                lifecycle="RUNNING",
                selected_transition="INTEGRATE",
                active_lineage_identity=f"pr:{pr_number}",
                next_action="COMPLETE_WORK",
                evidence=(f"merged-pr:{pr_number}:{work.exact_head_sha}",),
                schedule_key=schedule_key,
            )
            return TransitionExecutionResult(
                TransitionExecutionStatus.PROGRESSED,
                "PR_ALREADY_MERGED",
            )
        if observed != ("OPEN", work.exact_head_sha, registration.trunk_branch):
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "INTEGRATION_PR_PRECONDITION_CONFLICT",
            )
        generation = _generation(schedule_key)
        key = _effect_key(schedule_key, "MERGE", f"pr:{pr_number}")
        attempt = EffectAttempt(
            idempotency_key=key,
            work_identity=work.work_identity,
            kind="MERGE",
            target_identity=f"pr:{pr_number}",
            status="INTENT_RECORDED",
            packet_generation=generation,
            expected_preconditions=(
                ("head", work.exact_head_sha),
                ("base", registration.trunk_branch),
                ("state", "OPEN"),
            ),
            expected_effect=(("state", "MERGED"),),
        )
        pending = _pending_effect(self.work_state.recover(work.work_identity), key)
        if pending == "UNCERTAIN":
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "MERGE_EFFECT_UNCERTAIN",
            )
        if not self.work_state.record_effect_intent(attempt):
            fresh = self._pr_state(registration, pr_number)
            if fresh is not None and fresh[0] == "MERGED" and fresh[1] == work.exact_head_sha:
                return TransitionExecutionResult(
                    TransitionExecutionStatus.PROGRESSED,
                    "MERGE_EFFECT_ALREADY_CONFIRMED",
                )
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "MERGE_EFFECT_STATE_CONFLICT",
            )
        fresh_before = self._pr_state(registration, pr_number)
        if fresh_before != observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "MERGE_PRECONDITION_CHANGED",
            )
        command = self._run(
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
        if fresh is not None and fresh[0] == "MERGED" and fresh[1] == work.exact_head_sha:
            self.work_state.record_effect_outcome(key, "CONFIRMED")
            self._advance_work(
                work,
                lifecycle="RUNNING",
                selected_transition="INTEGRATE",
                active_lineage_identity=f"pr:{pr_number}",
                next_action="COMPLETE_WORK",
                evidence=(f"merged-pr:{pr_number}:{work.exact_head_sha}",),
                schedule_key=schedule_key,
            )
            return TransitionExecutionResult(
                TransitionExecutionStatus.PROGRESSED,
                "MERGE_CONFIRMED",
            )
        if command.succeeded and fresh == observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return TransitionExecutionResult(TransitionExecutionStatus.FAILED, "MERGE_NO_EFFECT")
        self.work_state.record_effect_outcome(key, "UNCERTAIN")
        return TransitionExecutionResult(
            TransitionExecutionStatus.INTERVENTION_REQUIRED,
            "MERGE_READBACK_UNPROVEN",
        )

    def _complete_work(
        self,
        registration: ProductDevelopmentRegistration,
        bootstrap: BootstrapResult,
        work: V2WorkObservation,
        schedule_key: str,
    ) -> TransitionExecutionResult:
        if not work.merged:
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "WORK_COMPLETION_MERGE_REQUIRED",
            )
        issue_state = self._issue_state(registration, work.issue_number)
        if issue_state is None:
            return TransitionExecutionResult(TransitionExecutionStatus.WAITING, "ISSUE_READBACK_UNAVAILABLE")
        if issue_state == "OPEN":
            result = self._close_issue(registration, work, schedule_key)
            if result is not None:
                return result
        elif issue_state != "CLOSED":
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "ISSUE_STATE_INVALID",
            )
        projected = next(
            (item for item in bootstrap.projection.works if item.issue_number == work.issue_number),
            None,
        )
        if projected is None:
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "PROJECTED_WORK_MISSING",
            )
        project_result = self._ensure_project_done(
            registration,
            work,
            projected.project_item_id,
            schedule_key,
        )
        if project_result is not None:
            return project_result
        self._advance_work(
            work,
            lifecycle="COMPLETED",
            selected_transition="COMPLETE_WORK",
            active_lineage_identity=work.active_lineage_identity,
            next_action="SELECT_NEXT_WORK",
            evidence=(f"work-completed:{work.issue_number}",),
            schedule_key=schedule_key,
        )
        return TransitionExecutionResult(
            TransitionExecutionStatus.PROGRESSED,
            "WORK_COMPLETED",
        )

    def _close_issue(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        schedule_key: str,
    ) -> TransitionExecutionResult | None:
        key = _effect_key(schedule_key, "ISSUE_UPDATE", f"issue:{work.issue_number}")
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
        pending = _pending_effect(self.work_state.recover(work.work_identity), key)
        if pending == "UNCERTAIN":
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "ISSUE_CLOSE_UNCERTAIN",
            )
        if not self.work_state.record_effect_intent(attempt):
            if self._issue_state(registration, work.issue_number) == "CLOSED":
                return None
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "ISSUE_CLOSE_STATE_CONFLICT",
            )
        if self._issue_state(registration, work.issue_number) != "OPEN":
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "ISSUE_CLOSE_PRECONDITION_CHANGED",
            )
        result = self._run(
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
        if result.succeeded and state == "OPEN":
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return TransitionExecutionResult(TransitionExecutionStatus.FAILED, "ISSUE_CLOSE_NO_EFFECT")
        self.work_state.record_effect_outcome(key, "UNCERTAIN")
        return TransitionExecutionResult(
            TransitionExecutionStatus.INTERVENTION_REQUIRED,
            "ISSUE_CLOSE_READBACK_UNPROVEN",
        )

    def _ensure_project_done(
        self,
        registration: ProductDevelopmentRegistration,
        work: V2WorkObservation,
        item_id: str,
        schedule_key: str,
    ) -> TransitionExecutionResult | None:
        project_id = self._project_id(registration)
        option = self._project_status_option(registration, project_id, self.done_project_status)
        if project_id is None or option is None:
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "PROJECT_STATUS_AUTHORITY_UNAVAILABLE",
            )
        field_id, option_id = option
        observed = self._project_item_status(item_id)
        if observed == self.done_project_status:
            return None
        key = _effect_key(schedule_key, "PROJECT_FIELD_UPDATE", f"project-item:{item_id}:Status")
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
        pending = _pending_effect(self.work_state.recover(work.work_identity), key)
        if pending == "UNCERTAIN":
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "PROJECT_STATUS_UNCERTAIN",
            )
        if not self.work_state.record_effect_intent(attempt):
            if self._project_item_status(item_id) == self.done_project_status:
                return None
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "PROJECT_STATUS_STATE_CONFLICT",
            )
        if self._project_item_status(item_id) != observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return TransitionExecutionResult(
                TransitionExecutionStatus.INTERVENTION_REQUIRED,
                "PROJECT_STATUS_PRECONDITION_CHANGED",
            )
        result = self._run(
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
        fresh = self._project_item_status(item_id)
        if fresh == self.done_project_status:
            self.work_state.record_effect_outcome(key, "CONFIRMED")
            return None
        if result.succeeded and fresh == observed:
            self.work_state.record_effect_outcome(key, "NO_EFFECT")
            return TransitionExecutionResult(TransitionExecutionStatus.FAILED, "PROJECT_STATUS_NO_EFFECT")
        self.work_state.record_effect_outcome(key, "UNCERTAIN")
        return TransitionExecutionResult(
            TransitionExecutionStatus.INTERVENTION_REQUIRED,
            "PROJECT_STATUS_READBACK_UNPROVEN",
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
        record = replace(
            recovered.record,
            lifecycle=lifecycle,
            selected_transition=selected_transition,
            active_lineage_identity=active_lineage_identity,
        )
        self.work_state.upsert_work(record)
        checkpoint = WorkCheckpoint(
            identity="autonomous-checkpoint:"
            + hashlib.sha256(
                f"{schedule_key}|{lifecycle}|{next_action}".encode("utf-8")
            ).hexdigest(),
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
        self.work_state.record_checkpoint(checkpoint)

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
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("number") != pr_number:
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
        if not result.succeeded:
            return None
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("number") != issue_number:
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
        if not result.succeeded:
            return None
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return None
        value = payload.get("id") if isinstance(payload, dict) else None
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
        if not result.succeeded:
            return None
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return None
        node = _mapping(_mapping(payload, "data"), "node")
        fields = _mapping(node, "fields")
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
                if isinstance(option, dict) and option.get("name") == status_name:
                    option_id = option.get("id")
                    if isinstance(option_id, str):
                        return field_id, option_id
        return None

    def _project_item_status(self, item_id: str) -> str | None:
        query = (
            "query($id:ID!){node(id:$id){... on ProjectV2Item{fieldValues(first:100){nodes{"
            "... on ProjectV2ItemFieldSingleSelectValue{field{... on ProjectV2FieldCommon{name}}name}"
            "}pageInfo{hasNextPage}}}}}"
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
            Path.cwd(),
        )
        if not result.succeeded:
            return None
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return None
        node = _mapping(_mapping(payload, "data"), "node")
        values = _mapping(node, "fieldValues")
        if _mapping(values, "pageInfo").get("hasNextPage") is True:
            return None
        nodes = values.get("nodes")
        if not isinstance(nodes, list):
            return None
        for raw in nodes:
            if not isinstance(raw, dict):
                continue
            if _mapping(raw, "field").get("name") == "Status":
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


def _generation(schedule_key: str) -> int:
    digest = hashlib.sha256(schedule_key.encode("utf-8")).hexdigest()
    return int(digest[:15], 16) + 1


def _effect_key(schedule_key: str, kind: str, target: str) -> str:
    raw = f"{schedule_key}|{kind}|{target}"
    return "autonomous-effect:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    return nested if isinstance(nested, dict) else {}
