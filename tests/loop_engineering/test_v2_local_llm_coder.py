from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from loop_engineering.config import (
    LocalLlmCoderConfig,
    LoopEngineConfig,
    LoopEngineeringSettings,
    ModelConfig,
    SecretReferenceConfig,
)
from loop_engineering.v2_implementer import (
    CodexProposalImplementer,
    DevelopmentTaskPacket,
    ImplementerFinding,
    ImplementerStatus,
    ImplementerTransition,
)
from loop_engineering.v2_local_llm_coder import (
    LocalLlmCoderImplementerAdapter,
    build_implementer_backend,
)
from loop_engineering.v2_local_worker_http import (
    LocalWorkerHttpFailure,
    LocalWorkerHttpTimeout,
)


@dataclass(frozen=True)
class Result:
    returncode: int = 0
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class FakeRunner:
    def __init__(self, head: str) -> None:
        self.head = head
        self.environments: list[Mapping[str, str] | None] = []

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> Result:
        del cwd, timeout_seconds, capture_output
        values = tuple(command)
        assert values[0] == "git"
        assert values[-2:] == ("rev-parse", "HEAD")
        self.environments.append(environment)
        return Result(output=self.head)


class FakeWorkerClient:
    def __init__(self, mode: str = "pass") -> None:
        self.mode = mode
        self.calls = 0
        self.endpoint: str | None = None
        self.production_name: str | None = None
        self.request: dict[str, object] | None = None
        self.timeout_seconds: int | None = None

    def __call__(
        self,
        endpoint: str,
        production_name: str,
        request: dict[str, object],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        self.calls += 1
        self.endpoint = endpoint
        self.production_name = production_name
        self.request = request
        self.timeout_seconds = timeout_seconds
        if self.mode == "timeout":
            raise LocalWorkerHttpTimeout
        if self.mode == "unavailable":
            raise LocalWorkerHttpFailure("LOCAL_WORKER_CONNECTION_FAILED")
        if self.mode == "busy":
            raise LocalWorkerHttpFailure("LOCAL_WORKER_HTTP_ERROR", 409)
        if self.mode == "http400":
            raise LocalWorkerHttpFailure("LOCAL_WORKER_HTTP_ERROR", 400)
        if self.mode == "malformed":
            return {"invalid": True}

        payload = worker_result(request)
        if self.mode == "identity-mismatch":
            payload["request_identity"] = "wrong"
        return payload


def worker_result(request: Mapping[str, object]) -> dict[str, Any]:
    scopes = request["scope_paths"]
    assert isinstance(scopes, list)
    first_scope = scopes[0]
    assert isinstance(first_scope, str)
    changed_path = (
        first_scope
        if "." in Path(first_scope).name
        else first_scope + "/app.py"
    )
    transition = request["transition"]
    verification = (
        []
        if transition == "DESIGN"
        else [{"command": "test", "status": "PASS", "summary": "ok"}]
    )
    return {
        "schema_version": 1,
        "request_identity": request["request_identity"],
        "task_packet_identity": request["task_packet_identity"],
        "role": request["role"],
        "input_target_identity": request["input_target_identity"],
        "result_target_identity": request["input_target_identity"],
        "change_identity": "sha256:" + "a" * 64,
        "status": "PASS",
        "failure_kind": None,
        "completion": {
            "scope_checked": True,
            "target_identity_checked": True,
            "work_finalized": True,
            "verification_finalized": True,
        },
        "findings": [],
        "changed_paths": [changed_path],
        "verification_evidence": verification,
        "diagnostics": [],
        "session_id": "session-1",
        "artifacts": {
            "runtime_directory": "/tmp/runtime",
            "event_log": "/tmp/runtime/events.ndjson",
            "stderr_log": "/tmp/runtime/stderr.log",
            "agent_artifact_refs": [],
        },
    }


def local_layout(tmp_path: Path) -> tuple[Path, LocalLlmCoderConfig]:
    workspace = tmp_path / "product"
    workspace.mkdir()
    config = LocalLlmCoderConfig(
        "http://127.0.0.1:8765",
        "product",
        "local-main",
    )
    return workspace.resolve(), config


def packet(
    workspace: Path,
    transition: ImplementerTransition,
) -> DevelopmentTaskPacket:
    return DevelopmentTaskPacket(
        packet_identity="packet:1",
        work_identity="work:owner/sample:100",
        generation=1,
        transition=transition,
        repository_identity="owner/sample",
        workspace_canonical_path=workspace,
        exact_base_sha="b" * 40,
        goal_revision="goal-1",
        issue_revision="issue-1",
        scope_paths=("src", "docs"),
        acceptance_checks=("必要な変更を完了する",),
        canonical_design_identities=("design:1",),
        canonical_design_targets=("docs/design.md",),
        active_lineage_identity="lineage:1",
        authority_refs=("Issue #100",),
        non_goals=("scope外を変更しない",),
        safety_constraints=("mainへ直接pushしない",),
    )


def environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "HOME": "/tmp/home",
        "PYENV_ROOT": "/tmp/pyenv",
        "GH_TOKEN": "secret-gh",
        "OPENAI_API_KEY": "secret-openai",
        "OPENAI_API_KEY_REVIEWER": "secret-reviewer",
        "LOOP_POSTGRES_DSN": "secret-db",
        "LOOP_TRUSTED_REVIEWER_SOCKET": "secret-socket",
    }


def adapter(
    runner: FakeRunner,
    config: LocalLlmCoderConfig,
    workspace: Path,
    worker: FakeWorkerClient,
) -> LocalLlmCoderImplementerAdapter:
    return LocalLlmCoderImplementerAdapter(
        runner,
        config,
        workspace,
        environment(),
        worker_client=worker,
    )


@pytest.mark.parametrize(
    ("transition", "expected_role"),
    [
        (ImplementerTransition.DESIGN, "IMPLEMENTER"),
        (ImplementerTransition.IMPLEMENT, "IMPLEMENTER"),
    ],
)
def test_local_backend_design_and_implement(
    tmp_path: Path,
    transition: ImplementerTransition,
    expected_role: str,
) -> None:
    workspace, config = local_layout(tmp_path)
    task = packet(workspace, transition)
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient()

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.SUCCESS
    assert result.workspace_effect is not None
    assert result.proposal is None
    expected_changed_path = (
        "docs/design.md"
        if transition is ImplementerTransition.DESIGN
        else "src/app.py"
    )
    assert result.workspace_effect.changed_paths == (expected_changed_path,)
    assert worker.endpoint == "http://127.0.0.1:8765"
    assert worker.production_name == "product"
    assert worker.request is not None
    assert worker.request["role"] == expected_role
    assert worker.request["transition"] == transition.value
    assert worker.request["effect_requirement"] == "MUST_CHANGE"
    assert worker.request["model_profile"] == "local-main"
    expected_scope = (
        ["docs/design.md"]
        if transition is ImplementerTransition.DESIGN
        else ["src", "docs"]
    )
    assert worker.request["scope_paths"] == expected_scope
    if transition is ImplementerTransition.DESIGN:
        assert result.workspace_effect.verification_evidence == ()


def test_local_backend_repair_binds_change_identity_and_findings(
    tmp_path: Path,
) -> None:
    workspace, config = local_layout(tmp_path)
    finding = ImplementerFinding(
        finding_identity="finding:1",
        severity="BLOCKING",
        path="src/app.py",
        location="L1",
        problem="不備",
        basis="設計",
        evidence="現行code",
        impact="受入条件未達",
        suggested_fix="修正する",
    )
    task = replace(
        packet(workspace, ImplementerTransition.REPAIR),
        expected_change_identity="sha256:" + "c" * 64,
        approved_findings=(finding,),
    )
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient()

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.SUCCESS
    assert worker.request is not None
    assert worker.request["role"] == "FIXER"
    assert worker.request["expected_change_identity"] == (
        task.expected_change_identity
    )
    assert worker.request["approved_findings"] == [
        {
            "finding_identity": "finding:1",
            "severity": "BLOCKING",
            "path": "src/app.py",
            "location": "L1",
            "problem": "不備",
            "basis": "設計",
            "evidence": "現行code",
            "impact": "受入条件未達",
            "suggested_fix": "修正する",
        }
    ]


def test_local_backend_rejects_configured_workspace_mismatch_before_worker(
    tmp_path: Path,
) -> None:
    workspace, config = local_layout(tmp_path)
    configured = tmp_path / "configured"
    configured.mkdir()
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient()

    result = adapter(
        runner,
        config,
        configured.resolve(),
        worker,
    ).execute(task)

    assert result.status is ImplementerStatus.BLOCKED
    assert result.detail == "LOCAL_WORKSPACE_IDENTITY_MISMATCH"
    assert worker.calls == 0


def test_local_backend_rejects_workspace_mismatch_before_worker(
    tmp_path: Path,
) -> None:
    workspace, config = local_layout(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    task = replace(
        packet(workspace, ImplementerTransition.IMPLEMENT),
        workspace_canonical_path=other.resolve(),
    )
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient()

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.BLOCKED
    assert result.detail == "LOCAL_WORKSPACE_IDENTITY_MISMATCH"
    assert worker.calls == 0


@pytest.mark.parametrize("mode", ["malformed", "identity-mismatch"])
def test_local_backend_rejects_malformed_or_mismatched_result(
    tmp_path: Path,
    mode: str,
) -> None:
    workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient(mode)

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.FAILED
    assert result.detail == "LOCAL_WORKER_RESULT_MALFORMED"


def test_local_backend_timeout_is_incomplete(tmp_path: Path) -> None:
    workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient("timeout")

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.INCOMPLETE
    assert result.detail == "LOCAL_WORKER_TIMEOUT"
    assert result.failure_kind == "PROCESS_TIMEOUT"


def test_local_backend_provider_unavailable_is_failed(tmp_path: Path) -> None:
    workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient("unavailable")

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.FAILED
    assert result.detail == "LOCAL_WORKER_CONNECTION_FAILED"
    assert result.failure_kind == "PROVIDER_UNAVAILABLE"


def test_worker_busy_is_incomplete(tmp_path: Path) -> None:
    workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient("busy")

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.INCOMPLETE
    assert result.detail == "LOCAL_WORKER_BUSY"


def test_http_client_error_cannot_promote_success(tmp_path: Path) -> None:
    workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient("http400")

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.FAILED
    assert result.failure_kind == "RUNTIME_PREFLIGHT"


def test_local_backend_strips_control_plane_secrets(tmp_path: Path) -> None:
    workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha)
    worker = FakeWorkerClient()

    result = adapter(runner, config, workspace, worker).execute(task)

    assert result.status is ImplementerStatus.SUCCESS
    assert worker.request is not None
    request_text = __import__("json").dumps(
        worker.request,
        ensure_ascii=False,
    )
    assert "secret-gh" not in request_text
    assert "secret-openai" not in request_text
    assert "secret-db" not in request_text
    assert "secret-socket" not in request_text
    assert runner.environments
    assert runner.environments[-1] == {
        "PATH": "/usr/bin",
        "HOME": "/tmp/home",
        "PYENV_ROOT": "/tmp/pyenv",
    }


def settings(
    tmp_path: Path,
    *,
    provider: str,
    local: LocalLlmCoderConfig | None,
) -> LoopEngineeringSettings:
    workspace = (tmp_path / "workspace").resolve()
    return LoopEngineeringSettings(
        config_path=(tmp_path / "loop-engineering.ini").resolve(),
        project_key="sample",
        workspace_path=workspace,
        engine=LoopEngineConfig(
            repository="owner/sample",
            owner="owner",
            project_number=1,
            mission_issue=1,
        ),
        models=ModelConfig(
            implementer_provider=provider,
            implementer_model="default",
            reviewer_provider="openai",
            reviewer_model="reviewer",
            reviewer_api_base="https://api.openai.com/v1",
            implementer_profile=(
                "local-main"
                if provider == "local-llm-coder"
                else None
            ),
        ),
        secrets=SecretReferenceConfig(
            github_token_env="GH_TOKEN",
            reviewer_api_key_env="OPENAI_API_KEY",
            operational_store_dsn_env="LOOP_POSTGRES_DSN",
            trusted_reviewer_socket_env="LOOP_TRUSTED_REVIEWER_SOCKET",
        ),
        local_llm_coder=local,
    )


def test_backend_factory_keeps_codex_available(tmp_path: Path) -> None:
    item = settings(tmp_path, provider="codex", local=None)
    runner = FakeRunner("b" * 40)

    backend = build_implementer_backend(item, runner, environment())

    assert isinstance(backend, CodexProposalImplementer)


def test_backend_factory_selects_local_llm_coder(tmp_path: Path) -> None:
    local = LocalLlmCoderConfig(
        "http://127.0.0.1:8765",
        "product",
        "local-main",
    )
    item = settings(
        tmp_path,
        provider="local-llm-coder",
        local=local,
    )
    runner = FakeRunner("b" * 40)

    backend = build_implementer_backend(item, runner, environment())

    assert isinstance(backend, LocalLlmCoderImplementerAdapter)


def test_settings_load_local_backend_and_profile(tmp_path: Path) -> None:
    workspace = tmp_path / "product"
    config_path = tmp_path / "loop-engineering.ini"
    config_path.write_text(
        "[project]\n"
        "key = sample\n"
        f"workspace_path = {workspace}\n"
        "repository = owner/sample\n"
        "project_number = 1\n"
        "mission_issue = 1\n"
        "\n[models]\n"
        "implementer_provider = local-llm-coder\n"
        "implementer_model = compatibility-profile\n"
        "implementer_profile = local-main\n"
        "reviewer_provider = openai\n"
        "reviewer_model = reviewer\n"
        "\n[local_llm_coder]\n"
        "endpoint = http://127.0.0.1:8765\n"
        "production_name = product\n"
        "\n[credentials]\n"
        "\n[operational_store]\n"
        "\n[runtime]\n",
        encoding="utf-8",
    )

    loaded = LoopEngineeringSettings.load(
        tmp_path,
        {},
        config_path=config_path,
    )

    assert loaded.local_llm_coder == LocalLlmCoderConfig(
        "http://127.0.0.1:8765",
        "product",
        "local-main",
    )
    runtime = loaded.runtime_environment(
        {"LOOP_LOCAL_LLM_CODER_ROOT": "/stale/backend/path"}
    )
    assert runtime["LOOP_IMPLEMENTER_PROFILE"] == "local-main"
    assert runtime["LOOP_LOCAL_LLM_CODER_ENDPOINT"] == (
        "http://127.0.0.1:8765"
    )
    assert "LOOP_LOCAL_LLM_CODER_ROOT" not in runtime
    assert runtime["LOOP_LOCAL_LLM_CODER_PRODUCTION"] == "product"
    assert runtime["LOOP_LOCAL_LLM_CODER_MODEL_PROFILE"] == "local-main"


def test_local_config_falls_back_to_implementer_model(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "product"
    config_path = tmp_path / "loop-engineering.ini"
    config_path.write_text(
        "[project]\n"
        "key = sample\n"
        f"workspace_path = {workspace}\n"
        "repository = owner/sample\n"
        "project_number = 1\n"
        "mission_issue = 1\n"
        "\n[models]\n"
        "implementer_provider = local-llm-coder\n"
        "implementer_model = compatibility-profile\n"
        "reviewer_model = reviewer\n"
        "\n[local_llm_coder]\n"
        "endpoint = http://localhost:8765\n"
        "production_name = product\n"
        "\n[credentials]\n"
        "\n[operational_store]\n"
        "\n[runtime]\n",
        encoding="utf-8",
    )

    loaded = LoopEngineeringSettings.load(
        tmp_path,
        {},
        config_path=config_path,
    )

    assert loaded.local_llm_coder is not None
    assert loaded.local_llm_coder.model_profile == (
        "compatibility-profile"
    )


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://127.0.0.1:8765",
        "http://0.0.0.0:8765",
        "http://192.168.1.10:8765",
        "http://127.0.0.1:8765/path",
        "http://user@127.0.0.1:8765",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:70000",
    ),
)
def test_local_config_rejects_non_loopback_or_ambiguous_endpoint(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError, match="local_llm_coder.endpoint"):
        LocalLlmCoderConfig(endpoint, "product", "local-main")



def test_legacy_local_root_config_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "product"
    config_path = tmp_path / "loop-engineering.ini"
    config_path.write_text(
        "[project]\n"
        "key = sample\n"
        f"workspace_path = {workspace}\n"
        "repository = owner/sample\n"
        "project_number = 1\n"
        "mission_issue = 1\n"
        "\n[models]\n"
        "implementer_provider = local-llm-coder\n"
        "implementer_model = local-main\n"
        "reviewer_model = reviewer\n"
        "\n[local_llm_coder]\n"
        "root = /old/local-llm-coder\n"
        "production_name = product\n"
        "\n[credentials]\n"
        "\n[operational_store]\n"
        "\n[runtime]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="rootは廃止"):
        LoopEngineeringSettings.load(
            tmp_path,
            {},
            config_path=config_path,
        )
