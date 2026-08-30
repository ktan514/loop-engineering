"""PostgreSQL Operational Storeの非秘密Host設定。"""

from __future__ import annotations

from collections.abc import Mapping
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class OperationalStoreSettings:
    required: bool = False
    driver: str = "host"
    docker_container: str | None = None
    migration_policy: str = "required"

    def __post_init__(self) -> None:
        if self.driver not in {"host", "docker"}:
            raise ValueError("operational_store.driverはhostまたはdockerを指定してください")
        if self.driver == "docker" and not self.docker_container:
            raise ValueError("docker方式にはoperational_store.docker_containerが必要です")
        if self.migration_policy not in {"required", "ignore"}:
            raise ValueError(
                "operational_store.migration_policyはrequiredまたはignoreを指定してください"
            )


def load_operational_store_settings(config_path: Path) -> OperationalStoreSettings:
    parser = ConfigParser(interpolation=None)
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError) as error:
        raise ValueError("設定ファイルを読み取れません") from error

    if not parser.has_section("operational_store"):
        return OperationalStoreSettings()
    section = parser["operational_store"]
    container = section.get("docker_container", "").strip() or None
    return OperationalStoreSettings(
        required=section.getboolean("required", fallback=False),
        driver=section.get("driver", "host").strip() or "host",
        docker_container=container,
        migration_policy=section.get("migration_policy", "required").strip() or "required",
    )


def inject_operational_store_environment(
    config_path: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    settings = load_operational_store_settings(config_path)
    values = dict(environment)
    values["LOOP_OPERATIONAL_STORE_REQUIRED"] = "true" if settings.required else "false"
    values["LOOP_POSTGRES_DRIVER"] = settings.driver
    values["LOOP_POSTGRES_MIGRATION_POLICY"] = settings.migration_policy
    if settings.docker_container is None:
        values.pop("LOOP_POSTGRES_CONTAINER", None)
    else:
        values["LOOP_POSTGRES_CONTAINER"] = settings.docker_container
    return values
