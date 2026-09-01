from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .config import LoopEngineeringSettings
from .host_runtime import HostTransitionResult, HostTransitionStatus
from .mission_goal import inject_mission_goal_environment
from .operational_config import inject_operational_store_environment
from .runtime_console import RuntimeConsole, VisibleSubprocessLocalRunner
from .v2_cli import (
    add_v2_arguments,
    legacy_host_block_reason,
    run_v2_command,
    v2_requested,
)

_CI_RECHECK_INITIAL_SECONDS = 60.0
_CI_RECHECK_MAX_SECONDS = 300.0
_PROJECT_RECHECK_INITIAL_SECONDS = 300.0
_PROJECT_RECHECK_MAX_SECONDS = 900.0
_MAX_IDENTICAL_COMPLETED = 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Loop EngineeringのMission実行系を起動する。")
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "--validate-installation",
        action="store_true",
        help="外部システムを観測・変更せずに制御系パッケージの導入状態を確認する。",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="CodexやGit mutationを開始せず、現在のHost能力だけを事前確認する。",
    )
    parser.add_argument(
        "--migrate-operational-store",
        action="store_true",
        help="設定されたPostgreSQLへ未適用のversioned SQL migrationを明示適用する。",
    )
    parser.add_argument(
        "--operational-state-check",
        action="store_true",
        help="ProductやGitHubを変更せず、Operational Storeのwrite/readbackだけを確認する。",
    )
    parser.add_argument(
        "--config",
        help="既定のconfig/loop-engineering.ini以外を使用する場合の設定ファイルpath。",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="継続実行ではなく、範囲を限定した遷移を1回だけ実行する。",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="永続実行ログに加えて、子プロセスの詳細出力を標準エラーにも表示する。",
    )
    add_v2_arguments(parser)
    arguments = parser.parse_args()
    if arguments.version:
        print("loop_engineering 1")
        return 0
    if arguments.validate_installation:
        print("LOOP_ENGINE_INSTALLATION=PASS")
        return 0

    platform_root = Path(__file__).resolve().parents[2]
    selected_config = Path(arguments.config) if arguments.config else None
    try:
        settings = LoopEngineeringSettings.load(
            platform_root,
            os.environ,
            config_path=selected_config,
        )
        environment = inject_operational_store_environment(
            settings.config_path,
            settings.runtime_environment(os.environ),
        )
    except ValueError as error:
        print(f"CONFIGURATION_INVALID: {error}")
        return 3

    workspace_root = settings.workspace_path
    environment = inject_mission_goal_environment(
        platform_root=platform_root,
        product_root=workspace_root,
        repository=settings.engine.repository,
        environment=environment,
    )

    if v2_requested(arguments):
        if any(
            (
                arguments.preflight,
                arguments.migrate_operational_store,
                arguments.operational_state_check,
                arguments.once,
            )
        ):
            print(json.dumps({"status": "BLOCKED", "detail": "V2_COMMAND_CONFLICT"}))
            return 3
        v2_exit = run_v2_command(
            arguments,
            settings=settings,
            environment=environment,
        )
        if v2_exit is not None:
            return v2_exit

    if arguments.migrate_operational_store:
        from .postgres_runtime import PostgreSQLCommandAdapter
        from .preflight import SubprocessCommandRunner

        migration_result = PostgreSQLCommandAdapter(
            SubprocessCommandRunner(),
            environment,
        ).apply_migrations()
        applied = ",".join(migration_result.applied) if migration_result.applied else "なし"
        print(
            "OPERATIONAL_STORE_MIGRATION="
            f"{'PASS' if migration_result.succeeded else 'FAIL'} "
            f"detail={migration_result.detail} applied={applied}"
        )
        return 0 if migration_result.succeeded else 3

    if arguments.operational_state_check:
        from .operational_state_check import check_operational_state_round_trip
        from .postgres_runtime import PostgreSQLCommandAdapter
        from .preflight import SubprocessCommandRunner

        check_result = check_operational_state_round_trip(
            PostgreSQLCommandAdapter(SubprocessCommandRunner(), environment),
            project_key=settings.project_key,
            repository=settings.engine.repository,
        )
        print(
            "OPERATIONAL_STATE_CHECK="
            f"{'PASS' if check_result.succeeded else 'FAIL'} "
            f"detail={check_result.detail}"
        )
        return 0 if check_result.succeeded else 3

    if arguments.preflight:
        from .preflight import (
            EnvironmentCapabilityPreflight,
            PreflightStatus,
            SubprocessCommandRunner,
        )

        preflight_result = EnvironmentCapabilityPreflight(
            settings.engine,
            SubprocessCommandRunner(),
            environment,
            project_root=workspace_root,
        ).run()
        print(f"MISSION_GOAL_PATH = {environment.get('LOOP_MISSION_GOAL_PATH', '')}")
        print(preflight_result.as_json())
        return 3 if preflight_result.status is PreflightStatus.BLOCKED else 0

    legacy_block = legacy_host_block_reason(settings=settings, environment=environment)
    if legacy_block is not None:
        print(json.dumps({"status": "BLOCKED", "detail": legacy_block}, sort_keys=True))
        return 3

    from .durable_host_entrypoint import run_durable_actual_host_transition

    console = RuntimeConsole(platform_root, verbose=arguments.verbose)
    runner = VisibleSubprocessLocalRunner(console)
    mode = "once" if arguments.once else "continuous"
    mode_label = "1回実行" if arguments.once else "継続実行"
    console.event(f"Start: mode={mode}（{mode_label}） project={settings.project_key}")
    console.event(f"Config: {settings.config_path}")
    console.event(f"Target Workspace: {workspace_root}")
    console.event(f"Mission Goal: {environment['LOOP_MISSION_GOAL_PATH']}")
    console.event(f"Log: {console.path}")

    ci_wait_seconds = _CI_RECHECK_INITIAL_SECONDS
    project_wait_seconds = _PROJECT_RECHECK_INITIAL_SECONDS
    previous_completed: tuple[str, int | None, int | None, str | None] | None = None
    identical_completed = 0
    transition_number = 0

    try:
        while True:
            transition_number += 1
            console.event(f"Transition {transition_number}: Start")
            transition_result = run_durable_actual_host_transition(
                root=workspace_root,
                environment=environment,
                local_runner=runner,
                config=settings.engine,
                project_key=settings.project_key,
            )
            console.event(
                f"Transition {transition_number}: "
                f"{transition_result.status.value} detail={transition_result.detail}"
            )

            if arguments.once:
                print(transition_result.as_json())
                return _exit_code(transition_result)

            if transition_result.status is HostTransitionStatus.COMPLETED:
                completed_key = (
                    transition_result.detail,
                    transition_result.work_issue,
                    transition_result.pr_number,
                    transition_result.head_sha,
                )
                if completed_key == previous_completed:
                    identical_completed += 1
                else:
                    previous_completed = completed_key
                    identical_completed = 1
                if identical_completed >= _MAX_IDENTICAL_COMPLETED:
                    blocked = HostTransitionResult(
                        HostTransitionStatus.INTERVENTION_REQUIRED,
                        "NO_PROGRESS_GUARD",
                        transition_result.work_issue,
                        transition_result.pr_number,
                        transition_result.head_sha,
                    )
                    console.event("Progress Guard: 同一の完了遷移が繰り返されました")
                    print(blocked.as_json())
                    return 3
                ci_wait_seconds = _CI_RECHECK_INITIAL_SECONDS
                project_wait_seconds = _PROJECT_RECHECK_INITIAL_SECONDS
                console.event("Continue: 現在状態をfresh observeします")
                continue

            previous_completed = None
            identical_completed = 0

            if (
                transition_result.status is HostTransitionStatus.YIELD_EXTERNAL
                and transition_result.detail in {"CI_PENDING", "REVIEW_PENDING"}
            ):
                wait_kind = "CI" if transition_result.detail == "CI_PENDING" else "Review"
                console.event(
                    f"{wait_kind} Wait: {int(ci_wait_seconds)}秒後に自動再開します"
                )
                time.sleep(ci_wait_seconds)
                ci_wait_seconds = min(
                    ci_wait_seconds * 2,
                    _CI_RECHECK_MAX_SECONDS,
                )
                continue

            if (
                transition_result.status is HostTransitionStatus.YIELD_EXTERNAL
                and transition_result.detail == "GITHUB_PROJECT_RATE_LIMIT"
            ):
                console.event(
                    "External Wait: GitHub Project API rate limitのため"
                    f"{int(project_wait_seconds)}秒後にfresh readから自動再開します"
                )
                time.sleep(project_wait_seconds)
                project_wait_seconds = min(
                    project_wait_seconds * 2,
                    _PROJECT_RECHECK_MAX_SECONDS,
                )
                continue

            print(transition_result.as_json())
            return _exit_code(transition_result)
    except KeyboardInterrupt:
        console.event("Operator Stop: 操作者の要求により停止します")
        return 130


def _exit_code(result: HostTransitionResult) -> int:
    if result.status is HostTransitionStatus.COMPLETED:
        return 0
    if result.status is HostTransitionStatus.YIELD_EXTERNAL:
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
