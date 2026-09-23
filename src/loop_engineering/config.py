"""Loop Engineeringの設定ファイルと秘密情報参照契約。"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Mapping
from configparser import ConfigParser, SectionProxy
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class SelfImprovementConfig:
    """Loop Engineering自身の改善Issueを公開する独立した公開先。"""

    enabled: bool = False
    repository: str | None = None
    owner: str | None = None
    project_number: int | None = None
    label: str | None = None
    area: str | None = None
    issue_level: str | None = None
    authority_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        values = (
            self.repository,
            self.owner,
            self.label,
            self.area,
            self.issue_level,
        )
        if not self.enabled:
            if any(value is not None for value in (*values, self.project_number)):
                raise ValueError("無効なself_improvementへ公開先を指定できません")
            return
        if any(value is None or not value.strip() for value in values):
            raise ValueError("有効なself_improvementには公開先設定が必要です")
        if self.repository is None or "/" not in self.repository:
            raise ValueError("self_improvement.repositoryはowner/name形式で指定してください")
        if self.project_number is None or self.project_number < 1:
            raise ValueError("self_improvement.project_numberは1以上で指定してください")
        if any(not item.strip() for item in self.authority_refs):
            raise ValueError("self_improvement.authority_refsに空文字は指定できません")


@dataclass(frozen=True, slots=True)
class LoopEngineConfig:
    """Product固有identityをCoreから分離する非秘密設定。"""

    repository: str
    owner: str
    project_number: int
    mission_issue: int | None = None
    label: str = "loop-engineering"
    trunk_branch: str = "main"
    authority_refs: tuple[str, ...] = ()
    improvement_area: str = "Runtime / Infrastructure"
    issue_level: str = "Work"
    root_issue: int | None = None
    parent_issue: int | None = None
    integration_work: int | None = None
    ci_workflow_name: str = "Deterministic CI"
    work_branch_template: str = "loop/work-{issue}"
    self_improvement: SelfImprovementConfig = field(default_factory=SelfImprovementConfig)

    def __post_init__(self) -> None:
        text_fields = (
            ("repository", self.repository),
            ("owner", self.owner),
            ("label", self.label),
            ("trunk_branch", self.trunk_branch),
            ("improvement_area", self.improvement_area),
            ("issue_level", self.issue_level),
            ("ci_workflow_name", self.ci_workflow_name),
            ("work_branch_template", self.work_branch_template),
        )
        for name, value in text_fields:
            if not value.strip():
                raise ValueError(f"{name}を空文字にはできません")
        if "/" not in self.repository:
            raise ValueError("repositoryはowner/name形式で指定してください")
        if self.project_number < 1:
            raise ValueError("project_numberは1以上である必要があります")
        optional_issue_fields: tuple[tuple[str, int | None], ...] = (
            ("mission_issue", self.mission_issue),
            ("root_issue", self.root_issue),
            ("parent_issue", self.parent_issue),
            ("integration_work", self.integration_work),
        )
        for field_name, field_value in optional_issue_fields:
            if field_value is not None and field_value < 1:
                raise ValueError(f"{field_name}は1以上である必要があります")
        if any(not item.strip() for item in self.authority_refs):
            raise ValueError("authority_refsに空文字は指定できません")
        _validate_work_branch_template(self.work_branch_template)

    def work_branch(self, issue: int) -> str:
        if issue < 1:
            raise ValueError("issueは1以上である必要があります")
        branch = self.work_branch_template.format(issue=issue)
        _validate_git_branch(branch)
        return branch

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> LoopEngineConfig:
        """設定loaderが生成した内部環境を既存Host境界へ受け渡す互換入口。"""

        repository = _required_mapping(environment, "LOOP_REPOSITORY")
        owner = environment.get("LOOP_PROJECT_OWNER", "").strip() or repository.split("/", 1)[0]
        return cls(
            repository=repository,
            owner=owner,
            project_number=_required_int_mapping(environment, "LOOP_PROJECT_NUMBER"),
            mission_issue=_optional_int_mapping(environment, "LOOP_MISSION_ISSUE"),
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
            work_branch_template=environment.get(
                "LOOP_WORK_BRANCH_TEMPLATE", "loop/work-{issue}"
            ).strip()
            or "loop/work-{issue}",
            self_improvement=_self_improvement_from_environment(environment),
        )


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """モデルとAPIの非秘密設定。"""

    implementer_provider: str
    implementer_model: str
    reviewer_provider: str
    reviewer_model: str
    reviewer_api_base: str
    implementer_profile: str | None = None

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
        if self.implementer_profile is not None and not self.implementer_profile.strip():
            raise ValueError("implementer_profileを空文字にはできません")


@dataclass(frozen=True, slots=True)
class ReviewLevelConfig:
    """External Review Levelの非秘密policy。"""

    level: int
    provider: str
    model: str
    api_base: str
    credential_env: str
    required: bool = True
    timeout_seconds: int = 1200
    context_policy: str = "default"
    escalation_policy: str = "BLOCK"
    passes_required: int = 1

    def __post_init__(self) -> None:
        if self.level < 1:
            raise ValueError("review levelは1以上で指定してください")
        for name, value in (
            ("provider", self.provider),
            ("model", self.model),
            ("api_base", self.api_base),
            ("context_policy", self.context_policy),
        ):
            if not value.strip():
                raise ValueError(f"review level {name}を空文字にはできません")
        _validate_env_name("review level credential_env", self.credential_env)
        if (
            self.provider.strip().lower() == "openai"
            and self.credential_env != "OPENAI_API_KEY"
        ):
            raise ValueError(
                "OpenAI review levelのcredential_envはOPENAI_API_KEYで指定してください"
            )
        if self.timeout_seconds < 1 or self.timeout_seconds > 7200:
            raise ValueError("review level timeout_secondsは1..7200で指定してください")
        if self.passes_required < 1 or self.passes_required > 8:
            raise ValueError("review level passes_requiredは1..8で指定してください")
        if self.escalation_policy not in {"NEXT_LEVEL", "HUMAN", "BLOCK"}:
            raise ValueError(
                "review level escalation_policyはNEXT_LEVEL/HUMAN/BLOCKで指定してください"
            )


@dataclass(frozen=True, slots=True)
class VerificationCommandConfig:
    """Production固有のtrusted verification command descriptor。"""

    identity: str
    argv: tuple[str, ...]
    working_directory: str = "."
    timeout_seconds: int = 1200
    required: bool = True

    def __post_init__(self) -> None:
        if (
            not self.identity.strip()
            or not self.argv
            or any(not item or "\x00" in item for item in self.argv)
        ):
            raise ValueError("verification commandが不正です")
        if (
            self.working_directory != "."
            and not _safe_relative_config_path(self.working_directory)
        ):
            raise ValueError("verification working_directoryが不正です")
        if self.timeout_seconds < 1 or self.timeout_seconds > 7200:
            raise ValueError("verification timeout_secondsは1..7200で指定してください")


@dataclass(frozen=True, slots=True)
class LocalLlmCoderConfig:
    """local-llm-coder Worker Backendの非秘密設定。"""

    endpoint: str
    production_name: str
    model_profile: str

    def __post_init__(self) -> None:
        _validate_local_llm_coder_endpoint(self.endpoint)
        if (
            not self.production_name
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*",
                self.production_name,
            )
            is None
            or self.production_name in {".", "..", "tmp"}
        ):
            raise ValueError("local_llm_coder.production_nameが不正です")
        if not self.model_profile.strip():
            raise ValueError(
                "local_llm_coder.model_profileを空文字にはできません"
            )


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
    local_llm_coder: LocalLlmCoderConfig | None = None
    review_levels: tuple[ReviewLevelConfig, ...] = ()
    verification_commands: tuple[VerificationCommandConfig, ...] = ()

    @classmethod
    def load(
        cls,
        platform_root: Path,
        environment: Mapping[str, str] | None = None,
        *,
        config_path: Path | None = None,
    ) -> LoopEngineeringSettings:
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
        self_improvement = _self_improvement_from_section(
            parser, project, workspace, platform_root.resolve(strict=False)
        )

        engine = LoopEngineConfig(
            repository=repository,
            owner=owner,
            project_number=_required_int_section(project, "project_number"),
            mission_issue=_optional_int_section(project, "mission_issue"),
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
            work_branch_template=(
                project.get("work_branch_template", "loop/work-{issue}").strip()
                or "loop/work-{issue}"
            ),
            self_improvement=self_improvement,
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
            implementer_profile=models.get("implementer_profile", "").strip() or None,
        )
        local_llm_coder = _local_llm_coder_from_parser(parser, model_config)
        reviewer_api_key_env = (
            models.get("reviewer_api_key_env", "OPENAI_API_KEY").strip()
            or "OPENAI_API_KEY"
        )
        if reviewer_api_key_env != "OPENAI_API_KEY":
            raise ValueError(
                "reviewer_api_key_envはOPENAI_API_KEYで指定してください"
            )
        review_levels = _review_levels_from_parser(
            parser,
            model_config,
            reviewer_api_key_env,
        )
        verification_commands = _verification_commands_from_parser(parser)
        secrets = SecretReferenceConfig(
            github_token_env=(
                credentials.get("github_token_env", "GH_TOKEN").strip() or "GH_TOKEN"
            ),
            reviewer_api_key_env=reviewer_api_key_env,
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
            local_llm_coder=local_llm_coder,
            review_levels=review_levels,
            verification_commands=verification_commands,
        )

    def runtime_environment(
        self,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """設定ファイルを内部Host境界が使う標準環境へ正規化する。"""

        values = dict(environment if environment is not None else os.environ)
        secret_mappings = (
            ("GH_TOKEN", self.secrets.github_token_env),
            ("OPENAI_API_KEY", self.secrets.reviewer_api_key_env),
            ("LOOP_POSTGRES_DSN", self.secrets.operational_store_dsn_env),
            ("LOOP_TRUSTED_REVIEWER_SOCKET", self.secrets.trusted_reviewer_socket_env),
        )
        for canonical_name, configured_name in secret_mappings:
            value = values.get(configured_name)
            if value:
                values[canonical_name] = value
            else:
                values.pop(canonical_name, None)

        engine = self.engine
        values.update(
            {
                "LOOP_REPOSITORY": engine.repository,
                "LOOP_PROJECT_OWNER": engine.owner,
                "LOOP_PROJECT_NUMBER": str(engine.project_number),
                "LOOP_LABEL": engine.label,
                "LOOP_TRUNK_BRANCH": engine.trunk_branch,
                "LOOP_AUTHORITY_REFS": ",".join(engine.authority_refs),
                "LOOP_IMPROVEMENT_AREA": engine.improvement_area,
                "LOOP_ISSUE_LEVEL": engine.issue_level,
                "LOOP_CI_WORKFLOW_NAME": engine.ci_workflow_name,
                "LOOP_WORK_BRANCH_TEMPLATE": engine.work_branch_template,
                "LOOP_IMPLEMENTER_PROVIDER": self.models.implementer_provider,
                "LOOP_IMPLEMENTER_MODEL": self.models.implementer_model,
                "LOOP_REVIEWER_PROVIDER": self.models.reviewer_provider,
                "LOOP_REVIEWER_MODEL": self.models.reviewer_model,
                "LOOP_REVIEWER_API_BASE": self.models.reviewer_api_base,
            }
        )
        if self.models.implementer_profile is None:
            values.pop("LOOP_IMPLEMENTER_PROFILE", None)
        else:
            values["LOOP_IMPLEMENTER_PROFILE"] = self.models.implementer_profile
        values.pop("LOOP_LOCAL_LLM_CODER_ROOT", None)
        if self.local_llm_coder is None:
            for name in (
                "LOOP_LOCAL_LLM_CODER_ENDPOINT",
                "LOOP_LOCAL_LLM_CODER_PRODUCTION",
                "LOOP_LOCAL_LLM_CODER_MODEL_PROFILE",
            ):
                values.pop(name, None)
        else:
            values["LOOP_LOCAL_LLM_CODER_ENDPOINT"] = (
                self.local_llm_coder.endpoint
            )
            values["LOOP_LOCAL_LLM_CODER_PRODUCTION"] = (
                self.local_llm_coder.production_name
            )
            values["LOOP_LOCAL_LLM_CODER_MODEL_PROFILE"] = (
                self.local_llm_coder.model_profile
            )

        sink = engine.self_improvement
        values["LOOP_SELF_IMPROVEMENT_ENABLED"] = "true" if sink.enabled else "false"
        for name, value in (
            ("REPOSITORY", sink.repository),
            ("OWNER", sink.owner),
            ("PROJECT_NUMBER", str(sink.project_number) if sink.project_number else None),
            ("LABEL", sink.label),
            ("AREA", sink.area),
            ("ISSUE_LEVEL", sink.issue_level),
            ("AUTHORITY_REFS", ",".join(sink.authority_refs) if sink.enabled else None),
        ):
            runtime_name = f"LOOP_SELF_IMPROVEMENT_{name}"
            if value is None:
                values.pop(runtime_name, None)
            else:
                values[runtime_name] = value
        optional_runtime_values: tuple[tuple[str, int | None], ...] = (
            ("LOOP_MISSION_ISSUE", engine.mission_issue),
            ("LOOP_ROOT_ISSUE", engine.root_issue),
            ("LOOP_PARENT_ISSUE", engine.parent_issue),
            ("LOOP_INTEGRATION_WORK", engine.integration_work),
        )
        for runtime_name, runtime_value in optional_runtime_values:
            if runtime_value is None:
                values.pop(runtime_name, None)
            else:
                values[runtime_name] = str(runtime_value)
        return values

    def canonical_environment(
        self,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """旧名称との互換用alias。"""

        return self.runtime_environment(environment)


def _self_improvement_from_section(
    parser: ConfigParser,
    project: SectionProxy,
    workspace: Path,
    platform_root: Path,
) -> SelfImprovementConfig:
    if not parser.has_section("self_improvement"):
        if workspace == platform_root:
            return _legacy_self_target_sink(project)
        return SelfImprovementConfig()
    section = parser["self_improvement"]
    enabled = section.getboolean("enabled", fallback=False)
    if not enabled:
        return SelfImprovementConfig()
    repository = _required(section, "repository")
    return SelfImprovementConfig(
        enabled=True,
        repository=repository,
        owner=section.get("project_owner", "").strip() or repository.split("/", 1)[0],
        project_number=_required_int_section(section, "project_number"),
        label=_required(section, "label"),
        area=_required(section, "area"),
        issue_level=_required(section, "issue_level"),
        authority_refs=_csv(section.get("authority_refs", "")),
    )


def _legacy_self_target_sink(project: SectionProxy) -> SelfImprovementConfig:
    repository = _required(project, "repository")
    return SelfImprovementConfig(
        enabled=True,
        repository=repository,
        owner=project.get("project_owner", "").strip() or repository.split("/", 1)[0],
        project_number=_required_int_section(project, "project_number"),
        label=project.get("label", "loop-engineering").strip() or "loop-engineering",
        area=(
            project.get("improvement_area", "Runtime / Infrastructure").strip()
            or "Runtime / Infrastructure"
        ),
        issue_level=project.get("issue_level", "Work").strip() or "Work",
        authority_refs=_csv(project.get("authority_refs", "")),
    )


def _self_improvement_from_environment(values: Mapping[str, str]) -> SelfImprovementConfig:
    if values.get("LOOP_SELF_IMPROVEMENT_ENABLED", "false").strip().lower() != "true":
        return SelfImprovementConfig()
    repository = _required_mapping(values, "LOOP_SELF_IMPROVEMENT_REPOSITORY")
    return SelfImprovementConfig(
        enabled=True,
        repository=repository,
        owner=values.get("LOOP_SELF_IMPROVEMENT_OWNER", "").strip()
        or repository.split("/", 1)[0],
        project_number=_required_int_mapping(values, "LOOP_SELF_IMPROVEMENT_PROJECT_NUMBER"),
        label=_required_mapping(values, "LOOP_SELF_IMPROVEMENT_LABEL"),
        area=_required_mapping(values, "LOOP_SELF_IMPROVEMENT_AREA"),
        issue_level=_required_mapping(values, "LOOP_SELF_IMPROVEMENT_ISSUE_LEVEL"),
        authority_refs=_csv(values.get("LOOP_SELF_IMPROVEMENT_AUTHORITY_REFS", "")),
    )


def _configured_path(platform_root: Path, environment: Mapping[str, str]) -> Path:
    raw = environment.get("LOOP_CONFIG_FILE")
    if raw:
        return Path(raw)
    return platform_root / "config" / "loop-engineering.ini"


def _verification_commands_from_parser(
    parser: ConfigParser,
) -> tuple[VerificationCommandConfig, ...]:
    sections: list[tuple[int, SectionProxy]] = []
    for name in parser.sections():
        matched = re.fullmatch(r"verification\.command\.(\d+)", name)
        if matched is None:
            continue
        index = int(matched.group(1))
        if index < 1:
            raise ValueError("verification command番号は1以上で指定してください")
        sections.append((index, parser[name]))

    if not sections:
        return (
            VerificationCommandConfig(
                identity="git-diff-check",
                argv=("git", "diff", "--check", "HEAD"),
                working_directory=".",
                timeout_seconds=120,
                required=True,
            ),
        )

    sections.sort(key=lambda item: item[0])
    result: list[VerificationCommandConfig] = []
    identities: set[str] = set()
    for _index, section in sections:
        identity = _required(section, "identity")
        if identity in identities:
            raise ValueError("verification command identityが重複しています")
        raw_argv = _required(section, "argv_json")
        try:
            parsed = json.loads(raw_argv)
        except json.JSONDecodeError as error:
            raise ValueError(f"{section.name}.argv_jsonはJSON配列で指定してください") from error
        if (
            not isinstance(parsed, list)
            or not parsed
            or not all(isinstance(item, str) and item for item in parsed)
        ):
            raise ValueError(f"{section.name}.argv_jsonは文字列JSON配列で指定してください")
        try:
            required = section.getboolean("required", fallback=True)
        except ValueError as error:
            raise ValueError(
                f"{section.name}.requiredはtrue/falseで指定してください"
            ) from error
        result.append(
            VerificationCommandConfig(
                identity=identity,
                argv=tuple(parsed),
                working_directory=(
                    section.get("working_directory", ".").strip() or "."
                ),
                timeout_seconds=_bounded_int_section(
                    section,
                    "timeout_seconds",
                    default=1200,
                    minimum=1,
                    maximum=7200,
                ),
                required=required,
            )
        )
        identities.add(identity)
    return tuple(result)


def _review_levels_from_parser(
    parser: ConfigParser,
    models: ModelConfig,
    default_credential_env: str,
) -> tuple[ReviewLevelConfig, ...]:
    sections: list[tuple[int, SectionProxy]] = []
    for name in parser.sections():
        matched = re.fullmatch(r"review\.level\.(\d+)", name)
        if matched is None:
            continue
        level = int(matched.group(1))
        if level < 1:
            raise ValueError("review levelは1以上で指定してください")
        sections.append((level, parser[name]))

    if not sections:
        return (
            ReviewLevelConfig(
                level=1,
                provider=models.reviewer_provider,
                model=models.reviewer_model,
                api_base=models.reviewer_api_base,
                credential_env=default_credential_env,
            ),
        )

    sections.sort(key=lambda item: item[0])
    result: list[ReviewLevelConfig] = []
    for level, section in sections:
        try:
            required = section.getboolean("required", fallback=True)
        except ValueError as error:
            raise ValueError(
                f"{section.name}.requiredはtrue/falseで指定してください"
            ) from error
        result.append(
            ReviewLevelConfig(
                level=level,
                provider=section.get("provider", models.reviewer_provider).strip()
                or models.reviewer_provider,
                model=section.get("model", models.reviewer_model).strip()
                or models.reviewer_model,
                api_base=section.get("api_base", models.reviewer_api_base).strip()
                or models.reviewer_api_base,
                credential_env=(
                    section.get("credential_env", default_credential_env).strip()
                    or default_credential_env
                ),
                required=required,
                timeout_seconds=_bounded_int_section(
                    section,
                    "timeout_seconds",
                    default=1200,
                    minimum=1,
                    maximum=7200,
                ),
                context_policy=section.get("context_policy", "default").strip()
                or "default",
                escalation_policy=(
                    section.get("escalation_policy", "BLOCK").strip().upper()
                    or "BLOCK"
                ),
                passes_required=_bounded_int_section(
                    section,
                    "passes_required",
                    default=1,
                    minimum=1,
                    maximum=8,
                ),
            )
        )
    return tuple(result)


def _local_llm_coder_from_parser(
    parser: ConfigParser,
    models: ModelConfig,
) -> LocalLlmCoderConfig | None:
    if models.implementer_provider != "local-llm-coder":
        return None
    if not parser.has_section("local_llm_coder"):
        raise ValueError("設定section [local_llm_coder] がありません")
    section = parser["local_llm_coder"]
    if "root" in section:
        raise ValueError(
            "local_llm_coder.rootは廃止されました。endpointを指定してください"
        )
    profile = models.implementer_profile or models.implementer_model
    return LocalLlmCoderConfig(
        endpoint=_required(section, "endpoint").rstrip("/"),
        production_name=_required(section, "production_name"),
        model_profile=profile,
    )


def _validate_local_llm_coder_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http":
        raise ValueError("local_llm_coder.endpointはhttpが必要です")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("local_llm_coder.endpointへuserinfoは指定できません")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("local_llm_coder.endpointはhost:portだけを指定してください")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(
            "local_llm_coder.endpointのportが不正です"
        ) from error
    if parsed.hostname is None or port is None or port < 1:
        raise ValueError("local_llm_coder.endpointにはhostとportが必要です")
    if parsed.hostname.lower() == "localhost":
        return
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ValueError(
            "local_llm_coder.endpointはloopback hostが必要です"
        ) from error
    if not address.is_loopback:
        raise ValueError("local_llm_coder.endpointはloopback hostが必要です")


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


def _bounded_int_section(
    section: SectionProxy,
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = section.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{section.name}.{name}は整数で指定してください") from error
    if value < minimum or value > maximum:
        raise ValueError(
            f"{section.name}.{name}は{minimum}..{maximum}で指定してください"
        )
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


def _safe_relative_config_path(value: str) -> bool:
    if not value or value != value.strip() or "\\" in value or "\x00" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and value not in {"", "./"}


def _validate_env_name(name: str, value: str) -> None:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValueError(f"{name}に不正な環境変数名が指定されています")


def _validate_work_branch_template(template: str) -> None:
    remainder = template.replace("{issue}", "")
    if template.count("{issue}") != 1 or "{" in remainder or "}" in remainder:
        raise ValueError("work_branch_templateは{issue}を1回だけ含める必要があります")
    _validate_git_branch(template.format(issue=1))


def _validate_git_branch(branch: str) -> None:
    components = branch.split("/")
    if (
        not branch
        or branch.startswith("/")
        or branch.endswith(("/", "."))
        or "//" in branch
        or ".." in branch
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
        or any(component.startswith(".") or component.endswith(".lock") for component in components)
    ):
        raise ValueError("work_branch_templateが安全なGit branchではありません")


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())
