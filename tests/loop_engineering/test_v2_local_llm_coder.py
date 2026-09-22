import json
import subprocess
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


@dataclass(frozen=True)
class Result:
    returncode: int = 0
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class FakeRunner:
    def __init__(
        self,
        head: str,
        *,
        result_mode: str = "pass",
        process_returncode: int = 0,
        raise_error: BaseException | None = None,
    ) -> None:
        self.head = head
        self.result_mode = result_mode
        self.process_returncode = process_returncode
        self.raise_error = raise_error
        self.worker_calls = 0
        self.worker_environment: Mapping[str, str] | None = None
        self.request: dict[str, Any] | None = None

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> Result:
        del timeout_seconds, capture_output
        values = tuple(command)
        if values[0] == "git":
            assert values[-2:] == ("rev-parse", "HEAD")
            return Result(output=self.head)

        self.worker_calls += 1
        self.worker_environment = environment
        if self.raise_error is not None:
            raise self.raise_error

        request_path = Path(values[values.index("--request") + 1])
        result_path = Path(values[values.index("--result") + 1])
        self.request = json.loads(request_path.read_text(encoding="utf-8"))
        if self.result_mode == "missing":
            return Result(self.process_returncode)
        if self.result_mode == "malformed":
            result_path.write_text("not-json", encoding="utf-8")
            return Result(self.process_returncode)

        payload = worker_result(self.request)
        if self.result_mode == "identity-mismatch":
            payload["request_identity"] = "wrong"
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return Result(self.process_returncode)


def worker_result(request: Mapping[str, Any]) -> dict[str, Any]:
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
        "changed_paths": ["src/app.py"],
        "verification_evidence": [
            {"command": "test", "status": "PASS", "summary": "ok"}
        ],
        "diagnostics": [],
        "session_id": "session-1",
        "artifacts": {
            "runtime_directory": "/tmp/runtime",
            "event_log": "/tmp/runtime/events.ndjson",
            "stderr_log": "/tmp/runtime/stderr.log",
            "agent_artifact_refs": [],
        },
    }


def local_layout(tmp_path: Path) -> tuple[Path, Path, LocalLlmCoderConfig]:
    root = tmp_path / "local-llm-coder"
    script = root / "scripts" / "run-worker.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    script.chmod(0o755)
    workspace = root / "productions" / "product"
    workspace.mkdir(parents=True)
    config = LocalLlmCoderConfig(root.resolve(), "product", "local-main")
    return root, workspace.resolve(), config


def packet(workspace: Path, transition: ImplementerTransition) -> DevelopmentTaskPacket:
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
        "LOCAL_LLM_CODER_PROFILE_CONFIG": "/tmp/local-profiles.json",
        "GH_TOKEN": "secret-gh",
        "OPENAI_API_KEY": "secret-openai",
        "OPENAI_API_KEY_REVIEWER": "secret-reviewer",
        "LOOP_POSTGRES_DSN": "secret-db",
        "LOOP_TRUSTED_REVIEWER_SOCKET": "secret-socket",
    }


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
    _root, workspace, config = local_layout(tmp_path)
    task = packet(workspace, transition)
    runner = FakeRunner(task.exact_base_sha)

    result = LocalLlmCoderImplementerAdapter(
        runner,
        config,
        environment(),
    ).execute(task)

    assert result.status is ImplementerStatus.SUCCESS
    assert result.workspace_effect is not None
    assert result.proposal is None
    assert result.workspace_effect.changed_paths == ("src/app.py",)
    assert runner.request is not None
    assert runner.request["role"] == expected_role
    assert runner.request["transition"] == transition.value
    assert runner.request["effect_requirement"] == "MUST_CHANGE"
    assert runner.request["model_profile"] == "local-main"


def test_local_backend_repair_binds_change_identity_and_findings(tmp_path: Path) -> None:
    _root, workspace, config = local_layout(tmp_path)
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

    result = LocalLlmCoderImplementerAdapter(runner, config, environment()).execute(task)

    assert result.status is ImplementerStatus.SUCCESS
    assert runner.request is not None
    assert runner.request["role"] == "FIXER"
    assert runner.request["expected_change_identity"] == task.expected_change_identity
    assert runner.request["approved_findings"] == [
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


def test_local_backend_rejects_workspace_mismatch_before_worker(tmp_path: Path) -> None:
    _root, workspace, config = local_layout(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    task = replace(
        packet(workspace, ImplementerTransition.IMPLEMENT),
        workspace_canonical_path=other.resolve(),
    )
    runner = FakeRunner(task.exact_base_sha)

    result = LocalLlmCoderImplementerAdapter(runner, config, environment()).execute(task)

    assert result.status is ImplementerStatus.BLOCKED
    assert result.detail == "LOCAL_WORKSPACE_IDENTITY_MISMATCH"
    assert runner.worker_calls == 0


@pytest.mark.parametrize("mode", ["malformed", "identity-mismatch"])
def test_local_backend_rejects_malformed_or_mismatched_result(
    tmp_path: Path,
    mode: str,
) -> None:
    _root, workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha, result_mode=mode)

    result = LocalLlmCoderImplementerAdapter(runner, config, environment()).execute(task)

    assert result.status is ImplementerStatus.FAILED
    assert result.detail == "LOCAL_WORKER_RESULT_MALFORMED"


def test_local_backend_timeout_is_incomplete(tmp_path: Path) -> None:
    _root, workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(
        task.exact_base_sha,
        raise_error=subprocess.TimeoutExpired("worker", 1),
    )

    result = LocalLlmCoderImplementerAdapter(runner, config, environment()).execute(task)

    assert result.status is ImplementerStatus.INCOMPLETE
    assert result.detail == "LOCAL_WORKER_TIMEOUT"
    assert result.failure_kind == "PROCESS_TIMEOUT"


def test_local_backend_provider_unavailable_is_failed(tmp_path: Path) -> None:
    _root, workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha, raise_error=OSError("unavailable"))

    result = LocalLlmCoderImplementerAdapter(runner, config, environment()).execute(task)

    assert result.status is ImplementerStatus.FAILED
    assert result.detail == "LOCAL_WORKER_UNAVAILABLE"


def test_process_exit_zero_without_result_is_not_success(tmp_path: Path) -> None:
    _root, workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha, result_mode="missing")

    result = LocalLlmCoderImplementerAdapter(runner, config, environment()).execute(task)

    assert result.status is ImplementerStatus.FAILED
    assert result.detail == "LOCAL_WORKER_RESULT_MISSING"


def test_nonzero_process_cannot_promote_pass_result(tmp_path: Path) -> None:
    _root, workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha, process_returncode=7)

    result = LocalLlmCoderImplementerAdapter(runner, config, environment()).execute(task)

    assert result.status is ImplementerStatus.FAILED
    assert result.detail == "LOCAL_WORKER_PROCESS_RESULT_CONFLICT"


def test_local_backend_strips_control_plane_secrets(tmp_path: Path) -> None:
    _root, workspace, config = local_layout(tmp_path)
    task = packet(workspace, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(task.exact_base_sha)

    result = LocalLlmCoderImplementerAdapter(runner, config, environment()).execute(task)

    assert result.status is ImplementerStatus.SUCCESS
    assert runner.worker_environment == {
        "PATH": "/usr/bin",
        "HOME": "/tmp/home",
        "PYENV_ROOT": "/tmp/pyenv",
        "LOCAL_LLM_CODER_PROFILE_CONFIG": "/tmp/local-profiles.json",
    }
    request_text = json.dumps(runner.request, ensure_ascii=False)
    assert "secret-gh" not in request_text
    assert "secret-openai" not in request_text
    assert "secret-db" not in request_text
    assert "secret-socket" not in request_text


def settings(
    tmp_path: Path,
    *,
    provider: str,
    local: LocalLlmCoderConfig | None,
) -> LoopEngineeringSettings:
    workspace = (
        local.active_production_path
        if local is not None
        else (tmp_path / "workspace").resolve()
    )
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
            implementer_profile="local-main" if provider == "local-llm-coder" else None,
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
    _root, _workspace, local = local_layout(tmp_path)
    item = settings(tmp_path, provider="local-llm-coder", local=local)
    runner = FakeRunner("b" * 40)

    backend = build_implementer_backend(item, runner, environment())

    assert isinstance(backend, LocalLlmCoderImplementerAdapter)


def test_settings_load_local_backend_and_profile(tmp_path: Path) -> None:
    root, workspace, local = local_layout(tmp_path)
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
        f"root = {root}\n"
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

    assert loaded.local_llm_coder == local
    runtime = loaded.runtime_environment({})
    assert runtime["LOOP_IMPLEMENTER_PROFILE"] == "local-main"
    assert runtime["LOOP_LOCAL_LLM_CODER_ROOT"] == str(local.root)
    assert runtime["LOOP_LOCAL_LLM_CODER_PRODUCTION"] == "product"
    assert runtime["LOOP_LOCAL_LLM_CODER_MODEL_PROFILE"] == "local-main"


def test_local_config_falls_back_to_implementer_model(tmp_path: Path) -> None:
    root, workspace, _local = local_layout(tmp_path)
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
        f"root = {root}\n"
        "production_name = product\n"
        "\n[credentials]\n"
        "\n[operational_store]\n"
        "\n[runtime]\n",
        encoding="utf-8",
    )

    loaded = LoopEngineeringSettings.load(tmp_path, {}, config_path=config_path)

    assert loaded.local_llm_coder is not None
    assert loaded.local_llm_coder.model_profile == "compatibility-profile"
