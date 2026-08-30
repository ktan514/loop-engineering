"""Operational Stateを伴う実ホスト遷移入口。"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from . import host_entrypoint
from .actual_host_merge_safety import (
    ReviewAwareHostLoopController,
    SafeActualHostMissionPort,
)
from .config import LoopEngineConfig
from .host_entrypoint import PilotAwareMissionPort, PilotPlanningImplementer
from .host_runtime import (
    HostTransitionResult,
    HostTransitionStatus,
    LocalRunner,
    SubprocessLocalRunner,
)
from .mission_goal import inject_mission_goal_environment
from .postgres_runtime import PostgreSQLCommandAdapter
from .preflight import (
    EnvironmentCapabilityPreflight,
    PreflightResult,
    PreflightStatus,
    SubprocessCommandRunner,
)
from .runtime_operational_state import (
    DurableHostTransitionCoordinator,
    OperationalStateUnavailable,
    PostgreSQLRuntimeOperationalStore,
)

_PROJECT_RATE_LIMIT_DIAGNOSTIC = "GITHUB_PROJECT_RATE_LIMITED"
_PROJECT_BLOCKERS = {"GITHUB_PROJECT_READ", "GITHUB_PROJECT_WRITE"}


class PreflightWaitStore(Protocol):
    """Preflightで外部待ちになった事実だけを永続化する最小Port。"""

    def begin_run(self, run_identity: str, project_key: str, repository: str) -> None: ...

    def record_transition(
        self,
        run_identity: str,
        sequence_number: int,
        result: HostTransitionResult,
    ) -> None: ...

    def record_external_wait(self, run_identity: str, result: HostTransitionResult) -> None: ...

    def finish_run(self, run_identity: str, status: str) -> None: ...


def run_durable_actual_host_transition(
    *,
    root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    local_runner: LocalRunner | None = None,
    config: LoopEngineConfig | None = None,
    project_key: str | None = None,
) -> HostTransitionResult:
    """DB設定済みならdurable coordinatorを通し、未設定なら従来入口へ委譲する。"""
    base_values = dict(environment or os.environ)
    required = (
        base_values.get("LOOP_OPERATIONAL_STORE_REQUIRED", "false").strip().lower()
        == "true"
    )
    database_configured = bool(
        base_values.get("LOOP_POSTGRES_DSN", "").strip()
        or base_values.get("LOOP_DATABASE_URL", "").strip()
    )
    if not required and not database_configured:
        return host_entrypoint.run_actual_host_transition(
            root=root,
            environment=base_values,
            local_runner=local_runner,
            config=config,
        )

    project_root = root or Path(__file__).resolve().parents[2]
    try:
        resolved_config = config or LoopEngineConfig.from_environment(base_values)
    except ValueError:
        return HostTransitionResult(
            HostTransitionStatus.INTERVENTION_REQUIRED,
            "CONFIGURATION_INVALID",
        )

    values = inject_mission_goal_environment(
        platform_root=Path(__file__).resolve().parents[2],
        product_root=project_root,
        repository=resolved_config.repository,
        environment=base_values,
    )
    database = PostgreSQLCommandAdapter(SubprocessCommandRunner(), values)
    store = PostgreSQLRuntimeOperationalStore(database)
    resolved_project_key = project_key or resolved_config.repository

    preflight = EnvironmentCapabilityPreflight(
        resolved_config,
        SubprocessCommandRunner(),
        values,
        project_root=project_root,
    ).run()
    if preflight.status is PreflightStatus.BLOCKED:
        if _project_rate_limit_is_only_blocker(preflight):
            wait_result = HostTransitionResult(
                HostTransitionStatus.YIELD_EXTERNAL,
                "GITHUB_PROJECT_RATE_LIMIT",
            )
            if not _record_preflight_external_wait(
                store,
                project_key=resolved_project_key,
                repository=resolved_config.repository,
                result=wait_result,
                required=required,
            ):
                return HostTransitionResult(
                    HostTransitionStatus.INTERVENTION_REQUIRED,
                    "OPERATIONAL_STORE_UNAVAILABLE",
                )
            return wait_result
        return HostTransitionResult(
            HostTransitionStatus.INTERVENTION_REQUIRED,
            "PREFLIGHT_BLOCKED:" + ",".join(preflight.blocking_for_loop_bootstrap),
        )

    runner = local_runner or SubprocessLocalRunner()
    try:
        argv_prefix = host_entrypoint._codex_argv(values)
    except ValueError:
        return HostTransitionResult(
            HostTransitionStatus.INTERVENTION_REQUIRED,
            "CODEX_COMMAND_INVALID",
        )

    strict = SafeActualHostMissionPort(resolved_config, runner, values)
    mission = PilotAwareMissionPort(resolved_config, strict)
    implementer = PilotPlanningImplementer(
        resolved_config,
        runner,
        project_root,
        values,
        argv_prefix,
    )
    controller = ReviewAwareHostLoopController(
        resolved_config,
        mission,
        implementer,
        strict,
    )
    return DurableHostTransitionCoordinator(
        project_key=resolved_project_key,
        repository=resolved_config.repository,
        mission=mission,
        controller=controller,
        store=store,
        required=required,
    ).run_once()


def _project_rate_limit_is_only_blocker(preflight: PreflightResult) -> bool:
    blockers = set(preflight.blocking_for_loop_bootstrap)
    return (
        _PROJECT_RATE_LIMIT_DIAGNOSTIC in preflight.diagnostics
        and bool(blockers)
        and blockers <= _PROJECT_BLOCKERS
    )


def _record_preflight_external_wait(
    store: PreflightWaitStore,
    *,
    project_key: str,
    repository: str,
    result: HostTransitionResult,
    required: bool,
) -> bool:
    run_identity = uuid.uuid4().hex
    try:
        store.begin_run(run_identity, project_key, repository)
        store.record_transition(run_identity, 1, result)
        store.record_external_wait(run_identity, result)
        store.finish_run(run_identity, result.status.value)
    except OperationalStateUnavailable:
        return not required
    return True
