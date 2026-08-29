from pathlib import Path

import pytest

from loop_engineering.config import LoopEngineeringSettings


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
    assert values["OPENAI_API_KEY_REVIEWER"] == "reviewer-secret"
    assert values["LOOP_POSTGRES_DSN"] == "postgresql://secret"
    assert values["LOOP_TRUSTED_REVIEWER_SOCKET"] == "/tmp/reviewer.sock"
    assert values["LOOP_REPOSITORY"] == "owner/product"
    assert values["LOOP_PROJECT_NUMBER"] == "9"
    assert values["LOOP_REVIEWER_MODEL"] == "gpt-5.6-terra"


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
