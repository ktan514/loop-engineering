"""Mission Goalの信頼済み参照元をProduct Workspaceから分離する。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MissionGoalIdentity:
    path: Path
    version: str
    generation: str
    sha256: str


def resolve_mission_goal_path(
    *,
    platform_root: Path,
    product_root: Path,
    repository: str,
    environment: Mapping[str, str],
) -> Path:
    explicit = environment.get("LOOP_MISSION_GOAL_PATH", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = platform_root / path
        return path.resolve(strict=False)

    if "/" in repository:
        owner, name = repository.split("/", 1)
        registry = platform_root / "config" / "goals" / f"{owner}__{name}.md"
        if registry.is_file():
            return registry.resolve(strict=False)

    return (product_root / "docs" / "operations" / "loop_mission_goal.md").resolve(
        strict=False
    )


def read_mission_goal_identity(path: Path) -> MissionGoalIdentity | None:
    if not path.is_file():
        return None
    try:
        content = path.read_bytes()
        lines = content.decode("utf-8").splitlines()
    except (OSError, UnicodeError):
        return None

    version = next(
        (line.removeprefix("version: ").strip() for line in lines if line.startswith("version: ")),
        "",
    )
    generation = next(
        (
            line.removeprefix("generation: ").strip()
            for line in lines
            if line.startswith("generation: ")
        ),
        "",
    )
    if not version or not generation:
        return None
    return MissionGoalIdentity(
        path=path.resolve(strict=False),
        version=version,
        generation=generation,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def inject_mission_goal_environment(
    *,
    platform_root: Path,
    product_root: Path,
    repository: str,
    environment: Mapping[str, str],
) -> dict[str, str]:
    values = dict(environment)
    path = resolve_mission_goal_path(
        platform_root=platform_root,
        product_root=product_root,
        repository=repository,
        environment=values,
    )
    values["LOOP_MISSION_GOAL_PATH"] = str(path)
    identity = read_mission_goal_identity(path)
    if identity is None:
        values.pop("CODEX_MISSION_GOAL_VERSION", None)
        values.pop("CODEX_MISSION_GOAL_GENERATION", None)
        values.pop("CODEX_MISSION_GOAL_SHA256", None)
        return values

    values["CODEX_MISSION_GOAL_VERSION"] = identity.version
    values["CODEX_MISSION_GOAL_GENERATION"] = identity.generation
    values["CODEX_MISSION_GOAL_SHA256"] = identity.sha256
    return values
