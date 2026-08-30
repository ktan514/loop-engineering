from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .mission_goal import inject_mission_goal_environment


class SecretProvider(Protocol):
    def github_token(self) -> str: ...


class GitHubCredentialUnavailable(RuntimeError):
    """GitHub認証情報を意図的に利用できない状態。"""


class EnvironmentSecretProvider:
    """ホストプロセス環境へ既に注入された認証情報だけを読み取る。"""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = values

    def github_token(self) -> str:
        try:
            return self._required("GH_TOKEN", "GitHub認証情報を利用できません")
        except RuntimeError as error:
            raise GitHubCredentialUnavailable(str(error)) from error

    def _required(self, name: str, message: str) -> str:
        value = self._values.get(name, "")
        if not value:
            raise RuntimeError(message)
        return value


@dataclass(frozen=True, slots=True)
class LaunchEnvironment:
    values: Mapping[str, str]


_NON_SECRET_LOOP_KEYS = (
    "LOOP_REPOSITORY",
    "LOOP_PROJECT_OWNER",
    "LOOP_PROJECT_NUMBER",
    "LOOP_MISSION_ISSUE",
    "LOOP_LABEL",
    "LOOP_TRUNK_BRANCH",
    "LOOP_AUTHORITY_REFS",
    "LOOP_IMPROVEMENT_AREA",
    "LOOP_ISSUE_LEVEL",
    "LOOP_ROOT_ISSUE",
    "LOOP_PARENT_ISSUE",
    "LOOP_INTEGRATION_WORK",
    "LOOP_CI_WORKFLOW_NAME",
    "LOOP_TRUSTED_REVIEWER_SOCKET",
    "LOOP_CODEX_COMMAND_JSON",
)
_GOAL_KEYS = (
    "LOOP_MISSION_GOAL_PATH",
    "CODEX_MISSION_GOAL_VERSION",
    "CODEX_MISSION_GOAL_GENERATION",
    "CODEX_MISSION_GOAL_SHA256",
)


def build_launch_environment(
    root: Path,
    secrets: SecretProvider,
    parent: Mapping[str, str],
) -> LaunchEnvironment:
    platform_root = Path(__file__).resolve().parents[2]
    goal_environment = inject_mission_goal_environment(
        platform_root=platform_root,
        product_root=root,
        repository=parent.get("LOOP_REPOSITORY", ""),
        environment=parent,
    )
    if not all(goal_environment.get(name) for name in _GOAL_KEYS):
        raise ValueError("Mission Goalを信頼済み参照元から解決できません")

    path = parent.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    values: dict[str, str] = {
        "PATH": path,
        "GH_TOKEN": secrets.github_token(),
    }
    for name in (*_NON_SECRET_LOOP_KEYS, *_GOAL_KEYS):
        value = goal_environment.get(name)
        if value:
            values[name] = value
    return LaunchEnvironment(values)


def launch_vscode(root: Path, environment: LaunchEnvironment) -> None:
    subprocess.run(
        ("code", str(root)),
        check=True,
        env=dict(environment.values),
        timeout=30,
    )
