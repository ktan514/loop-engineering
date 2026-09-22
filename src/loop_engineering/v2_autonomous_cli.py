"""V2自律Runnerのproduction compositionとCLI実行入口。"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import LoopEngineeringSettings
from .mission_goal import read_mission_goal_identity
from .postgres_runtime import PostgreSQLCommandAdapter
from .v2_autonomous_runner import (
    AutonomousRunStatus,
    DurableGoalBootstrap,
    EvidenceEnricher,
    GitHubAutonomousLineageObserver,
    V2AutonomousRunner,
)
from .v2_autonomous_runtime import PostgreSQLAutonomousRuntimeStore
from .v2_autonomous_transitions import V2AutonomousTransitionExecutor
from .v2_bootstrap_state import PostgreSQLBootstrapStateStore
from .v2_development_lineage import (
    GitHubDevelopmentLineageEffects,
    TrustedProposalMaterializer,
)
from .v2_evidence import GitHubExactHeadCIAdapter, GitHubHumanVerificationAdapter
from .v2_execution_state import V2ExecutionStateStore
from .v2_external_review import (
    ExternalReviewCoordinator,
    OpenAICompatibleExternalReviewer,
    PostgreSQLExternalReviewStore,
)
from .v2_goal_completion import GitHubGoalCompletion
from .v2_goal_planning import (
    ProductDevelopmentRegistration,
    SingleWorkGoalPlanner,
    V2GoalBootstrapService,
)
from .v2_local_llm_coder import build_implementer_backend
from .v2_local_quality import (
    LocalLlmCoderReviewerAdapter,
    LocalQualityCoordinator,
    LocalVerificationRunner,
    PostgreSQLLocalQualityStore,
    VerificationCommandDescriptor,
)
from .v2_planning_projection import GitHubPlanningProjectionAdapter
from .v2_supervisor import V2Supervisor
from .v2_work_definition import GitHubWorkDefinitionAdapter
from .v2_work_queue import GitHubV2WorkQueue
from .work_state import PostgreSQLWorkStateStore

_WAIT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class AutonomousCommandResult:
    returncode: int
    output: str

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class AutonomousSubprocessRunner:
    """shellを介さずWorkspaceを既定cwdとしてcommandを実行する。"""

    def __init__(self, root: Path, environment: Mapping[str, str]) -> None:
        self._root = root
        self._environment = dict(environment)

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> AutonomousCommandResult:
        try:
            completed = subprocess.run(
                tuple(command),
                cwd=cwd or self._root,
                env=dict(environment) if environment is not None else self._environment,
                stdin=subprocess.DEVNULL,
                capture_output=capture_output,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return AutonomousCommandResult(124, "")
        except OSError:
            return AutonomousCommandResult(127, "")
        return AutonomousCommandResult(completed.returncode, completed.stdout or "")


class TextCommandAdapter:
    """typed result runnerをGitHub read adapterのtext runnerへ変換する。"""

    def __init__(
        self,
        runner: AutonomousSubprocessRunner,
        root: Path,
        environment: Mapping[str, str],
    ) -> None:
        self._runner = runner
        self._root = root
        self._environment = dict(environment)

    def run(self, args: Sequence[str]) -> str:
        result = self._runner.run(
            args,
            cwd=self._root,
            environment=self._environment,
            timeout_seconds=180,
        )
        if not result.succeeded:
            raise subprocess.CalledProcessError(result.returncode, tuple(args))
        return result.output


def run_autonomous(
    *,
    settings: LoopEngineeringSettings,
    environment: Mapping[str, str],
    database: PostgreSQLCommandAdapter,
    max_iterations: int,
    continuous: bool,
) -> int:
    registration = _registration(settings, environment)
    if registration is None:
        return _print_blocked("V2_GOAL_DEFINITION_INVALID")
    if settings.local_llm_coder is None:
        return _print_blocked("V2_LOCAL_LLM_CODER_REQUIRED")

    root = settings.workspace_path
    runner = AutonomousSubprocessRunner(root, environment)
    text_runner = TextCommandAdapter(runner, root, environment)

    runtime = PostgreSQLAutonomousRuntimeStore(database)
    bootstrap_state = PostgreSQLBootstrapStateStore(database)
    projection = GitHubPlanningProjectionAdapter(text_runner, bootstrap_state)
    bootstrap_service = V2GoalBootstrapService(SingleWorkGoalPlanner(), projection)
    bootstrap = DurableGoalBootstrap(runtime, bootstrap_service, projection)

    execution_state = V2ExecutionStateStore(database)
    work_state = PostgreSQLWorkStateStore(database)
    definitions = GitHubWorkDefinitionAdapter(text_runner, settings.engine.project_number)
    queue = GitHubV2WorkQueue(text_runner, definitions, execution_state, work_state)
    lineage_observer = GitHubAutonomousLineageObserver(text_runner)

    local_store = PostgreSQLLocalQualityStore(database)
    external_store = PostgreSQLExternalReviewStore(database)
    evidence = EvidenceEnricher(
        local_store,
        external_store,
        GitHubExactHeadCIAdapter(runner, environment),
        GitHubHumanVerificationAdapter(runner, environment),
    )

    implementer = build_implementer_backend(settings, runner, environment)
    local_reviewer = LocalLlmCoderReviewerAdapter(
        runner,
        settings.local_llm_coder,
        settings.workspace_path,
        environment,
        settings.local_llm_coder.model_profile,
    )
    local_quality = LocalQualityCoordinator(
        local_store,
        LocalVerificationRunner(runner, environment),
        local_reviewer,
        implementer,
    )
    external_review = ExternalReviewCoordinator(
        external_store,
        OpenAICompatibleExternalReviewer(runner, environment),
    )
    lineage = GitHubDevelopmentLineageEffects(
        runner,
        work_state,
        settings.workspace_path,
        environment,
    )
    transitions = V2AutonomousTransitionExecutor(
        implementer=implementer,
        materializer=TrustedProposalMaterializer(runner, environment),
        lineage=lineage,
        work_state=work_state,
        runner=runner,
        environment=environment,
        local_quality=local_quality,
        local_quality_state=local_store,
        external_review=external_review,
        external_review_state=external_store,
        review_levels=settings.review_levels,
        verification_commands=tuple(
            VerificationCommandDescriptor(
                item.identity,
                item.argv,
                item.working_directory,
                item.timeout_seconds,
                item.required,
            )
            for item in settings.verification_commands
        ),
        scope_paths=(".",),
    )
    application = V2AutonomousRunner(
        runtime,
        bootstrap,
        queue,
        lineage_observer,
        evidence,
        V2Supervisor(),
        transitions,
        goal_completion=GitHubGoalCompletion(text_runner, bootstrap_state),
    )

    while True:
        result = application.run(registration, max_iterations=max_iterations)
        print(
            json.dumps(
                {
                    "status": result.status.value,
                    "detail": result.detail,
                    "iterations": result.iterations,
                    "runtime_identity": result.runtime_identity,
                    "current_work_identity": result.current_work_identity,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if result.status is AutonomousRunStatus.GOAL_COMPLETED:
            return 0
        if result.status is AutonomousRunStatus.INTERVENTION_REQUIRED:
            return 3
        if not continuous:
            return 2 if result.status is AutonomousRunStatus.WAITING else 0
        if result.status is AutonomousRunStatus.WAITING:
            time.sleep(_WAIT_SECONDS)


def _registration(
    settings: LoopEngineeringSettings,
    environment: Mapping[str, str],
) -> ProductDevelopmentRegistration | None:
    raw_path = environment.get("LOOP_MISSION_GOAL_PATH", "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve(strict=False)
    identity = read_mission_goal_identity(path)
    if identity is None:
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    goal_text = _goal_text(content)
    acceptance = _acceptance_criteria(content)
    if not goal_text or not acceptance:
        return None
    revision = f"{identity.version}:{identity.generation}:{identity.sha256}"
    return ProductDevelopmentRegistration(
        product_key=settings.project_key,
        workspace_canonical_path=settings.workspace_path,
        repository_identity=settings.engine.repository,
        project_owner=settings.engine.owner,
        project_number=settings.engine.project_number,
        trunk_branch=settings.engine.trunk_branch,
        goal_definition_identity=f"mission-goal:{identity.sha256}",
        goal_revision=revision,
        goal_text=goal_text,
        acceptance_criteria=acceptance,
        work_branch_template=settings.engine.work_branch_template,
        ci_workflow_name=settings.engine.ci_workflow_name,
        initial_project_status="Backlog",
        human_verification_policy="WHEN_REQUIRED",
        self_improvement_target=(
            settings.engine.self_improvement.repository
            if settings.engine.self_improvement.enabled
            else None
        ),
    )


def _goal_text(content: str) -> str:
    lines = [
        line.rstrip()
        for line in content.splitlines()
        if not line.startswith("version: ") and not line.startswith("generation: ")
    ]
    value = "\n".join(lines).strip()
    return value[:200_000]


def _acceptance_criteria(content: str) -> tuple[str, ...]:
    checklist: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if lowered.startswith("- [ ] ") or lowered.startswith("- [x] "):
            value = line[6:].strip()
            if value:
                checklist.append(value)
    if checklist:
        return tuple(dict.fromkeys(checklist))

    headings = ("受入条件", "完了条件", "acceptance", "completion")
    active = False
    bullets: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            active = any(token in title for token in headings)
            continue
        if active and line.startswith("- "):
            value = line[2:].strip()
            if value:
                bullets.append(value)
    if bullets:
        return tuple(dict.fromkeys(bullets))
    return ("Mission Goal本文に記載された要求を満たす",)


def _print_blocked(detail: str) -> int:
    print(json.dumps({"status": "BLOCKED", "detail": detail}, sort_keys=True))
    return 3
