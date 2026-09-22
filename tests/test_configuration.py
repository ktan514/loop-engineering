from pathlib import Path

import pytest

from loop_engineering.config import (
    LoopEngineConfig,
    LoopEngineeringSettings,
    SelfImprovementConfig,
)


def _write_config(path: Path, workspace: str, *, github_env: str = "MY_GITHUB_TOKEN") -> None:
    path.write_text(
        "\n".join(
            (
                "[project]",
                "key = sample",
                f"workspace_path = {workspace}",
                "repository = owner/product",
                "trunk_branch = main",
                "project_owner = owner",
                "project_number = 9",
                "mission_issue = 100",
                "root_issue = 101",
                "parent_issue = 102",
                "integration_work = 103",
                "label = loop-engineering",
                "authority_refs = #100, #102",
                "ci_workflow_name = Deterministic CI",
                "improvement_area = Runtime / Infrastructure",
                "issue_level = Work",
                "",
                "[models]",
                "implementer_provider = codex",
                "implementer_model = default",
                "reviewer_provider = openai",
                "reviewer_model = gpt-5.6-terra",
                "reviewer_api_base = https://api.openai.com/v1",
                "reviewer_api_key_env = MY_REVIEWER_KEY",
                "",
                "[credentials]",
                f"github_token_env = {github_env}",
                "",
                "[operational_store]",
                "dsn_env = MY_DATABASE_DSN",
                "",
                "[runtime]",
                "trusted_reviewer_socket_env = MY_REVIEWER_SOCKET",
            )
        ),
        encoding="utf-8",
    )


def test_settings_load_workspace_models_and_secret_environment_names(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    workspace = tmp_path / "product"
    _write_config(config, str(workspace))

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert settings.project_key == "sample"
    assert settings.workspace_path == workspace.resolve(strict=False)
    assert settings.engine.repository == "owner/product"
    assert settings.engine.project_number == 9
    assert settings.engine.mission_issue == 100
    assert settings.models.reviewer_model == "gpt-5.6-terra"
    assert settings.secrets.github_token_env == "MY_GITHUB_TOKEN"
    assert settings.secrets.reviewer_api_key_env == "MY_REVIEWER_KEY"


def test_settings_allow_v2_without_legacy_mission_issue(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    content = config.read_text(encoding="utf-8").replace("mission_issue = 100\n", "")
    config.write_text(content, encoding="utf-8")

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert settings.engine.mission_issue is None
    assert "LOOP_MISSION_ISSUE" not in settings.runtime_environment({})

def test_runtime_environment_maps_secret_values_without_putting_them_in_config(
    tmp_path: Path,
) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    values = settings.runtime_environment(
        {
            "MY_GITHUB_TOKEN": "github-secret",
            "MY_REVIEWER_KEY": "reviewer-secret",
            "MY_DATABASE_DSN": "postgresql://secret",
            "MY_REVIEWER_SOCKET": "/tmp/reviewer.sock",
        }
    )

    assert values["GH_TOKEN"] == "github-secret"
    assert values["OPENAI_API_KEY"] == "reviewer-secret"
    assert values["LOOP_POSTGRES_DSN"] == "postgresql://secret"
    assert values["LOOP_TRUSTED_REVIEWER_SOCKET"] == "/tmp/reviewer.sock"
    assert values["LOOP_REPOSITORY"] == "owner/product"
    assert values["LOOP_PROJECT_NUMBER"] == "9"
    assert values["LOOP_REVIEWER_MODEL"] == "gpt-5.6-terra"
    assert values["LOOP_INITIAL_PROJECT_STATUS"] == "Backlog"
    assert values["LOOP_DONE_PROJECT_STATUS"] == "Done"





def test_reviewer_single_config_normalizes_to_level_one(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert len(settings.review_levels) == 1
    level = settings.review_levels[0]
    assert level.level == 1
    assert level.provider == "openai"
    assert level.model == "gpt-5.6-terra"
    assert level.credential_env == "MY_REVIEWER_KEY"
    assert level.passes_required == 1


def test_multiple_review_levels_and_fresh_pass_count_load_from_config(
    tmp_path: Path,
) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n[review.level.1]\n"
            "provider = openai\n"
            "model = low-reviewer\n"
            "credential_env = LOW_REVIEW_KEY\n"
            "required = true\n"
            "passes_required = 2\n"
            "escalation_policy = NEXT_LEVEL\n"
            "\n[review.level.2]\n"
            "provider = openai\n"
            "model = high-reviewer\n"
            "credential_env = HIGH_REVIEW_KEY\n"
            "required = true\n"
            "passes_required = 2\n"
            "escalation_policy = HUMAN\n"
        )

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert [(item.level, item.model, item.passes_required) for item in settings.review_levels] == [
        (1, "low-reviewer", 2),
        (2, "high-reviewer", 2),
    ]
    assert settings.review_levels[0].credential_env == "LOW_REVIEW_KEY"
    assert settings.review_levels[1].credential_env == "HIGH_REVIEW_KEY"


def test_invalid_review_level_pass_count_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n[review.level.1]\n"
            "passes_required = 0\n"
        )

    with pytest.raises(ValueError, match="passes_required"):
        LoopEngineeringSettings.load(tmp_path, {}, config_path=config)


def test_project_status_names_are_product_configurable(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    content = config.read_text(encoding="utf-8")
    content = content.replace(
        "issue_level = Work\n",
        "issue_level = Work\n"
        "initial_project_status = Ready\n"
        "done_project_status = Completed\n",
    )
    config.write_text(content, encoding="utf-8")

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert settings.engine.initial_project_status == "Ready"
    assert settings.engine.done_project_status == "Completed"


def test_verification_commands_default_to_safe_diff_check(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert len(settings.verification_commands) == 1
    command = settings.verification_commands[0]
    assert command.identity == "git-diff-check"
    assert command.argv == ("git", "diff", "--check", "HEAD")


def test_verification_commands_load_as_argv_without_shell(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n[verification.command.1]\n"
            "identity = tests\n"
            'argv_json = ["python", "-m", "pytest", "-q"]\n'
            "working_directory = .\n"
            "timeout_seconds = 900\n"
            "required = true\n"
        )

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert len(settings.verification_commands) == 1
    command = settings.verification_commands[0]
    assert command.identity == "tests"
    assert command.argv == ("python", "-m", "pytest", "-q")
    assert command.timeout_seconds == 900
    assert command.required


def test_verification_command_path_traversal_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n[verification.command.1]\n"
            "identity = tests\n"
            'argv_json = ["python", "-m", "pytest"]\n'
            "working_directory = ../outside\n"
        )

    with pytest.raises(ValueError, match="working_directory"):
        LoopEngineeringSettings.load(tmp_path, {}, config_path=config)


def test_default_reviewer_api_key_environment_is_openai_api_key(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    workspace = tmp_path / "product"
    _write_config(config, str(workspace))
    text = config.read_text(encoding="utf-8").replace(
        "reviewer_api_key_env = MY_REVIEWER_KEY\n",
        "",
    )
    config.write_text(text, encoding="utf-8")

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert settings.secrets.reviewer_api_key_env == "OPENAI_API_KEY"


def test_python_source_does_not_load_dotenv_directly() -> None:
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src" / "loop_engineering").rglob("*.py")
    )

    assert "load_dotenv" not in source
    assert "dotenv_values" not in source
    assert "python-dotenv" not in source


def test_relative_workspace_path_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, "relative/product")

    with pytest.raises(ValueError, match="workspace_path"):
        LoopEngineeringSettings.load(tmp_path, {}, config_path=config)


def test_invalid_secret_environment_name_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"), github_env="BAD-NAME")

    with pytest.raises(ValueError, match="github_token_env"):
        LoopEngineeringSettings.load(tmp_path, {}, config_path=config)


def test_product_branch_template_expands_only_issue_placeholder() -> None:
    yura = LoopEngineConfig(
        repository="ktan514/ai-liver-yura",
        owner="ktan514",
        project_number=7,
        mission_issue=450,
        work_branch_template="feature/work-{issue}",
    )

    assert yura.work_branch(123) == "feature/work-123"
    assert LoopEngineConfig(
        repository="ktan514/loop-engineering",
        owner="ktan514",
        project_number=9,
        mission_issue=33,
    ).work_branch(123) == "loop/work-123"


@pytest.mark.parametrize(
    "template",
    (
        "",
        "feature/{name}",
        "../work-{issue}",
        "feature//{issue}",
        "feature/foo.lock",
        "feature/.hidden-{issue}",
    ),
)
def test_invalid_product_branch_template_fails_closed(template: str) -> None:
    with pytest.raises(ValueError, match="work_branch_template"):
        LoopEngineConfig(
            repository="owner/product",
            owner="owner",
            project_number=1,
            mission_issue=1,
            work_branch_template=template,
        )


def test_self_improvement_is_disabled_without_explicit_sink() -> None:
    config = LoopEngineConfig(
        repository="ktan514/ai-liver-yura",
        owner="ktan514",
        project_number=7,
        mission_issue=450,
    )

    assert not config.self_improvement.enabled


def test_explicit_self_improvement_sink_is_independent_from_product() -> None:
    config = LoopEngineConfig(
        repository="ktan514/ai-liver-yura",
        owner="ktan514",
        project_number=7,
        mission_issue=450,
        label="v2",
        self_improvement=SelfImprovementConfig(
            enabled=True,
            repository="ktan514/loop-engineering",
            owner="ktan514",
            project_number=9,
            label="loop-engineering",
            area="Runtime / Infrastructure",
            issue_level="Work",
        ),
    )

    assert config.self_improvement.repository == "ktan514/loop-engineering"
    assert config.self_improvement.project_number == 9
    assert config.self_improvement.label == "loop-engineering"


def test_settings_load_explicit_self_improvement_sink(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    with config.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n[self_improvement]\n"
            "enabled = true\n"
            "repository = ktan514/loop-engineering\n"
            "project_owner = ktan514\n"
            "project_number = 9\n"
            "label = loop-engineering\n"
            "area = Runtime / Infrastructure\n"
            "issue_level = Work\n"
        )

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert settings.engine.self_improvement.enabled
    assert settings.engine.self_improvement.repository == "ktan514/loop-engineering"
    assert settings.engine.self_improvement.project_number == 9


@pytest.mark.parametrize("enabled", (True, False))
def test_runtime_environment_round_trips_self_improvement_config(
    tmp_path: Path, enabled: bool
) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path / "product"))
    if enabled:
        with config.open("a", encoding="utf-8") as stream:
            stream.write(
                "\n[self_improvement]\n"
                "enabled = true\nrepository = ktan514/loop-engineering\n"
                "project_owner = ktan514\nproject_number = 9\n"
                "label = loop-engineering\narea = Runtime / Infrastructure\n"
                "issue_level = Work\nauthority_refs = #26, #40\n"
            )
    else:
        with config.open("a", encoding="utf-8") as stream:
            stream.write("\n[self_improvement]\nenabled = false\n")

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)
    round_tripped = LoopEngineConfig.from_environment(settings.runtime_environment({}))

    assert round_tripped.self_improvement.enabled is enabled
    if enabled:
        assert round_tripped.self_improvement.repository == "ktan514/loop-engineering"
        assert round_tripped.self_improvement.owner == "ktan514"
        assert round_tripped.self_improvement.project_number == 9
        assert round_tripped.self_improvement.label == "loop-engineering"
        assert round_tripped.self_improvement.area == "Runtime / Infrastructure"
        assert round_tripped.self_improvement.issue_level == "Work"


def test_legacy_self_target_configuration_is_migrated_only_at_platform_root(tmp_path: Path) -> None:
    config = tmp_path / "loop-engineering.ini"
    _write_config(config, str(tmp_path))

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert settings.engine.self_improvement.enabled
    assert settings.engine.self_improvement.repository == "owner/product"


def test_distributed_example_configuration_loads_after_workspace_replacement(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    template = root / "config" / "loop-engineering.example.ini"
    config = tmp_path / "loop-engineering.ini"
    config.write_text(
        template.read_text(encoding="utf-8").replace(
            "/absolute/path/to/product-workspace", str(tmp_path / "workspace")
        ),
        encoding="utf-8",
    )

    settings = LoopEngineeringSettings.load(tmp_path, {}, config_path=config)

    assert settings.engine.repository == "owner/repository"
    assert settings.engine.self_improvement.authority_refs == ()
