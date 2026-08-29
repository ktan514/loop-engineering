"""Loop Engineeringの設定ファイルと秘密情報参照契約。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from configparser import ConfigParser, SectionProxy
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LoopEngineConfig:
    """Product固有identityをCoreから分離する非秘密設定。"""

    repository: str
    owner: str
    project_number: int
    mission_issue: int
    label: str = "loop-engineering"
    trunk_branch: str = "main"
    authority_refs: tuple[str, ...] = ()
    improvement_area: str = "Runtime / Infrastructure"
    issue_level: str = "Work"
    root_issue: int | None = None
    parent_issue: int | None = None
    integration_work: int | None = None
    ci_workflow_name: str = "Deterministic CI"

    def __post_init__(self) -> None:
        text_fields = (
            ("repository", self.repository),
            ("owner", self.owner),
            ("label", self.label),
            ("trunk_branch", self.trunk_branch),
            ("improvement_area", self.improvement_area),
            ("issue_level", self.issue_level),
            ("ci_workflow_name", self.ci_workflow_name),
        )
        for name, value in text_fields:
            if not value.strip():
                raise ValueError(f"{name}を空文字にはできません")
        if "/" not in self.repository:
            raise ValueError("repositoryはowner/name形式で指定してください")
        if self.project_number < 1:
            raise ValueError("project_numberは1以上である必要があります")
        if self.mission_issue < 1:
            raise ValueError("mission_issueは1以上である必要があります")
        for name, value in (
            ("root_issue", self.root_issue),
            ("parent_issue", self.parent_issue),
            ("integration_work", self.integration_work),
        ):
            if value is not None and value < 1:
                raise ValueError(f"{name}は1以上である必要があります")
        if any(not item.strip() for item in self.authority_refs):
            raise ValueError("authority_refsに空文字は指定できません")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "LoopEngineConfig":
        """設定loaderが生成した内部環境を既存Host境界へ受け渡す互換入口。"""

        repository = _required_mapping(environment, "LOOP_REPOSITORY")
        owner = environment.get("LOOP_PROJECT_OWNER", "").strip() or repository.split("/", 1)[0]
        return cls(
            repository=repository,
            owner=owner,
            project_number=_required_int_mapping(environment, "LOOP_PROJECT_NUMBER"),
            mission_issue=_required_int_mapping(environment, "LOOP_MISSION_ISSUE"),
            label=environment.get("LOOP_LABEL", "loop-engineering").strip()
            or "loop-engineering",
            trunk_branch=environment.get("LOOP_TRUNK_BRANCH", "main").strip() or "main",
            authority_refs=_csv(environment.get("LOOP_AUTHORITY_REFS", "")),
            improvement_area=environment.get(
                "LOOP_IMPROVEMENT_AREA", "Runtime / Infrastructure"
            ).strip()
            or "Runtime / Infrastructure",
            issue_level=environment.get("LOOP_ISSUE_LEVEL", "Work").strip() or "Work",
            root_issue=_optional_int_mapping(environment, "LOOP_ROOT_ISSUE"),
            parent_issue=_optional_int_mapping(environment, "LOOP_PARENT_ISSUE"),
            integration_work=_optional_int_mapping(environment, "LOOP_INTEGRATION_WORK"),
            ci_workflow_name=environment.get(
                "LOOP_CI_WORKFLOW_NAME", "Deterministic CI"
            ).strip()
            or "Deterministic CI",
        )


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """モデルとAPIの非秘密設定。"""

    implementer_provider: str
    implementer_model: str
    reviewer_provider: str
    reviewer_model: str
    reviewer_api_base: str

    def __post_init__(self) -> None:
        for name, value in (
            ("implementer_provider", self.implementer_provider),
            ("implementer_model", self.implementer_model),
            ("reviewer_provider", self.reviewer_provider),
            ("reviewer_model", self.reviewer_model),
            ("reviewer_api_base", self.reviewer_api_base),
        ):
            if not value.strip():
                raise ValueError(f"{name}を空文字にはできません")


@dataclass(frozen=True, slots=True)
class SecretReferenceConfig:
    """秘密値そのものではなく、値を保持する環境変数名だけを持つ。"""

    github_token_env: str
    reviewer_api_key_env: str
    operational_store_dsn_env: str
    trusted_reviewer_socket_env: str

    def __post_init__(self) -> None:
        for name, value in (
            ("github_token_env", self.github_token_env),
            ("reviewer_api_key_env", self.reviewer_api_key_env),
            ("operational_store_dsn_env", self.operational_store_dsn_env),
            ("trusted_reviewer_socket_env", self.trusted_reviewer_socket_env),
        ):
            _validate_env_name(name, value)


@dataclass(frozen=True, slots=True)
class LoopEngineeringSettings:
    """1つのProduct Workspaceを実行するためのホスト設定。"""

    config_path: Path
    project_key: str
    workspace_path: Path
    engine: LoopEngineConfig
    models: ModelConfig
    secrets: SecretReferenceConfig

    @classmethod
    def load(
        cls,
        platform_root: Path,
        environment: Mapping[str, str] | None = None,
        *,
        config_path: Path | None = None,
    ) -> "LoopEngineeringSettings":
        values = environment if environment is not None else os.environ
        selected = config_path or _configured_path(platform_root, values)
        selected = selected.expanduser()
        if not selected.is_absolute():
            selected = platform_root / selected
        selected = selected.resolve(strict=False)
        if not selected.is_file():
            raise ValueError(f"設定ファイルが見つかりません: {selected}")

        parser = ConfigParser(interpolation=None)
        try:
            with selected.open("r", encoding="utf-8") as stream:
                parser.read_file(stream)
        except (OSError, UnicodeError) as error:
            raise ValueError("設定ファイルを読み取れません") from error

        project = _section(parser, "project")
        models = _section(parser, "models")
        credentials = _section(parser, "credentials")
        operational_store = _section(parser, "operational_store")
        runtime = _section(parser, "runtime")

        repository = _required(project, "repository")
        owner = project.get("project_owner", "").strip() or repository.split("/", 1)[0]
        workspace = Path(_required(project, "workspace_path")).expanduser()
        if not workspace.is_absolute():
            raise ValueError("workspace_pathは絶対pathで指定してください")
        workspace = workspace.resolve(strict=False)

        engine = LoopEngineConfig(
            repository=repository,
            owner=owner,
            project_number=_required_int_section(project, "project_number"),
            mission_issue=_required_int_section(project, "mission_issue"),
            label=project.get("label", "loop-engineering").strip() or "loop-engineering",
            trunk_branch=project.get("trunk_branch", "main").strip() or "main",
            authority_refs=_csv(project.get("authority_refs", "")),
            improvement_area=(
                project.get("improvement_area", "Runtime / Infrastructure").strip()
                or "Runtime / Infrastructure"
            ),
            issue_level=project.get("issue_level", "Work").strip() or "Work",
            root_issue=_optional_int_section(project, "root_issue"),
            parent_issue=_optional_int_section(project, "parent_issue"),
            integration_work=_optional_int_section(project, "integration_work"),
            ci_workflow_name=(
                project.get("ci_workflow_name", "Deterministic CI").strip()
                or "Deterministic CI"
            ),
        )
        model_config = ModelConfig(
            implementer_provider=models.get("implementer_provider", "codex").strip() or "codex",
            implementer_model=models.get("implementer_model", "default").strip() or "default",
            reviewer_provider=models.get("reviewer_provider", "openai").strip() or "openai",
            reviewer_model=_required(models, "reviewer_model"),
            reviewer_api_base=(
                models.get("reviewer_api_base", "https://api.openai.com/v1").strip()
                or "https://api.openai.com/v1"
            ),
        )
        secrets = SecretReferenceConfig(
            github_token_env=(
                credentials.get("github_token_env", "GH_TOKEN").strip() or "GH_TOKEN"
            ),
            reviewer_api_key_env=(
                models.get("reviewer_api_key_env", "OPENAI_API_KEY_REVIEWER").strip()
                or "OPENAI_API_KEY_REVIEWER"
            ),
            operational_store_dsn_env=(
                operational_store.get("dsn_env", "LOOP_POSTGRES_DSN").strip()
                or "LOOP_POSTGRES_DSN"
            ),
            trusted_reviewer_socket_env=(
                runtime.get("trusted_reviewer_socket_env", "LOOP_TRUSTED_REVIEWER_SOCKET").strip()
                or "LOOP_TRUSTED_REVIEWER_SOCKET"
            ),
        )
        return cls(
            config_path=selected,
            project_key=_required(project, "key"),
            workspace_path=workspace,
            engine=engine,
            models=model_config,
            secrets=secrets,
        )

    def runtime_environment(
        self,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """設定ファイルを内部Host境界が使う標準環境へ正規化する。"""

        values = dict(environment if environment is not None else os.environ)
        secret_mappings = (
            ("GH_TOKEN", self.secrets.github_token_env),
            ("OPENAI_API_KEY_REVIEWER", self.secrets.reviewer_api_key_env),
            ("LOOP_POSTGRES_DSN", self.secrets.operational_store_dsn_env),
            ("LOOP_TRUSTED_REVIEWER_SOCKET", self.secrets.trusted_reviewer_socket_env),
        )
        for canonical_name, configured_name in secret_mappings:
            value = values.get(configured_name)
            if value:
                values[canonical_name] = value

        engine = self.engine
        values.update(
            {
                "LOOP_REPOSITORY": engine.repository,
                "LOOP_PROJECT_OWNER": engine.owner,
                "LOOP_PROJECT_NUMBER": str(engine.project_number),
                "LOOP_MISSION_ISSUE": str(engine.mission_issue),
                "LOOP_LABEL": engine.label,
                "LOOP_TRUNK_BRANCH": engine.trunk_branch,
                "LOOP_AUTHORITY_REFS": ",".join(engine.authority_refs),
                "LOOP_IMPROVEMENT_AREA": engine.improvement_area,
                "LOOP_ISSUE_LEVEL": engine.issue_level,
                "LOOP_CI_WORKFLOW_NAME": engine.ci_workflow_name,
                "LOOP_IMPLEMENTER_PROVIDER": self.models.implementer_provider,
                "LOOP_IMPLEMENTER_MODEL": self.models.implementer_model,
                "LOOP_REVIEWER_PROVIDER": self.models.reviewer_provider,
                "LOOP_REVIEWER_MODEL": self.models.reviewer_model,
                "LOOP_REVIEWER_API_BASE": self.models.reviewer_api_base,
            }
        )
        for name, value in (
            ("LOOP_ROOT_ISSUE", engine.root_issue),
            ("LOOP_PARENT_ISSUE", engine.parent_issue),
            ("LOOP_INTEGRATION_WORK", engine.integration_work),
        ):
            if value is None:
                values.pop(name, None)
            else:
                values[name] = str(value)
        return values

    def canonical_environment(
        self,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """旧名称との互換用alias。"""

        return self.runtime_environment(environment)


def _configured_path(platform_root: Path, environment: Mapping[str, str]) -> Path:
    raw = environment.get("LOOP_CONFIG_FILE")
    if raw:
        return Path(raw)
    return platform_root / "config" / "loop-engineering.ini"


def _section(parser: ConfigParser, name: str) -> SectionProxy:
    if not parser.has_section(name):
        raise ValueError(f"設定section [{name}] がありません")
    return parser[name]


def _required(section: SectionProxy, name: str) -> str:
    value = section.get(name, "").strip()
    if not value:
        raise ValueError(f"設定値 {section.name}.{name} がありません")
    return value


def _required_mapping(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"{name}が設定されていません")
    return value


def _required_int_section(section: SectionProxy, name: str) -> int:
    raw = _required(section, name)
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{section.name}.{name}は整数で指定してください") from error
    if value < 1:
        raise ValueError(f"{section.name}.{name}は1以上で指定してください")
    return value


def _optional_int_section(section: SectionProxy, name: str) -> int | None:
    raw = section.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{section.name}.{name}は整数で指定してください") from error
    if value < 1:
        raise ValueError(f"{section.name}.{name}は1以上で指定してください")
    return value


def _required_int_mapping(values: Mapping[str, str], name: str) -> int:
    raw = _required_mapping(values, name)
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name}は整数で指定してください") from error
    if value < 1:
        raise ValueError(f"{name}は1以上で指定してください")
    return value


def _optional_int_mapping(values: Mapping[str, str], name: str) -> int | None:
    raw = values.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name}は整数で指定してください") from error
    if value < 1:
        raise ValueError(f"{name}は1以上で指定してください")
    return value


def _validate_env_name(name: str, value: str) -> None:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValueError(f"{name}に不正な環境変数名が指定されています")


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())
