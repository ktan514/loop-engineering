from pathlib import Path

import pytest

from loop_engineering.operational_config import (
    inject_operational_store_environment,
    load_operational_store_settings,
)


def _write_config(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_docker_required_settings_are_injected_without_dsn_value(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(
        config,
        """[operational_store]\n"
        "dsn_env = LOOP_POSTGRES_DSN\n"
        "required = true\n"
        "driver = docker\n"
        "docker_container = local-postgres\n"
        "migration_policy = required\n""",
    )

    settings = load_operational_store_settings(config)
    values = inject_operational_store_environment(config, {"LOOP_POSTGRES_DSN": "secret-dsn"})

    assert settings.required
    assert settings.driver == "docker"
    assert settings.docker_container == "local-postgres"
    assert values["LOOP_OPERATIONAL_STORE_REQUIRED"] == "true"
    assert values["LOOP_POSTGRES_DRIVER"] == "docker"
    assert values["LOOP_POSTGRES_CONTAINER"] == "local-postgres"
    assert values["LOOP_POSTGRES_DSN"] == "secret-dsn"


def test_optional_host_is_backward_compatible_default(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, "[operational_store]\ndsn_env = LOOP_POSTGRES_DSN\n")

    settings = load_operational_store_settings(config)

    assert not settings.required
    assert settings.driver == "host"
    assert settings.docker_container is None
    assert settings.migration_policy == "required"


def test_docker_driver_requires_explicit_container(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, "[operational_store]\ndriver = docker\n")

    with pytest.raises(ValueError, match="docker_container"):
        load_operational_store_settings(config)


def test_invalid_migration_policy_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(
        config,
        "[operational_store]\nmigration_policy = auto-mutate\n",
    )

    with pytest.raises(ValueError, match="migration_policy"):
        load_operational_store_settings(config)
