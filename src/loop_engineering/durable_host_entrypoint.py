"""Operational Stateを伴う実ホスト遷移入口。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from . import host_entrypoint
from .config import LoopEngineConfig
from .host_entrypoint import (
    PilotAwareMissionPort,
    PilotPlanningImplementer,
    ReconciliationAwareHostLoopController,
    StrictGhMissionPort,
)
from .host_runtime import HostTransitionResult, HostTransitionStatus, LocalRunner, SubprocessLocalRunner
from .mission_goal import inject_mission_goal_environment
from .postgres_runtime import PostgreSQLCommandAdapter
from .preflight import EnvironmentCapabilityPreflight, PreflightStatus, SubprocessCommandRunner
from .runtime_operational_state import (
    DurableHostTransitionCoordinator,
    PostgreSQLRuntimeOperationalStore,
)


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
    required = base_values.get("LOOP_OPERATIONAL_STORE_REQUIRED", "false").strip().lower() == "true"
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
    preflight = EnvironmentCapabilityPreflight(
        resolved_config,
        SubprocessCommandRunner(),
        values,
        project_root=project_root,
    ).run()
    if preflight.status is PreflightStatus.BLOCKED:
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

    strict = StrictGhMissionPort(resolved_config, runner, values)
    mission = PilotAwareMissionPort(resolved_config, strict)
    implementer = PilotPlanningImplementer(
        resolved_config,
        runner,
        project_root,
        values,
        argv_prefix,
    )
    controller = ReconciliationAwareHostLoopController(
        resolved_config,
        mission,
        implementer,
    )
    database = PostgreSQLCommandAdapter(SubprocessCommandRunner(), values)
    store = PostgreSQLRuntimeOperationalStore(database)
    return DurableHostTransitionCoordinator(
        project_key=project_key or resolved_config.repository,
        repository=resolved_config.repository,
        mission=mission,
        controller=controller,
        store=store,
        required=required,
    ).run_once()
