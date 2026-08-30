from collections.abc import Mapping, Sequence
from pathlib import Path

from loop_engineering.mission_goal import inject_mission_goal_environment
from loop_engineering.preflight import CommandResult, EnvironmentCapabilityPreflight

from .conftest import config


class UnusedRunner:
    def run(
        self,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        del command, environment
        return CommandResult(True)


def _write_goal(path: Path, *, version: str = "5", generation: str = "1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Mission\n\nversion: {version}\ngeneration: {generation}\n",
        encoding="utf-8",
    )


def test_host_registry_is_used_when_product_has_no_legacy_goal(tmp_path: Path) -> None:
    platform_root = tmp_path / "platform"
    product_root = tmp_path / "product"
    product_root.mkdir()
    registry = platform_root / "config" / "goals" / "ktan514__ai-liver-yura.md"
    _write_goal(registry)

    values = inject_mission_goal_environment(
        platform_root=platform_root,
        product_root=product_root,
        repository="ktan514/ai-liver-yura",
        environment={},
    )

    assert values["LOOP_MISSION_GOAL_PATH"] == str(registry.resolve())
    assert values["CODEX_MISSION_GOAL_VERSION"] == "5"
    assert values["CODEX_MISSION_GOAL_GENERATION"] == "1"
    assert len(values["CODEX_MISSION_GOAL_SHA256"]) == 64


def test_explicit_missing_goal_fails_closed_without_registry_fallback(tmp_path: Path) -> None:
    platform_root = tmp_path / "platform"
    product_root = tmp_path / "product"
    product_root.mkdir()
    registry = platform_root / "config" / "goals" / "ktan514__ai-liver-yura.md"
    _write_goal(registry)
    missing = tmp_path / "missing-goal.md"

    values = inject_mission_goal_environment(
        platform_root=platform_root,
        product_root=product_root,
        repository="ktan514/ai-liver-yura",
        environment={"LOOP_MISSION_GOAL_PATH": str(missing)},
    )

    assert values["LOOP_MISSION_GOAL_PATH"] == str(missing.resolve())
    assert "CODEX_MISSION_GOAL_VERSION" not in values
    assert "CODEX_MISSION_GOAL_GENERATION" not in values
    assert "CODEX_MISSION_GOAL_SHA256" not in values


def test_legacy_product_goal_remains_compatible_without_host_registry(tmp_path: Path) -> None:
    platform_root = tmp_path / "platform"
    product_root = tmp_path / "product"
    legacy = product_root / "docs" / "operations" / "loop_mission_goal.md"
    _write_goal(legacy, version="4", generation="9")

    values = inject_mission_goal_environment(
        platform_root=platform_root,
        product_root=product_root,
        repository="owner/product",
        environment={},
    )

    assert values["LOOP_MISSION_GOAL_PATH"] == str(legacy.resolve())
    assert values["CODEX_MISSION_GOAL_VERSION"] == "4"
    assert values["CODEX_MISSION_GOAL_GENERATION"] == "9"


def test_preflight_accepts_injected_host_goal_without_product_goal(tmp_path: Path) -> None:
    platform_root = tmp_path / "platform"
    product_root = tmp_path / "product"
    product_root.mkdir()
    registry = platform_root / "config" / "goals" / "ktan514__ai-liver-yura.md"
    _write_goal(registry)
    values = inject_mission_goal_environment(
        platform_root=platform_root,
        product_root=product_root,
        repository="ktan514/ai-liver-yura",
        environment={},
    )

    preflight = EnvironmentCapabilityPreflight(
        config(),
        UnusedRunner(),
        values,
        project_root=product_root,
    )

    assert preflight._mission_goal_matches()


def test_preflight_rejects_stale_goal_identity(tmp_path: Path) -> None:
    platform_root = tmp_path / "platform"
    product_root = tmp_path / "product"
    product_root.mkdir()
    registry = platform_root / "config" / "goals" / "ktan514__ai-liver-yura.md"
    _write_goal(registry)
    values = inject_mission_goal_environment(
        platform_root=platform_root,
        product_root=product_root,
        repository="ktan514/ai-liver-yura",
        environment={},
    )
    values["CODEX_MISSION_GOAL_SHA256"] = "stale"

    preflight = EnvironmentCapabilityPreflight(
        config(),
        UnusedRunner(),
        values,
        project_root=product_root,
    )

    assert not preflight._mission_goal_matches()
