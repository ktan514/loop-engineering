"""初期Goalを型付きWork Planへ変換し、検証済みPlanだけをprojectionする。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_LOGICAL_KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_ALLOWED_WORK_KINDS = frozenset(
    {"DESIGN_IMPLEMENT", "IMPLEMENTATION", "DOCUMENTATION", "VERIFICATION", "INTEGRATION"}
)
_MAX_WORKS = 64


class PlanningValidationError(RuntimeError):
    """GoalまたはPlanning proposalを安全に採用できない。"""


@dataclass(frozen=True, slots=True)
class ProductDevelopmentRegistration:
    product_key: str
    workspace_canonical_path: Path
    repository_identity: str
    project_owner: str
    project_number: int
    trunk_branch: str
    goal_definition_identity: str
    goal_revision: str
    goal_text: str
    acceptance_criteria: tuple[str, ...]
    work_branch_template: str
    ci_workflow_name: str
    initial_project_status: str
    human_verification_policy: str = "WHEN_REQUIRED"
    self_improvement_target: str | None = None

    def __post_init__(self) -> None:
        text_values = (
            self.product_key,
            self.repository_identity,
            self.project_owner,
            self.trunk_branch,
            self.goal_definition_identity,
            self.goal_revision,
            self.goal_text,
            self.work_branch_template,
            self.ci_workflow_name,
            self.initial_project_status,
            self.human_verification_policy,
        )
        if any(not value.strip() or value.strip() != value for value in text_values):
            raise PlanningValidationError("REGISTRATION_TEXT_INVALID")
        if "/" not in self.repository_identity or self.project_number < 1:
            raise PlanningValidationError("REGISTRATION_IDENTITY_INVALID")
        if not self.workspace_canonical_path.is_absolute():
            raise PlanningValidationError("REGISTRATION_WORKSPACE_INVALID")
        if not self.acceptance_criteria or any(
            not item.strip() for item in self.acceptance_criteria
        ):
            raise PlanningValidationError("REGISTRATION_ACCEPTANCE_INVALID")
        if self.self_improvement_target is not None and "/" not in self.self_improvement_target:
            raise PlanningValidationError("REGISTRATION_IMPROVEMENT_TARGET_INVALID")


@dataclass(frozen=True, slots=True)
class PlannedWork:
    logical_key: str
    title: str
    purpose: str
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    work_kind: str = "DESIGN_IMPLEMENT"
    human_verification_required: bool = False
    canonical_design_targets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkPlanProposal:
    proposal_identity: str
    goal_revision: str
    works: tuple[PlannedWork, ...]
    completion_conditions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectedWork:
    logical_key: str
    issue_number: int
    issue_url: str
    project_item_id: str
    acceptance_digest: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectedPlan:
    goal_issue_number: int
    goal_issue_url: str
    goal_project_item_id: str
    works: tuple[ProjectedWork, ...]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    proposal: WorkPlanProposal
    projection: ProjectedPlan


class GoalPlannerPort(Protocol):
    def plan(self, registration: ProductDevelopmentRegistration) -> WorkPlanProposal: ...


class PlanningProjectionPort(Protocol):
    def ensure_plan(
        self,
        registration: ProductDevelopmentRegistration,
        proposal: WorkPlanProposal,
    ) -> ProjectedPlan: ...


class SingleWorkGoalPlanner:
    """外部Plannerが無い場合もbootstrapを失わないgeneric fallback。"""

    def plan(self, registration: ProductDevelopmentRegistration) -> WorkPlanProposal:
        logical_key = "goal-implementation"
        work = PlannedWork(
            logical_key=logical_key,
            title=_goal_title(registration.goal_text),
            purpose=registration.goal_text,
            acceptance_criteria=registration.acceptance_criteria,
            work_kind="DESIGN_IMPLEMENT",
            human_verification_required=registration.human_verification_policy == "ALWAYS",
            canonical_design_targets=("docs/design.md",),
        )
        proposal = WorkPlanProposal(
            proposal_identity=proposal_identity(registration, (work,)),
            goal_revision=registration.goal_revision,
            works=(work,),
            completion_conditions=registration.acceptance_criteria,
        )
        validate_work_plan(registration, proposal)
        return proposal


class V2GoalBootstrapService:
    def __init__(self, planner: GoalPlannerPort, projection: PlanningProjectionPort) -> None:
        self._planner = planner
        self._projection = projection

    def bootstrap(self, registration: ProductDevelopmentRegistration) -> BootstrapResult:
        proposal = self._planner.plan(registration)
        validate_work_plan(registration, proposal)
        projection = self._projection.ensure_plan(registration, proposal)
        if tuple(item.logical_key for item in projection.works) != tuple(
            item.logical_key for item in proposal.works
        ):
            raise PlanningValidationError("PROJECTION_WORK_IDENTITY_MISMATCH")
        return BootstrapResult(proposal, projection)


def validate_work_plan(
    registration: ProductDevelopmentRegistration,
    proposal: WorkPlanProposal,
) -> None:
    if proposal.goal_revision != registration.goal_revision:
        raise PlanningValidationError("PLAN_GOAL_REVISION_MISMATCH")
    if not 1 <= len(proposal.works) <= _MAX_WORKS:
        raise PlanningValidationError("PLAN_WORK_COUNT_INVALID")
    if not proposal.completion_conditions or any(
        not condition.strip() for condition in proposal.completion_conditions
    ):
        raise PlanningValidationError("PLAN_COMPLETION_INVALID")

    keys: set[str] = set()
    for work in proposal.works:
        if _LOGICAL_KEY_RE.fullmatch(work.logical_key) is None or work.logical_key in keys:
            raise PlanningValidationError("PLAN_LOGICAL_KEY_INVALID")
        keys.add(work.logical_key)
        if (
            not work.title.strip()
            or not work.purpose.strip()
            or not work.acceptance_criteria
            or any(not item.strip() for item in work.acceptance_criteria)
        ):
            raise PlanningValidationError("PLAN_WORK_CONTENT_INVALID")
        if work.work_kind not in _ALLOWED_WORK_KINDS:
            raise PlanningValidationError("PLAN_WORK_KIND_INVALID")
        if len(set(work.dependencies)) != len(work.dependencies):
            raise PlanningValidationError("PLAN_DEPENDENCY_DUPLICATE")
        if any(not target.strip() for target in work.canonical_design_targets):
            raise PlanningValidationError("PLAN_DESIGN_TARGET_INVALID")

    for work in proposal.works:
        for dependency in work.dependencies:
            if dependency == work.logical_key or dependency not in keys:
                raise PlanningValidationError("PLAN_DEPENDENCY_INVALID")
    _assert_acyclic(proposal.works)

    expected = proposal_identity(registration, proposal.works)
    if proposal.proposal_identity != expected:
        raise PlanningValidationError("PLAN_IDENTITY_MISMATCH")


def proposal_identity(
    registration: ProductDevelopmentRegistration,
    works: tuple[PlannedWork, ...],
) -> str:
    payload = {
        "product_key": registration.product_key,
        "repository": registration.repository_identity,
        "goal_identity": registration.goal_definition_identity,
        "goal_revision": registration.goal_revision,
        "works": [
            {
                "logical_key": item.logical_key,
                "title": item.title,
                "purpose": item.purpose,
                "acceptance": list(item.acceptance_criteria),
                "dependencies": list(item.dependencies),
                "work_kind": item.work_kind,
                "human_verification_required": item.human_verification_required,
                "design_targets": list(item.canonical_design_targets),
            }
            for item in works
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "plan:" + hashlib.sha256(encoded.encode()).hexdigest()


def acceptance_digest(criteria: tuple[str, ...]) -> str:
    encoded = json.dumps(list(criteria), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def goal_marker(registration: ProductDevelopmentRegistration) -> str:
    return (
        "<!-- loop-engineering-goal:"
        f"{registration.product_key}:{registration.goal_revision} -->"
    )


def work_marker(registration: ProductDevelopmentRegistration, logical_key: str) -> str:
    return (
        "<!-- loop-engineering-work:"
        f"{registration.product_key}:{registration.goal_revision}:{logical_key} -->"
    )


def _assert_acyclic(works: tuple[PlannedWork, ...]) -> None:
    graph = {item.logical_key: item.dependencies for item in works}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise PlanningValidationError("PLAN_DEPENDENCY_CYCLE")
        if key in visited:
            return
        visiting.add(key)
        for dependency in graph[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in graph:
        visit(key)


def _goal_title(goal_text: str) -> str:
    first_line = next((line.strip() for line in goal_text.splitlines() if line.strip()), "Goal")
    return first_line[:120]
