import hashlib
import subprocess
from pathlib import Path

import pytest

from loop_engineering.host_launcher import (
    EnvironmentSecretProvider,
    GitHubCredentialUnavailable,
    build_launch_environment,
)


class FakeSecrets:
    def github_token(self) -> str:
        return "secret"


def test_launcher_injects_goal_identity_and_path_without_persisting_token(
    tmp_path: Path,
) -> None:
    operations = tmp_path / "docs" / "operations"
    operations.mkdir(parents=True)
    content = b"version: 3\ngeneration: 9\nmission"
    (operations / "loop_mission_goal.md").write_bytes(content)

    environment = build_launch_environment(
        tmp_path, FakeSecrets(), {"PATH": "/opt/homebrew/bin"}
    )

    assert environment.values["PATH"] == "/opt/homebrew/bin"
    assert environment.values["CODEX_MISSION_GOAL_VERSION"] == "3"
    assert environment.values["CODEX_MISSION_GOAL_GENERATION"] == "9"
    assert environment.values["CODEX_MISSION_GOAL_SHA256"] == hashlib.sha256(
        content
    ).hexdigest()
    assert environment.values["GH_TOKEN"] == "secret"


def test_environment_credentials_are_injected_and_dotenv_is_git_ignored() -> None:
    provider = EnvironmentSecretProvider({"GH_TOKEN": "github-token"})
    repository_root = Path(__file__).resolve().parents[2]

    assert provider.github_token() == "github-token"
    ignored = (repository_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    assert "config/loop-engineering.ini" in ignored
    assert subprocess.run(
        ("git", "check-ignore", "-q", ".env"),
        cwd=repository_root,
        check=False,
    ).returncode == 0


def test_missing_github_environment_key_is_typed_unavailable() -> None:
    with pytest.raises(GitHubCredentialUnavailable):
        EnvironmentSecretProvider({}).github_token()


def test_target_checkout_operations_do_not_reference_reviewer_credentials_or_client() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    package = repository_root / "src" / "loop_engineering"
    operation_paths = (
        package / "host_launcher.py",
        package / "host_entrypoint.py",
        package / "host_runtime.py",
        package / "trusted_worktree.py",
        repository_root / "scripts" / "launch-loop-engineering.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in operation_paths)

    assert "OPENAI_API_KEY" not in source
    assert "from openai" not in source
    assert "import openai" not in source
