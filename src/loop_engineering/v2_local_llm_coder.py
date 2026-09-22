"""local-llm-coder Worker BackendをV2 Implementer境界へ接続する。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .config import LocalLlmCoderConfig, LoopEngineeringSettings
from .v2_implementer import (
    CodexProposalImplementer,
    DevelopmentTaskPacket,
    ImplementerFinding,
    ImplementerResult,
    ImplementerStatus,
    ImplementerTransition,
    V2ImplementerPort,
    VerificationEvidence,
    WorkspaceEffectReport,
    validate_development_task_packet,
)

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_CHANGE_RE = re.compile(r"sha256:[0-9a-f]{64}")
_MAX_RESULT_BYTES = 2_000_000
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "request_identity",
        "task_packet_identity",
        "role",
        "input_target_identity",
        "result_target_identity",
        "change_identity",
        "status",
        "failure_kind",
        "completion",
        "findings",
        "changed_paths",
        "verification_evidence",
        "diagnostics",
        "session_id",
        "artifacts",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {"runtime_directory", "event_log", "stderr_log", "agent_artifact_refs"}
)
_COMPLETION_FIELDS = frozenset(
    {
        "scope_checked",
        "target_identity_checked",
        "work_finalized",
        "verification_finalized",
    }
)
_FAILURE_KINDS = frozenset(
    {
        "CONFIGURATION",
        "TARGET_PREFLIGHT",
        "RUNTIME_PREFLIGHT",
        "MALFORMED_EVENT_STREAM",
        "TARGET_READBACK",
        "PROCESS_TIMEOUT",
        "PROCESS_INTERRUPTED",
        "PROCESS_EXIT",
        "MALFORMED_AGENT_RESULT",
        "REVIEWER_MUTATION_DETECTED",
        "SCOPE_VIOLATION",
        "LINEAGE_VIOLATION",
        "REQUIRED_EFFECT_MISSING",
        "UNEXPECTED_EFFECT",
        "AGENT_REPORTED_INCOMPLETE",
        "AGENT_REPORTED_BLOCKED",
        "AGENT_REPORTED_FAILED",
    }
)
_BASE_ENV_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "TERM",
        "COLORTERM",
        "PYENV_ROOT",
        "PYENV_VERSION",
        "LOCAL_LLM_CODER_PROFILE_CONFIG",
    }
)
_FORBIDDEN_ENV_NAMES = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "LOOP_POSTGRES_DSN",
        "LOOP_DATABASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_API_KEY_REVIEWER",
        "LOOP_TRUSTED_REVIEWER_SOCKET",
    }
)


@dataclass(frozen=True, slots=True)
class _ParsedWorkerResult:
    status: str
    failure_kind: str | None
    result_target_identity: str | None
    change_identity: str | None
    changed_paths: tuple[str, ...]
    verification_evidence: tuple[VerificationEvidence, ...]
    diagnostics: tuple[str, ...]


class LocalLlmCoderImplementerAdapter:
    """1回のDESIGN / IMPLEMENT / REPAIRをfile-based Worker CLIへ委譲する。"""

    def __init__(
        self,
        runner: Any,
        config: LocalLlmCoderConfig,
        workspace_path: Path,
        environment: Mapping[str, str],
        *,
        timeout_seconds: int = 1800,
    ) -> None:
        if timeout_seconds < 1 or timeout_seconds > 7200:
            raise ValueError("LOCAL_LLM_CODER_TIMEOUT_INVALID")
        self._runner = runner
        self._config = config
        self._workspace = workspace_path.resolve(strict=False)
        self._environment = _sanitized_environment(environment)
        self._timeout_seconds = timeout_seconds
        self._root = config.root.resolve(strict=False)
        self._script = self._root / "scripts" / "run-worker.sh"

    def execute(self, packet: DevelopmentTaskPacket) -> ImplementerResult:
        validation = validate_development_task_packet(packet)
        if validation is not None:
            return _blocked(validation)

        local_validation = _validate_local_packet(packet)
        if local_validation is not None:
            return _blocked(local_validation)

        workspace = packet.workspace_canonical_path.resolve(strict=False)
        if (
            workspace != self._workspace
            or workspace != self._config.active_production_path
        ):
            return _blocked("LOCAL_WORKSPACE_IDENTITY_MISMATCH")
        if (
            not self._root.is_dir()
            or not self._script.is_file()
            or not os.access(self._script, os.X_OK)
        ):
            return _failed(
                "LOCAL_LLM_CODER_UNAVAILABLE",
                failure_kind="PROVIDER_UNAVAILABLE",
            )

        pre_head = self._git_head(workspace)
        if pre_head is None:
            return _blocked("LOCAL_WORKSPACE_PREFLIGHT_FAILED")
        if pre_head != packet.exact_base_sha:
            return _blocked("LOCAL_TARGET_IDENTITY_MISMATCH")

        request = _request_payload(packet, self._config.model_profile)
        request_identity = _required_string(request["request_identity"])
        try:
            with tempfile.TemporaryDirectory(prefix="loop-local-worker-") as temporary:
                temporary_root = Path(temporary)
                request_path = temporary_root / "request.json"
                result_path = temporary_root / "result.json"
                request_path.write_text(
                    json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                try:
                    process = self._runner.run(
                        (
                            str(self._script),
                            self._config.production_name,
                            "--request",
                            str(request_path),
                            "--result",
                            str(result_path),
                        ),
                        cwd=self._root,
                        environment=self._environment,
                        timeout_seconds=self._timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    return ImplementerResult(
                        ImplementerStatus.INCOMPLETE,
                        "LOCAL_WORKER_TIMEOUT",
                        failure_kind="PROCESS_TIMEOUT",
                    )
                except (OSError, subprocess.SubprocessError):
                    return _failed(
                        "LOCAL_WORKER_UNAVAILABLE",
                        failure_kind="PROVIDER_UNAVAILABLE",
                    )

                if not result_path.is_file():
                    detail = (
                        "LOCAL_WORKER_PROCESS_FAILED"
                        if process.returncode != 0
                        else "LOCAL_WORKER_RESULT_MISSING"
                    )
                    return _failed(detail, failure_kind="PROCESS_EXIT")

                try:
                    parsed = _read_worker_result(
                        result_path,
                        packet=packet,
                        request_identity=request_identity,
                    )
                except (OSError, UnicodeError, ValueError):
                    return _failed(
                        "LOCAL_WORKER_RESULT_MALFORMED",
                        failure_kind="MALFORMED_AGENT_RESULT",
                    )
        except OSError:
            return _failed(
                "LOCAL_WORKER_TEMPORARY_IO_FAILED",
                failure_kind="RUNTIME_PREFLIGHT",
            )

        if parsed.result_target_identity is not None:
            post_head = self._git_head(workspace)
            if post_head != parsed.result_target_identity:
                return _failed(
                    "LOCAL_WORKER_TARGET_READBACK_MISMATCH",
                    failure_kind="TARGET_READBACK",
                )

        if process.returncode != 0 and parsed.status == "PASS":
            return _failed(
                "LOCAL_WORKER_PROCESS_RESULT_CONFLICT",
                failure_kind="PROCESS_EXIT",
            )

        if parsed.status == "PASS":
            assert parsed.result_target_identity is not None
            assert parsed.change_identity is not None
            effect = WorkspaceEffectReport(
                request_identity=request_identity,
                packet_identity=packet.packet_identity,
                work_identity=packet.work_identity,
                transition=packet.transition,
                input_target_identity=packet.exact_base_sha,
                result_target_identity=parsed.result_target_identity,
                change_identity=parsed.change_identity,
                changed_paths=parsed.changed_paths,
                verification_evidence=parsed.verification_evidence,
                diagnostics=parsed.diagnostics,
            )
            return ImplementerResult(
                ImplementerStatus.SUCCESS,
                "LOCAL_WORKER_EFFECT_READY",
                workspace_effect=effect,
                diagnostics=parsed.diagnostics,
            )
        if parsed.status == "INCOMPLETE":
            return ImplementerResult(
                ImplementerStatus.INCOMPLETE,
                "LOCAL_WORKER_INCOMPLETE",
                failure_kind=parsed.failure_kind,
                diagnostics=parsed.diagnostics,
            )
        if parsed.status == "BLOCKED":
            return ImplementerResult(
                ImplementerStatus.BLOCKED,
                "LOCAL_WORKER_BLOCKED",
                failure_kind=parsed.failure_kind,
                diagnostics=parsed.diagnostics,
            )
        return _failed(
            "LOCAL_WORKER_FAILED",
            failure_kind=parsed.failure_kind,
            diagnostics=parsed.diagnostics,
        )

    def _git_head(self, workspace: Path) -> str | None:
        try:
            result = self._runner.run(
                ("git", "-C", str(workspace), "rev-parse", "HEAD"),
                cwd=workspace,
                environment=self._environment,
                timeout_seconds=120,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if not result.succeeded:
            return None
        value = result.output.strip()
        return value if _SHA_RE.fullmatch(value) is not None else None


def build_implementer_backend(
    settings: LoopEngineeringSettings,
    runner: Any,
    environment: Mapping[str, str],
    *,
    codex_argv_prefix: Sequence[str] = ("codex", "exec"),
    timeout_seconds: int = 1200,
) -> V2ImplementerPort:
    """設定されたproviderから交換可能Implementer Adapterを構成する。"""

    provider = settings.models.implementer_provider
    if provider == "codex":
        return CodexProposalImplementer(
            runner,
            codex_argv_prefix,
            environment,
            timeout_seconds=timeout_seconds,
        )
    if provider == "local-llm-coder":
        if settings.local_llm_coder is None:
            raise ValueError("LOCAL_LLM_CODER_CONFIG_REQUIRED")
        return LocalLlmCoderImplementerAdapter(
            runner,
            settings.local_llm_coder,
            settings.workspace_path,
            environment,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError("IMPLEMENTER_PROVIDER_UNSUPPORTED")


def _validate_local_packet(packet: DevelopmentTaskPacket) -> str | None:
    if packet.transition is ImplementerTransition.REPAIR:
        if packet.expected_change_identity is None:
            return "LOCAL_REPAIR_CHANGE_IDENTITY_REQUIRED"
        if not packet.approved_findings:
            return "LOCAL_REPAIR_FINDINGS_REQUIRED"
    elif packet.approved_findings:
        return "LOCAL_APPROVED_FINDINGS_UNEXPECTED"

    if packet.transition is ImplementerTransition.DESIGN:
        if any(
            not _path_in_scope(target, packet.scope_paths)
            for target in packet.canonical_design_targets
        ):
            return "LOCAL_DESIGN_SCOPE_INVALID"

    for finding in packet.approved_findings:
        if finding.severity not in {"BLOCKING", "NON_BLOCKING"}:
            return "LOCAL_FINDING_SEVERITY_INVALID"
        if not _safe_relative_path(finding.path):
            return "LOCAL_FINDING_PATH_INVALID"
        if not _path_in_scope(finding.path, packet.scope_paths):
            return "LOCAL_FINDING_SCOPE_VIOLATION"
        values = (
            finding.finding_identity,
            finding.location,
            finding.problem,
            finding.basis,
            finding.evidence,
            finding.impact,
            finding.suggested_fix,
        )
        if any(not item.strip() for item in values):
            return "LOCAL_FINDING_INVALID"
    return None


def _request_payload(
    packet: DevelopmentTaskPacket,
    model_profile: str,
) -> dict[str, object]:
    role = "FIXER" if packet.transition is ImplementerTransition.REPAIR else "IMPLEMENTER"
    request_identity = _request_identity(packet, role, model_profile)
    return {
        "schema_version": 1,
        "request_identity": request_identity,
        "task_packet_identity": packet.packet_identity,
        "role": role,
        "transition": packet.transition.value,
        "effect_requirement": "MUST_CHANGE",
        "repository_identity": packet.repository_identity,
        "workspace_canonical_path": str(packet.workspace_canonical_path.resolve(strict=False)),
        "input_target_identity": packet.exact_base_sha,
        "exact_base_sha": packet.exact_base_sha,
        "expected_change_identity": packet.expected_change_identity,
        "active_lineage_identity": packet.active_lineage_identity,
        "authority_refs": list(packet.authority_refs),
        "scope_paths": list(_worker_scope(packet)),
        "canonical_refs": list(packet.canonical_design_identities),
        "acceptance_checks": list(packet.acceptance_checks),
        "non_goals": list(packet.non_goals),
        "safety_constraints": list(packet.safety_constraints),
        "model_profile": model_profile,
        "task": _task_text(packet),
        "approved_findings": [_finding_payload(item) for item in packet.approved_findings],
    }


def _request_identity(
    packet: DevelopmentTaskPacket,
    role: str,
    model_profile: str,
) -> str:
    payload = {
        "schema_version": 1,
        "task_packet_identity": packet.packet_identity,
        "role": role,
        "transition": packet.transition.value,
        "repository_identity": packet.repository_identity,
        "input_target_identity": packet.exact_base_sha,
        "expected_change_identity": packet.expected_change_identity,
        "active_lineage_identity": packet.active_lineage_identity,
        "model_profile": model_profile,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "local-worker:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _task_text(packet: DevelopmentTaskPacket) -> str:
    acceptance = "\n".join(f"- {item}" for item in packet.acceptance_checks) or "- なし"
    design_targets = (
        "\n".join(f"- {item}" for item in packet.canonical_design_targets)
        or "- なし"
    )
    design_identities = (
        "\n".join(f"- {item}" for item in packet.canonical_design_identities)
        or "- 未確定"
    )
    transition_instruction = {
        ImplementerTransition.DESIGN: (
            "最終受入条件を満たすための設計をcanonical design targetへ記録する。"
            "この段階ではProduct実装codeを変更しない。"
        ),
        ImplementerTransition.IMPLEMENT: (
            "canonical design targetを読み、確定済み設計に従って実装する。"
            "最終受入条件のverificationを完了する。"
        ),
        ImplementerTransition.REPAIR: (
            "承認済みfindingだけを同一lineageで修正し、"
            "最終受入条件のverificationを完了する。"
        ),
    }[packet.transition]
    return (
        f"Loop Engineering V2の{packet.transition.value}を1回実行する。\n"
        f"Work: {packet.work_identity}\n"
        f"{transition_instruction}\n"
        "canonical design target:\n"
        f"{design_targets}\n"
        "canonical design identity:\n"
        f"{design_identities}\n"
        "最終Product受入条件:\n"
        f"{acceptance}"
    )


def _finding_payload(finding: ImplementerFinding) -> dict[str, str]:
    return {
        "finding_identity": finding.finding_identity,
        "severity": finding.severity,
        "path": finding.path,
        "location": finding.location,
        "problem": finding.problem,
        "basis": finding.basis,
        "evidence": finding.evidence,
        "impact": finding.impact,
        "suggested_fix": finding.suggested_fix,
    }


def _read_worker_result(
    path: Path,
    *,
    packet: DevelopmentTaskPacket,
    request_identity: str,
) -> _ParsedWorkerResult:
    if path.stat().st_size > _MAX_RESULT_BYTES:
        raise ValueError("result too large")
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    value = _object_mapping(raw)
    if value is None or set(value) != _RESULT_FIELDS:
        raise ValueError("result fields invalid")
    if value.get("schema_version") != 1:
        raise ValueError("schema version invalid")

    role = "FIXER" if packet.transition is ImplementerTransition.REPAIR else "IMPLEMENTER"
    echoes = {
        "request_identity": request_identity,
        "task_packet_identity": packet.packet_identity,
        "role": role,
        "input_target_identity": packet.exact_base_sha,
    }
    for field, expected in echoes.items():
        if value.get(field) != expected:
            raise ValueError("result identity mismatch")

    status = _required_string(value.get("status"))
    if status not in {"PASS", "INCOMPLETE", "BLOCKED", "FAILED"}:
        raise ValueError("worker status invalid")
    failure_value = value.get("failure_kind")
    if status == "PASS":
        if failure_value is not None:
            raise ValueError("PASS failure kind invalid")
        failure_kind = None
    else:
        failure_kind = _required_string(failure_value)
        if failure_kind not in _FAILURE_KINDS:
            raise ValueError("failure kind invalid")

    completion = _object_mapping(value.get("completion"))
    if completion is None or set(completion) != _COMPLETION_FIELDS:
        raise ValueError("completion invalid")
    if not all(isinstance(item, bool) for item in completion.values()):
        raise ValueError("completion type invalid")
    if status == "PASS" and not all(item is True for item in completion.values()):
        raise ValueError("PASS completion invalid")

    findings = value.get("findings")
    if not isinstance(findings, list) or findings:
        raise ValueError("implementer findings invalid")

    changed_paths = _string_tuple(value.get("changed_paths"))
    if len(set(changed_paths)) != len(changed_paths):
        raise ValueError("changed path duplicate")
    if any(
        not _safe_relative_path(item) or not _path_in_scope(item, _worker_scope(packet))
        for item in changed_paths
    ):
        raise ValueError("changed path invalid")
    if status == "PASS" and not changed_paths:
        raise ValueError("MUST_CHANGE effect missing")

    verification = _verification_tuple(value.get("verification_evidence"))
    if (
        status == "PASS"
        and packet.transition is not ImplementerTransition.DESIGN
        and packet.acceptance_checks
    ):
        if not verification or any(item.status != "PASS" for item in verification):
            raise ValueError("PASS verification invalid")

    diagnostics = _string_tuple(value.get("diagnostics"))
    if status != "PASS" and not diagnostics:
        raise ValueError("failure diagnostics missing")

    result_target = _optional_string(value.get("result_target_identity"))
    change_identity = _optional_string(value.get("change_identity"))
    if status == "PASS":
        if result_target is None or _SHA_RE.fullmatch(result_target) is None:
            raise ValueError("result target invalid")
        if change_identity is None or _CHANGE_RE.fullmatch(change_identity) is None:
            raise ValueError("change identity invalid")

    session_id = value.get("session_id")
    if session_id is not None:
        _required_string(session_id)

    artifacts = _object_mapping(value.get("artifacts"))
    if artifacts is None or set(artifacts) != _ARTIFACT_FIELDS:
        raise ValueError("artifacts invalid")
    for field in ("runtime_directory", "event_log", "stderr_log"):
        _optional_string(artifacts.get(field))
    _string_tuple(artifacts.get("agent_artifact_refs"))

    return _ParsedWorkerResult(
        status=status,
        failure_kind=failure_kind,
        result_target_identity=result_target,
        change_identity=change_identity,
        changed_paths=changed_paths,
        verification_evidence=verification,
        diagnostics=diagnostics,
    )


def _verification_tuple(value: object) -> tuple[VerificationEvidence, ...]:
    if not isinstance(value, list):
        raise ValueError("verification invalid")
    results: list[VerificationEvidence] = []
    for item in value:
        mapping = _object_mapping(item)
        if mapping is None or set(mapping) != {"command", "status", "summary"}:
            raise ValueError("verification fields invalid")
        status = _required_string(mapping.get("status"))
        if status not in {"PASS", "FAIL", "NOT_RUN"}:
            raise ValueError("verification status invalid")
        results.append(
            VerificationEvidence(
                command=_required_string(mapping.get("command")),
                status=status,
                summary=_required_string(mapping.get("summary")),
            )
        )
    return tuple(results)


def _object_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        result[key] = item
    return result


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("string list invalid")
    return tuple(_required_string(item) for item in value)


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("string invalid")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _required_string(value)


def _safe_relative_path(value: str) -> bool:
    if not value or value != value.strip() or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and value not in {".", "./"}


def _worker_scope(packet: DevelopmentTaskPacket) -> tuple[str, ...]:
    if packet.transition is ImplementerTransition.DESIGN:
        return packet.canonical_design_targets
    return packet.scope_paths


def _path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    for scope in scopes:
        normalized = scope.rstrip("/")
        if normalized in {"", "."}:
            return True
        if path == normalized or path.startswith(normalized + "/"):
            return True
    return False


def _sanitized_environment(environment: Mapping[str, str]) -> dict[str, str]:
    allowed = set(_BASE_ENV_NAMES)
    allowed.update(name for name in environment if name.startswith("LC_"))
    allowed.update(name for name in environment if name.startswith("XDG_"))
    return {
        name: value
        for name, value in environment.items()
        if name in allowed and name not in _FORBIDDEN_ENV_NAMES and "REVIEWER" not in name
    }


def _blocked(detail: str) -> ImplementerResult:
    return ImplementerResult(ImplementerStatus.BLOCKED, detail)


def _failed(
    detail: str,
    *,
    failure_kind: str | None,
    diagnostics: tuple[str, ...] = (),
) -> ImplementerResult:
    return ImplementerResult(
        ImplementerStatus.FAILED,
        detail,
        failure_kind=failure_kind,
        diagnostics=diagnostics,
    )
