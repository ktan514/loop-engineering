"""V2 Local Quality Loopをexact targetへbindして自動反復する。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .config import LocalLlmCoderConfig
from .v2_local_worker_http import (
    LocalWorkerHttpFailure,
    LocalWorkerHttpTimeout,
    post_worker_request,
)
from .v2_implementer import (
    DevelopmentTaskPacket,
    ImplementerFinding,
    ImplementerStatus,
    ImplementerTransition,
    V2ImplementerPort,
)

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_CHANGE_RE = re.compile(r"sha256:[0-9a-f]{64}")
_REVIEW_COMPLETION_FIELDS = frozenset(
    {
        "scope_checked",
        "target_identity_checked",
        "work_finalized",
        "verification_finalized",
        "review_scope_checked",
        "canonical_checked",
        "blocking_findings_finalized",
        "non_blocking_findings_finalized",
        "unverified_finalized",
        "final_verdict_present",
    }
)
_WORKER_RESULT_FIELDS = frozenset(
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
_FINDING_FIELDS = frozenset(
    {
        "finding_identity",
        "severity",
        "path",
        "location",
        "problem",
        "basis",
        "evidence",
        "impact",
        "suggested_fix",
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
    }
)


class LocalQualityStage(str, Enum):
    VERIFY_LOCAL = "VERIFY_LOCAL"
    LOCAL_REVIEW = "LOCAL_REVIEW"
    REPAIR = "REPAIR"
    LOCAL_PASS = "LOCAL_PASS"
    BLOCKED = "BLOCKED"


class LocalQualityStatus(str, Enum):
    PASS = "PASS"
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"


class LocalVerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"


class LocalReviewStatus(str, Enum):
    PASS = "PASS"
    FINDINGS = "FINDINGS"
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class CommandResultLike(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def output(self) -> str: ...

    @property
    def succeeded(self) -> bool: ...


class LocalQualityCommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> CommandResultLike: ...


class LocalQualityDatabase(Protocol):
    def execute_sql(self, sql: str) -> bool: ...

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None: ...


@dataclass(frozen=True, slots=True)
class LocalQualityTarget:
    repository_identity: str
    work_identity: str
    exact_head_sha: str
    change_identity: str
    active_lineage_identity: str
    canonical_design_identities: tuple[str, ...]
    acceptance_digest: str
    scope_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if "/" not in self.repository_identity or not self.work_identity:
            raise ValueError("LOCAL_QUALITY_TARGET_IDENTITY_INVALID")
        if _SHA_RE.fullmatch(self.exact_head_sha) is None:
            raise ValueError("LOCAL_QUALITY_TARGET_HEAD_INVALID")
        if _CHANGE_RE.fullmatch(self.change_identity) is None:
            raise ValueError("LOCAL_QUALITY_CHANGE_IDENTITY_INVALID")
        if not self.active_lineage_identity:
            raise ValueError("LOCAL_QUALITY_LINEAGE_REQUIRED")
        if not self.canonical_design_identities:
            raise ValueError("LOCAL_QUALITY_DESIGN_REQUIRED")
        if not self.acceptance_digest:
            raise ValueError("LOCAL_QUALITY_ACCEPTANCE_REQUIRED")
        if not self.scope_paths or any(not _safe_scope_path(item) for item in self.scope_paths):
            raise ValueError("LOCAL_QUALITY_SCOPE_INVALID")


@dataclass(frozen=True, slots=True)
class VerificationCommandDescriptor:
    identity: str
    argv: tuple[str, ...]
    working_directory: str = "."
    timeout_seconds: int = 1200
    required: bool = True

    def __post_init__(self) -> None:
        if not self.identity.strip() or not self.argv or any(not item for item in self.argv):
            raise ValueError("LOCAL_VERIFICATION_COMMAND_INVALID")
        if self.timeout_seconds < 1 or self.timeout_seconds > 7200:
            raise ValueError("LOCAL_VERIFICATION_TIMEOUT_INVALID")
        if self.working_directory != "." and not _safe_relative_path(self.working_directory):
            raise ValueError("LOCAL_VERIFICATION_WORKDIR_INVALID")


@dataclass(frozen=True, slots=True)
class VerificationCommandOutcome:
    identity: str
    status: str
    returncode: int | None
    summary: str


@dataclass(frozen=True, slots=True)
class LocalVerificationResult:
    status: LocalVerificationStatus
    evidence_identity: str
    outcomes: tuple[VerificationCommandOutcome, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalReviewCompletion:
    scope_checked: bool
    target_identity_checked: bool
    work_finalized: bool
    verification_finalized: bool
    review_scope_checked: bool
    canonical_checked: bool
    blocking_findings_finalized: bool
    non_blocking_findings_finalized: bool
    unverified_finalized: bool
    final_verdict_present: bool

    @property
    def complete(self) -> bool:
        return all(
            (
                self.scope_checked,
                self.target_identity_checked,
                self.work_finalized,
                self.verification_finalized,
                self.review_scope_checked,
                self.canonical_checked,
                self.blocking_findings_finalized,
                self.non_blocking_findings_finalized,
                self.unverified_finalized,
                self.final_verdict_present,
            )
        )


@dataclass(frozen=True, slots=True)
class LocalReviewExecutionResult:
    status: LocalReviewStatus
    request_identity: str
    result_target_identity: str | None
    change_identity: str | None
    completion: LocalReviewCompletion | None
    findings: tuple[ImplementerFinding, ...]
    failure_kind: str | None
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LocalFindingValidation:
    approved_blocking: tuple[ImplementerFinding, ...]
    approved_non_blocking: tuple[ImplementerFinding, ...]
    duplicate_identities: tuple[str, ...]
    rejected_identities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LocalQualityState:
    work_identity: str
    target_head_sha: str
    change_identity: str
    stage: LocalQualityStage
    review_cycle: int = 0
    no_progress_count: int = 0
    last_progress_fingerprint: str | None = None
    verification_identity: str | None = None
    review_request_key: str | None = None
    review_identity: str | None = None
    local_pass_identity: str | None = None
    approved_findings: tuple[ImplementerFinding, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalQualityContext:
    target: LocalQualityTarget
    workspace_canonical_path: Path
    packet_identity: str
    generation: int
    goal_revision: str
    issue_revision: str
    acceptance_checks: tuple[str, ...]
    authority_refs: tuple[str, ...]
    non_goals: tuple[str, ...]
    safety_constraints: tuple[str, ...]
    verification_commands: tuple[VerificationCommandDescriptor, ...]

    def __post_init__(self) -> None:
        if not self.workspace_canonical_path.is_absolute():
            raise ValueError("LOCAL_QUALITY_WORKSPACE_INVALID")
        if not self.packet_identity or self.generation < 1:
            raise ValueError("LOCAL_QUALITY_PACKET_INVALID")
        if not self.goal_revision or not self.issue_revision:
            raise ValueError("LOCAL_QUALITY_REVISION_INVALID")
        if not self.acceptance_checks or any(not item.strip() for item in self.acceptance_checks):
            raise ValueError("LOCAL_QUALITY_ACCEPTANCE_CHECK_INVALID")
        if not self.verification_commands:
            raise ValueError("LOCAL_VERIFICATION_POLICY_EMPTY")


@dataclass(frozen=True, slots=True)
class LocalQualityResult:
    status: LocalQualityStatus
    detail: str
    target: LocalQualityTarget
    local_pass_identity: str | None = None
    approved_findings: tuple[ImplementerFinding, ...] = ()


class LocalQualityStorePort(Protocol):
    def get(self, work_identity: str) -> LocalQualityState | None: ...

    def save(self, state: LocalQualityState) -> None: ...


class LocalVerificationPort(Protocol):
    def verify(self, context: LocalQualityContext) -> LocalVerificationResult: ...


class LocalReviewerPort(Protocol):
    def review(self, context: LocalQualityContext) -> LocalReviewExecutionResult: ...


class PostgreSQLLocalQualityStore:
    def __init__(self, database: LocalQualityDatabase) -> None:
        self._database = database

    def get(self, work_identity: str) -> LocalQualityState | None:
        rows = self._database.query_json_rows(
            "SELECT work_identity, target_head_sha, change_identity, stage, review_cycle, "
            "no_progress_count, last_progress_fingerprint, verification_identity, "
            "review_request_key, review_identity, local_pass_identity, approved_findings, "
            "diagnostics FROM loop_local_quality_state "
            f"WHERE work_identity = {_literal(work_identity)} LIMIT 1"
        )
        if rows is None:
            raise RuntimeError("LOCAL_QUALITY_STATE_READ_FAILED")
        if not rows:
            return None
        return _state_from_row(rows[0])

    def save(self, state: LocalQualityState) -> None:
        findings = _literal(
            json.dumps(
                [_finding_payload(item) for item in state.approved_findings],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        diagnostics = _literal(
            json.dumps(state.diagnostics, ensure_ascii=False, separators=(",", ":"))
        )
        sql = (
            "INSERT INTO loop_local_quality_state "
            "(work_identity, target_head_sha, change_identity, stage, review_cycle, "
            "no_progress_count, last_progress_fingerprint, verification_identity, "
            "review_request_key, review_identity, local_pass_identity, approved_findings, "
            "diagnostics) VALUES ("
            f"{_literal(state.work_identity)}, {_literal(state.target_head_sha)}, "
            f"{_literal(state.change_identity)}, {_literal(state.stage.value)}, "
            f"{state.review_cycle}, {state.no_progress_count}, "
            f"{_nullable_literal(state.last_progress_fingerprint)}, "
            f"{_nullable_literal(state.verification_identity)}, "
            f"{_nullable_literal(state.review_request_key)}, "
            f"{_nullable_literal(state.review_identity)}, "
            f"{_nullable_literal(state.local_pass_identity)}, "
            f"{findings}::jsonb, {diagnostics}::jsonb) "
            "ON CONFLICT (work_identity) DO UPDATE SET "
            "target_head_sha = EXCLUDED.target_head_sha, "
            "change_identity = EXCLUDED.change_identity, "
            "stage = EXCLUDED.stage, review_cycle = EXCLUDED.review_cycle, "
            "no_progress_count = EXCLUDED.no_progress_count, "
            "last_progress_fingerprint = EXCLUDED.last_progress_fingerprint, "
            "verification_identity = EXCLUDED.verification_identity, "
            "review_request_key = EXCLUDED.review_request_key, "
            "review_identity = EXCLUDED.review_identity, "
            "local_pass_identity = EXCLUDED.local_pass_identity, "
            "approved_findings = EXCLUDED.approved_findings, "
            "diagnostics = EXCLUDED.diagnostics, updated_at = now()"
        )
        if not self._database.execute_sql(sql):
            raise RuntimeError("LOCAL_QUALITY_STATE_WRITE_FAILED")


class LocalVerificationRunner:
    def __init__(
        self,
        runner: LocalQualityCommandRunner,
        environment: Mapping[str, str],
    ) -> None:
        self._runner = runner
        self._environment = _sanitized_environment(environment)

    def verify(
        self,
        context: LocalQualityContext,
    ) -> LocalVerificationResult:
        workspace = context.workspace_canonical_path.resolve(strict=False)
        before = self._fingerprint(workspace)
        if (
            before is None
            or before[0] != context.target.exact_head_sha
            or before[1] != context.target.change_identity
        ):
            return _verification_incomplete(
                context,
                "LOCAL_VERIFICATION_TARGET_READBACK_FAILED",
            )

        outcomes: list[VerificationCommandOutcome] = []
        diagnostics: list[str] = []
        required_failed = False
        for descriptor in context.verification_commands:
            cwd = _resolve_working_directory(workspace, descriptor.working_directory)
            if cwd is None:
                return _verification_incomplete(
                    context,
                    f"LOCAL_VERIFICATION_WORKDIR_INVALID:{descriptor.identity}",
                )
            try:
                result = self._runner.run(
                    descriptor.argv,
                    cwd=cwd,
                    environment=self._environment,
                    timeout_seconds=descriptor.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                outcomes.append(
                    VerificationCommandOutcome(
                        descriptor.identity,
                        "INCOMPLETE",
                        None,
                        "timeout",
                    )
                )
                return self._result(
                    context,
                    LocalVerificationStatus.INCOMPLETE,
                    before[1],
                    tuple(outcomes),
                    (f"LOCAL_VERIFICATION_TIMEOUT:{descriptor.identity}",),
                )
            except (OSError, subprocess.SubprocessError):
                outcomes.append(
                    VerificationCommandOutcome(
                        descriptor.identity,
                        "INCOMPLETE",
                        None,
                        "command unavailable",
                    )
                )
                return self._result(
                    context,
                    LocalVerificationStatus.INCOMPLETE,
                    before[1],
                    tuple(outcomes),
                    (f"LOCAL_VERIFICATION_COMMAND_UNAVAILABLE:{descriptor.identity}",),
                )
            status = "PASS" if result.succeeded else "FAIL"
            summary = _bounded_summary(result.output, result.returncode)
            outcomes.append(
                VerificationCommandOutcome(
                    descriptor.identity,
                    status,
                    result.returncode,
                    summary,
                )
            )
            if descriptor.required and not result.succeeded:
                required_failed = True
                diagnostics.append(f"LOCAL_VERIFICATION_FAILED:{descriptor.identity}")

        after = self._fingerprint(workspace)
        if after is None or after != before:
            return self._result(
                context,
                LocalVerificationStatus.INCOMPLETE,
                before[1],
                tuple(outcomes),
                ("LOCAL_VERIFICATION_TARGET_CHANGED",),
            )
        status = LocalVerificationStatus.FAIL if required_failed else LocalVerificationStatus.PASS
        return self._result(context, status, before[1], tuple(outcomes), tuple(diagnostics))

    def _fingerprint(self, workspace: Path) -> tuple[str, str] | None:
        simple_commands = (
            ("rev-parse", "HEAD"),
            ("rev-parse", "--abbrev-ref", "HEAD"),
            ("diff", "--cached", "--name-only", "-z", "HEAD", "--"),
            ("diff", "--name-only", "-z", "--"),
            ("ls-files", "--others", "--exclude-standard", "-z"),
            (
                "diff",
                "--cached",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
            ),
            ("diff", "--binary", "--no-ext-diff", "--no-textconv", "--"),
        )
        outputs: list[str] = []
        for arguments in simple_commands:
            value = self._git_output(workspace, arguments)
            if value is None:
                return None
            outputs.append(value)

        head = outputs[0].strip()
        branch = outputs[1].strip()
        if _SHA_RE.fullmatch(head) is None or not branch:
            return None

        staged_paths = _split_null(outputs[2])
        unstaged_paths = _split_null(outputs[3])
        untracked_paths = _split_null(outputs[4])
        untracked = set(untracked_paths)
        changed_paths = tuple(
            sorted(set(staged_paths) | set(unstaged_paths) | untracked)
        )

        digest = hashlib.sha256()
        digest.update(b"local-llm-coder-change-v2\0")
        digest.update(head.encode("ascii"))
        digest.update(b"\0branch\0")
        digest.update(branch.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0staged-diff\0")
        digest.update(outputs[5].encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0unstaged-diff\0")
        digest.update(outputs[6].encode("utf-8", errors="surrogateescape"))

        for relative in changed_paths:
            if not _safe_relative_path(relative):
                return None
            state = self._path_state(workspace, relative, untracked)
            if state is None:
                return None
            digest.update(b"\0path\0")
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0state\0")
            digest.update(state.encode("ascii"))

        return head, "sha256:" + digest.hexdigest()

    def _path_state(
        self,
        workspace: Path,
        relative: str,
        untracked: set[str],
    ) -> str | None:
        staged = self._git_output(
            workspace,
            (
                "diff",
                "--cached",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
                relative,
            ),
        )
        unstaged = self._git_output(
            workspace,
            (
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                relative,
            ),
        )
        if staged is None or unstaged is None:
            return None

        digest = hashlib.sha256()
        digest.update(b"local-llm-coder-path-state-v1\0")
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0staged\0")
        digest.update(staged.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0unstaged\0")
        digest.update(unstaged.encode("utf-8", errors="surrogateescape"))

        if relative in untracked:
            path = (workspace / relative).absolute()
            try:
                resolved_parent = path.parent.resolve()
            except OSError:
                return None
            if not resolved_parent.is_relative_to(workspace):
                return None
            digest.update(b"\0untracked\0")
            if not _hash_change_file(path, digest):
                return None

        return "sha256:" + digest.hexdigest()

    def _git_output(
        self,
        workspace: Path,
        arguments: Sequence[str],
    ) -> str | None:
        try:
            result = self._runner.run(
                ("git", "-C", str(workspace), *arguments),
                cwd=workspace,
                environment=self._environment,
                timeout_seconds=120,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.output if result.succeeded else None

    def _result(
        self,
        context: LocalQualityContext,
        status: LocalVerificationStatus,
        fingerprint: str,
        outcomes: tuple[VerificationCommandOutcome, ...],
        diagnostics: tuple[str, ...],
    ) -> LocalVerificationResult:
        payload = {
            "work": context.target.work_identity,
            "head": context.target.exact_head_sha,
            "change": context.target.change_identity,
            "fingerprint": fingerprint,
            "status": status.value,
            "outcomes": [
                {
                    "identity": item.identity,
                    "status": item.status,
                    "returncode": item.returncode,
                    "summary": item.summary,
                }
                for item in outcomes
            ],
        }
        identity = "local-verification:" + _digest(payload)
        return LocalVerificationResult(status, identity, outcomes, diagnostics)


class LocalLlmCoderReviewerAdapter:
    """local-llm-coderのfresh SELF_REVIEWERをread-onlyで1回実行する。"""

    def __init__(
        self,
        runner: LocalQualityCommandRunner,
        config: LocalLlmCoderConfig,
        workspace_path: Path,
        environment: Mapping[str, str],
        model_profile: str,
        *,
        timeout_seconds: int = 1800,
        worker_client: Callable[
            [str, str, dict[str, object], int],
            dict[str, Any],
        ] = post_worker_request,
    ) -> None:
        if not model_profile.strip():
            raise ValueError("LOCAL_REVIEWER_PROFILE_REQUIRED")
        if timeout_seconds < 1 or timeout_seconds > 7200:
            raise ValueError("LOCAL_REVIEWER_TIMEOUT_INVALID")
        self._runner = runner
        self._config = config
        self._workspace = workspace_path.resolve(strict=False)
        self._environment = _sanitized_environment(environment)
        self._model_profile = model_profile
        self._timeout_seconds = timeout_seconds
        self._worker_client = worker_client

    def review(self, context: LocalQualityContext) -> LocalReviewExecutionResult:
        if context.workspace_canonical_path.resolve(strict=False) != self._workspace:
            return _review_failure(
                LocalReviewStatus.BLOCKED,
                "LOCAL_REVIEW_WORKSPACE_IDENTITY_MISMATCH",
                "TARGET_PREFLIGHT",
            )

        payload = _review_request_payload(context, self._model_profile)
        request_identity = _required_string(payload["request_identity"])
        task_identity = _required_string(payload["task_packet_identity"])
        try:
            response = self._worker_client(
                self._config.endpoint,
                self._config.production_name,
                payload,
                self._timeout_seconds,
            )
        except LocalWorkerHttpTimeout:
            return _review_failure(
                LocalReviewStatus.INCOMPLETE,
                request_identity,
                "PROCESS_TIMEOUT",
            )
        except LocalWorkerHttpFailure as exc:
            if exc.http_status == 409:
                return _review_failure(
                    LocalReviewStatus.INCOMPLETE,
                    request_identity,
                    "PROCESS_EXIT",
                )
            failure_kind = (
                "RUNTIME_PREFLIGHT"
                if exc.http_status is not None
                and 400 <= exc.http_status < 500
                else "PROCESS_EXIT"
            )
            return _review_failure(
                LocalReviewStatus.FAILED,
                request_identity,
                failure_kind,
            )

        try:
            return _parse_review_result(
                response,
                context=context,
                request_identity=request_identity,
                task_identity=task_identity,
            )
        except ValueError:
            return _review_failure(
                LocalReviewStatus.FAILED,
                request_identity,
                "MALFORMED_AGENT_RESULT",
            )


class LocalQualityCoordinator:
    """VERIFY_LOCAL→LOCAL_REVIEW→REPAIRをdurable stateで自動反復する。"""

    def __init__(
        self,
        store: LocalQualityStorePort,
        verifier: LocalVerificationPort,
        reviewer: LocalReviewerPort,
        implementer: V2ImplementerPort,
        *,
        max_steps: int = 32,
        max_review_cycles: int = 12,
        max_no_progress: int = 3,
    ) -> None:
        if max_steps < 1 or max_review_cycles < 1 or max_no_progress < 1:
            raise ValueError("LOCAL_QUALITY_LIMIT_INVALID")
        self._store = store
        self._verifier = verifier
        self._reviewer = reviewer
        self._implementer = implementer
        self._max_steps = max_steps
        self._max_review_cycles = max_review_cycles
        self._max_no_progress = max_no_progress

    def run(self, context: LocalQualityContext) -> LocalQualityResult:
        current = context
        state = self._state_for_target(current.target)
        for _ in range(self._max_steps):
            if state.stage is LocalQualityStage.LOCAL_PASS:
                identity = state.local_pass_identity
                if identity is None:
                    return self._blocked(current.target, state, "LOCAL_PASS_IDENTITY_MISSING")
                return LocalQualityResult(
                    LocalQualityStatus.PASS,
                    "LOCAL_PASS",
                    current.target,
                    identity,
                )
            if state.stage is LocalQualityStage.BLOCKED:
                return self._blocked(current.target, state, "LOCAL_QUALITY_BLOCKED")

            if state.stage is LocalQualityStage.VERIFY_LOCAL:
                verification = self._verifier.verify(current)
                if verification.status is LocalVerificationStatus.INCOMPLETE:
                    state = _state_replace(
                        state,
                        diagnostics=verification.diagnostics,
                    )
                    self._store.save(state)
                    return LocalQualityResult(
                        LocalQualityStatus.INCOMPLETE,
                        verification.diagnostics[0]
                        if verification.diagnostics
                        else "LOCAL_VERIFICATION_INCOMPLETE",
                        current.target,
                    )
                if verification.status is LocalVerificationStatus.FAIL:
                    findings = _verification_findings(current, verification)
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.REPAIR,
                        verification_identity=verification.evidence_identity,
                        approved_findings=findings,
                        diagnostics=verification.diagnostics,
                    )
                    self._store.save(state)
                    continue
                state = _state_replace(
                    state,
                    stage=LocalQualityStage.LOCAL_REVIEW,
                    verification_identity=verification.evidence_identity,
                    approved_findings=(),
                    diagnostics=(),
                )
                self._store.save(state)
                continue

            if state.stage is LocalQualityStage.LOCAL_REVIEW:
                review = self._reviewer.review(current)
                if review.status is LocalReviewStatus.INCOMPLETE:
                    state = _state_replace(
                        state,
                        review_request_key=review.request_identity,
                        diagnostics=review.diagnostics,
                    )
                    self._store.save(state)
                    return LocalQualityResult(
                        LocalQualityStatus.INCOMPLETE,
                        "LOCAL_REVIEW_INCOMPLETE",
                        current.target,
                    )
                if review.status in {LocalReviewStatus.BLOCKED, LocalReviewStatus.FAILED}:
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.BLOCKED,
                        review_request_key=review.request_identity,
                        diagnostics=review.diagnostics,
                    )
                    self._store.save(state)
                    return self._blocked(current.target, state, "LOCAL_REVIEW_FAILED")
                if review.completion is None or not review.completion.complete:
                    state = _state_replace(
                        state,
                        diagnostics=("LOCAL_REVIEW_COMPLETION_INCOMPLETE",),
                    )
                    self._store.save(state)
                    return LocalQualityResult(
                        LocalQualityStatus.INCOMPLETE,
                        "LOCAL_REVIEW_COMPLETION_INCOMPLETE",
                        current.target,
                    )

                validation = validate_local_findings(review.findings, current.target.scope_paths)
                if validation.rejected_identities:
                    invalid_fingerprint = "invalid-findings:" + _digest(
                        {
                            "target": _target_fingerprint(current.target),
                            "identities": validation.rejected_identities,
                        }
                    )
                    no_progress = (
                        state.no_progress_count + 1
                        if state.last_progress_fingerprint == invalid_fingerprint
                        else 1
                    )
                    blocked = no_progress >= self._max_no_progress
                    state = _state_replace(
                        state,
                        stage=(
                            LocalQualityStage.BLOCKED
                            if blocked
                            else LocalQualityStage.LOCAL_REVIEW
                        ),
                        no_progress_count=no_progress,
                        last_progress_fingerprint=invalid_fingerprint,
                        review_request_key=review.request_identity,
                        review_identity=_local_review_identity(current.target, review),
                        diagnostics=tuple(
                            f"LOCAL_FINDING_REJECTED:{item}"
                            for item in validation.rejected_identities
                        ),
                    )
                    self._store.save(state)
                    if blocked:
                        return self._blocked(
                            current.target,
                            state,
                            "LOCAL_FINDING_VALIDATION_NO_PROGRESS",
                        )
                    return LocalQualityResult(
                        LocalQualityStatus.INCOMPLETE,
                        "LOCAL_FINDING_VALIDATION_INCOMPLETE",
                        current.target,
                    )
                review_identity = _local_review_identity(current.target, review)
                if validation.approved_blocking:
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.REPAIR,
                        review_request_key=review.request_identity,
                        review_identity=review_identity,
                        approved_findings=validation.approved_blocking,
                        diagnostics=(),
                    )
                    self._store.save(state)
                    continue
                local_pass = _local_pass_identity(
                    current.target,
                    state.verification_identity,
                    review_identity,
                )
                state = _state_replace(
                    state,
                    stage=LocalQualityStage.LOCAL_PASS,
                    review_request_key=review.request_identity,
                    review_identity=review_identity,
                    local_pass_identity=local_pass,
                    approved_findings=(),
                    diagnostics=(),
                )
                self._store.save(state)
                continue

            if state.stage is LocalQualityStage.REPAIR:
                if not state.approved_findings:
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.BLOCKED,
                        diagnostics=("LOCAL_REPAIR_FINDINGS_MISSING",),
                    )
                    self._store.save(state)
                    return self._blocked(current.target, state, "LOCAL_REPAIR_FINDINGS_MISSING")
                if state.review_cycle >= self._max_review_cycles:
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.BLOCKED,
                        diagnostics=("LOCAL_REVIEW_CYCLE_LIMIT",),
                    )
                    self._store.save(state)
                    return self._blocked(current.target, state, "LOCAL_REVIEW_CYCLE_LIMIT")

                packet = _repair_packet(current, state.approved_findings, state.review_cycle)
                repaired = self._implementer.execute(packet)
                if repaired.status is ImplementerStatus.INCOMPLETE:
                    return LocalQualityResult(
                        LocalQualityStatus.INCOMPLETE,
                        repaired.detail,
                        current.target,
                        approved_findings=state.approved_findings,
                    )
                if repaired.status is not ImplementerStatus.SUCCESS:
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.BLOCKED,
                        diagnostics=(repaired.detail, *repaired.diagnostics),
                    )
                    self._store.save(state)
                    return self._blocked(current.target, state, repaired.detail)
                if repaired.workspace_effect is None:
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.BLOCKED,
                        diagnostics=("LOCAL_REPAIR_WORKSPACE_EFFECT_REQUIRED",),
                    )
                    self._store.save(state)
                    return self._blocked(
                        current.target,
                        state,
                        "LOCAL_REPAIR_WORKSPACE_EFFECT_REQUIRED",
                    )
                effect = repaired.workspace_effect
                if effect.work_identity != current.target.work_identity:
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.BLOCKED,
                        diagnostics=("LOCAL_REPAIR_WORK_IDENTITY_MISMATCH",),
                    )
                    self._store.save(state)
                    return self._blocked(
                        current.target,
                        state,
                        "LOCAL_REPAIR_WORK_IDENTITY_MISMATCH",
                    )
                next_target = LocalQualityTarget(
                    repository_identity=current.target.repository_identity,
                    work_identity=current.target.work_identity,
                    exact_head_sha=effect.result_target_identity,
                    change_identity=effect.change_identity,
                    active_lineage_identity=current.target.active_lineage_identity,
                    canonical_design_identities=current.target.canonical_design_identities,
                    acceptance_digest=current.target.acceptance_digest,
                    scope_paths=current.target.scope_paths,
                )
                progress = _target_fingerprint(next_target)
                no_progress = (
                    state.no_progress_count + 1
                    if progress == state.last_progress_fingerprint
                    or (
                        next_target.exact_head_sha == current.target.exact_head_sha
                        and next_target.change_identity == current.target.change_identity
                    )
                    else 0
                )
                if no_progress >= self._max_no_progress:
                    state = _state_replace(
                        state,
                        stage=LocalQualityStage.BLOCKED,
                        no_progress_count=no_progress,
                        last_progress_fingerprint=progress,
                        diagnostics=("LOCAL_REPAIR_NO_PROGRESS",),
                    )
                    self._store.save(state)
                    return self._blocked(current.target, state, "LOCAL_REPAIR_NO_PROGRESS")

                current = _context_with_target(current, next_target)
                state = LocalQualityState(
                    work_identity=next_target.work_identity,
                    target_head_sha=next_target.exact_head_sha,
                    change_identity=next_target.change_identity,
                    stage=LocalQualityStage.VERIFY_LOCAL,
                    review_cycle=state.review_cycle + 1,
                    no_progress_count=no_progress,
                    last_progress_fingerprint=progress,
                )
                self._store.save(state)
                continue

        state = _state_replace(
            state,
            stage=LocalQualityStage.BLOCKED,
            diagnostics=("LOCAL_QUALITY_STEP_LIMIT",),
        )
        self._store.save(state)
        return self._blocked(current.target, state, "LOCAL_QUALITY_STEP_LIMIT")

    def _state_for_target(self, target: LocalQualityTarget) -> LocalQualityState:
        stored = self._store.get(target.work_identity)
        if (
            stored is None
            or stored.target_head_sha != target.exact_head_sha
            or stored.change_identity != target.change_identity
        ):
            state = LocalQualityState(
                target.work_identity,
                target.exact_head_sha,
                target.change_identity,
                LocalQualityStage.VERIFY_LOCAL,
                last_progress_fingerprint=_target_fingerprint(target),
            )
            self._store.save(state)
            return state
        return stored

    @staticmethod
    def _blocked(
        target: LocalQualityTarget,
        state: LocalQualityState,
        detail: str,
    ) -> LocalQualityResult:
        return LocalQualityResult(
            LocalQualityStatus.BLOCKED,
            detail,
            target,
            approved_findings=state.approved_findings,
        )


def validate_local_findings(
    findings: tuple[ImplementerFinding, ...],
    scope_paths: tuple[str, ...],
) -> LocalFindingValidation:
    blocking: list[ImplementerFinding] = []
    non_blocking: list[ImplementerFinding] = []
    duplicates: list[str] = []
    rejected: list[str] = []
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    for finding in findings:
        identity = finding.finding_identity
        values = (
            identity,
            finding.severity,
            finding.path,
            finding.location,
            finding.problem,
            finding.basis,
            finding.evidence,
            finding.impact,
            finding.suggested_fix,
        )
        if (
            any(not item.strip() for item in values)
            or finding.severity not in {"BLOCKING", "NON_BLOCKING"}
            or not _safe_relative_path(finding.path)
            or not _path_in_scope(finding.path, scope_paths)
        ):
            rejected.append(identity or "<missing>")
            continue
        content = _digest(
            {
                "severity": finding.severity,
                "path": finding.path,
                "location": finding.location,
                "problem": finding.problem,
                "basis": finding.basis,
                "evidence": finding.evidence,
                "impact": finding.impact,
                "suggested_fix": finding.suggested_fix,
            }
        )
        if identity in seen_ids or content in seen_content:
            duplicates.append(identity)
            continue
        seen_ids.add(identity)
        seen_content.add(content)
        if finding.severity == "BLOCKING":
            blocking.append(finding)
        else:
            non_blocking.append(finding)
    return LocalFindingValidation(
        tuple(blocking),
        tuple(non_blocking),
        tuple(duplicates),
        tuple(rejected),
    )


def _review_request_payload(
    context: LocalQualityContext,
    model_profile: str,
) -> dict[str, object]:
    target = context.target
    task_identity = "local-review-packet:" + _digest(
        {
            "packet": context.packet_identity,
            "head": target.exact_head_sha,
            "change": target.change_identity,
        }
    )
    request_identity = "local-review:" + _digest(
        {
            "task": task_identity,
            "work": target.work_identity,
            "head": target.exact_head_sha,
            "change": target.change_identity,
            "profile": model_profile,
        }
    )
    return {
        "schema_version": 1,
        "request_identity": request_identity,
        "task_packet_identity": task_identity,
        "role": "SELF_REVIEWER",
        "transition": "REVIEW",
        "effect_requirement": "MUST_NOT_CHANGE",
        "repository_identity": target.repository_identity,
        "workspace_canonical_path": str(
            context.workspace_canonical_path.resolve(strict=False)
        ),
        "input_target_identity": target.exact_head_sha,
        "exact_base_sha": target.exact_head_sha,
        "expected_change_identity": target.change_identity,
        "active_lineage_identity": target.active_lineage_identity,
        "authority_refs": list(context.authority_refs),
        "scope_paths": list(target.scope_paths),
        "canonical_refs": list(target.canonical_design_identities),
        "acceptance_checks": list(context.acceptance_checks),
        "non_goals": list(context.non_goals),
        "safety_constraints": list(context.safety_constraints),
        "model_profile": model_profile,
        "task": (
            "現在のexact targetをfresh sessionで独立セルフレビューする。"
            "実装変更は行わず、canonical design、受入条件、scope、検証証拠に基づいて"
            "blocking/non-blocking findingを確定する。"
        ),
        "approved_findings": [],
    }


def _parse_review_result(
    raw: object,
    *,
    context: LocalQualityContext,
    request_identity: str,
    task_identity: str,
) -> LocalReviewExecutionResult:
    if not isinstance(raw, dict) or set(raw) != _WORKER_RESULT_FIELDS:
        raise ValueError("review result fields invalid")
    if raw.get("schema_version") != 1:
        raise ValueError("review schema invalid")
    echoes = {
        "request_identity": request_identity,
        "task_packet_identity": task_identity,
        "role": "SELF_REVIEWER",
        "input_target_identity": context.target.exact_head_sha,
    }
    for field, expected in echoes.items():
        if raw.get(field) != expected:
            raise ValueError("review identity mismatch")
    status_text = _required_string(raw.get("status"))
    try:
        status = LocalReviewStatus(status_text)
    except ValueError as error:
        raise ValueError("review status invalid") from error

    failure_raw = raw.get("failure_kind")
    if status in {LocalReviewStatus.PASS, LocalReviewStatus.FINDINGS}:
        if failure_raw is not None:
            raise ValueError("successful review failure kind invalid")
        failure_kind = None
    else:
        failure_kind = _required_string(failure_raw)
        if failure_kind not in _FAILURE_KINDS:
            raise ValueError("review failure kind invalid")

    completion_raw = raw.get("completion")
    completion: LocalReviewCompletion | None = None
    if isinstance(completion_raw, dict):
        if set(completion_raw) != _REVIEW_COMPLETION_FIELDS:
            raise ValueError("review completion fields invalid")
        if not all(isinstance(item, bool) for item in completion_raw.values()):
            raise ValueError("review completion values invalid")
        completion = LocalReviewCompletion(
            **{name: bool(completion_raw[name]) for name in _REVIEW_COMPLETION_FIELDS}
        )
    else:
        raise ValueError("review completion invalid")

    findings_raw = raw.get("findings")
    if not isinstance(findings_raw, list):
        raise ValueError("review findings invalid")
    findings = tuple(_review_finding(item) for item in findings_raw)
    if status is LocalReviewStatus.PASS and findings:
        raise ValueError("review PASS findings invalid")
    if status is LocalReviewStatus.FINDINGS and not findings:
        raise ValueError("review FINDINGS missing")
    if status in {LocalReviewStatus.PASS, LocalReviewStatus.FINDINGS}:
        if completion is None or not completion.complete:
            raise ValueError("review completion incomplete")
        if raw.get("result_target_identity") != context.target.exact_head_sha:
            raise ValueError("review target changed")
        if raw.get("change_identity") != context.target.change_identity:
            raise ValueError("review change identity changed")
        changed = raw.get("changed_paths")
        if not isinstance(changed, list) or changed:
            raise ValueError("review mutation detected")

    diagnostics = _string_tuple(raw.get("diagnostics"))
    if status not in {LocalReviewStatus.PASS, LocalReviewStatus.FINDINGS} and not diagnostics:
        raise ValueError("review failure diagnostics missing")

    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != _ARTIFACT_FIELDS:
        raise ValueError("review artifacts invalid")
    _string_tuple(artifacts.get("agent_artifact_refs"))

    return LocalReviewExecutionResult(
        status=status,
        request_identity=request_identity,
        result_target_identity=_optional_string(raw.get("result_target_identity")),
        change_identity=_optional_string(raw.get("change_identity")),
        completion=completion,
        findings=findings,
        failure_kind=failure_kind,
        diagnostics=diagnostics,
    )


def _review_finding(value: object) -> ImplementerFinding:
    if not isinstance(value, dict) or set(value) != _FINDING_FIELDS:
        raise ValueError("finding schema invalid")
    return ImplementerFinding(
        finding_identity=_required_string(value.get("finding_identity")),
        severity=_required_string(value.get("severity")),
        path=_required_string(value.get("path")),
        location=_required_string(value.get("location")),
        problem=_required_string(value.get("problem")),
        basis=_required_string(value.get("basis")),
        evidence=_required_string(value.get("evidence")),
        impact=_required_string(value.get("impact")),
        suggested_fix=_required_string(value.get("suggested_fix")),
    )


def _review_failure(
    status: LocalReviewStatus,
    request_identity: str,
    failure_kind: str,
) -> LocalReviewExecutionResult:
    return LocalReviewExecutionResult(
        status=status,
        request_identity=request_identity,
        result_target_identity=None,
        change_identity=None,
        completion=None,
        findings=(),
        failure_kind=failure_kind,
        diagnostics=(failure_kind,),
    )


def _verification_incomplete(
    context: LocalQualityContext,
    detail: str,
) -> LocalVerificationResult:
    identity = "local-verification:" + _digest(
        {
            "work": context.target.work_identity,
            "head": context.target.exact_head_sha,
            "change": context.target.change_identity,
            "detail": detail,
        }
    )
    return LocalVerificationResult(
        LocalVerificationStatus.INCOMPLETE,
        identity,
        (),
        (detail,),
    )


def _verification_findings(
    context: LocalQualityContext,
    result: LocalVerificationResult,
) -> tuple[ImplementerFinding, ...]:
    findings: list[ImplementerFinding] = []
    path = context.target.scope_paths[0]
    for outcome in result.outcomes:
        if outcome.status != "FAIL":
            continue
        identity = "verification-finding:" + _digest(
            {
                "work": context.target.work_identity,
                "head": context.target.exact_head_sha,
                "change": context.target.change_identity,
                "command": outcome.identity,
                "summary": outcome.summary,
            }
        )
        findings.append(
            ImplementerFinding(
                finding_identity=identity,
                severity="BLOCKING",
                path=path,
                location=f"verification:{outcome.identity}",
                problem="required deterministic verificationが失敗した",
                basis="Local Quality Loop required verification policy",
                evidence=outcome.summary or f"returncode={outcome.returncode}",
                impact="LOCAL_PASSを成立させられない",
                suggested_fix="失敗した検証がPASSするように同一lineageで修正する",
            )
        )
    if not findings:
        raise RuntimeError("LOCAL_VERIFICATION_FAILURE_WITHOUT_FINDING")
    return tuple(findings)


def _repair_packet(
    context: LocalQualityContext,
    findings: tuple[ImplementerFinding, ...],
    review_cycle: int,
) -> DevelopmentTaskPacket:
    target = context.target
    packet_identity = "local-repair-packet:" + _digest(
        {
            "base_packet": context.packet_identity,
            "work": target.work_identity,
            "head": target.exact_head_sha,
            "change": target.change_identity,
            "cycle": review_cycle,
            "findings": [item.finding_identity for item in findings],
        }
    )
    return DevelopmentTaskPacket(
        packet_identity=packet_identity,
        work_identity=target.work_identity,
        generation=context.generation,
        transition=ImplementerTransition.REPAIR,
        repository_identity=target.repository_identity,
        workspace_canonical_path=context.workspace_canonical_path,
        exact_base_sha=target.exact_head_sha,
        goal_revision=context.goal_revision,
        issue_revision=context.issue_revision,
        scope_paths=target.scope_paths,
        acceptance_checks=context.acceptance_checks,
        canonical_design_identities=target.canonical_design_identities,
        active_lineage_identity=target.active_lineage_identity,
        authority_refs=context.authority_refs,
        non_goals=context.non_goals,
        safety_constraints=context.safety_constraints,
        expected_change_identity=target.change_identity,
        approved_findings=findings,
    )


def _local_review_identity(
    target: LocalQualityTarget,
    review: LocalReviewExecutionResult,
) -> str:
    return "local-review-evidence:" + _digest(
        {
            "request": review.request_identity,
            "work": target.work_identity,
            "head": target.exact_head_sha,
            "change": target.change_identity,
            "status": review.status.value,
            "findings": [item.finding_identity for item in review.findings],
        }
    )


def _local_pass_identity(
    target: LocalQualityTarget,
    verification_identity: str | None,
    review_identity: str,
) -> str:
    if verification_identity is None:
        raise RuntimeError("LOCAL_VERIFICATION_IDENTITY_MISSING")
    return "local-pass:" + _digest(
        {
            "work": target.work_identity,
            "head": target.exact_head_sha,
            "change": target.change_identity,
            "verification": verification_identity,
            "review": review_identity,
        }
    )


def _target_fingerprint(target: LocalQualityTarget) -> str:
    return _digest(
        {
            "head": target.exact_head_sha,
            "change": target.change_identity,
            "lineage": target.active_lineage_identity,
        }
    )


def _context_with_target(
    context: LocalQualityContext,
    target: LocalQualityTarget,
) -> LocalQualityContext:
    return LocalQualityContext(
        target=target,
        workspace_canonical_path=context.workspace_canonical_path,
        packet_identity=context.packet_identity,
        generation=context.generation,
        goal_revision=context.goal_revision,
        issue_revision=context.issue_revision,
        acceptance_checks=context.acceptance_checks,
        authority_refs=context.authority_refs,
        non_goals=context.non_goals,
        safety_constraints=context.safety_constraints,
        verification_commands=context.verification_commands,
    )


def _state_replace(
    state: LocalQualityState,
    *,
    stage: LocalQualityStage | None = None,
    review_cycle: int | None = None,
    no_progress_count: int | None = None,
    last_progress_fingerprint: str | None | object = ...,
    verification_identity: str | None | object = ...,
    review_request_key: str | None | object = ...,
    review_identity: str | None | object = ...,
    local_pass_identity: str | None | object = ...,
    approved_findings: tuple[ImplementerFinding, ...] | None = None,
    diagnostics: tuple[str, ...] | None = None,
) -> LocalQualityState:
    return LocalQualityState(
        work_identity=state.work_identity,
        target_head_sha=state.target_head_sha,
        change_identity=state.change_identity,
        stage=stage if stage is not None else state.stage,
        review_cycle=review_cycle if review_cycle is not None else state.review_cycle,
        no_progress_count=(
            no_progress_count if no_progress_count is not None else state.no_progress_count
        ),
        last_progress_fingerprint=(
            state.last_progress_fingerprint
            if last_progress_fingerprint is ...
            else _optional_object_string(last_progress_fingerprint)
        ),
        verification_identity=(
            state.verification_identity
            if verification_identity is ...
            else _optional_object_string(verification_identity)
        ),
        review_request_key=(
            state.review_request_key
            if review_request_key is ...
            else _optional_object_string(review_request_key)
        ),
        review_identity=(
            state.review_identity
            if review_identity is ...
            else _optional_object_string(review_identity)
        ),
        local_pass_identity=(
            state.local_pass_identity
            if local_pass_identity is ...
            else _optional_object_string(local_pass_identity)
        ),
        approved_findings=(
            approved_findings if approved_findings is not None else state.approved_findings
        ),
        diagnostics=diagnostics if diagnostics is not None else state.diagnostics,
    )


def _state_from_row(row: dict[str, object]) -> LocalQualityState:
    stage_raw = _required_row_string(row, "stage")
    try:
        stage = LocalQualityStage(stage_raw)
    except ValueError as error:
        raise RuntimeError("LOCAL_QUALITY_STATE_ROW_INVALID") from error
    cycle = row.get("review_cycle")
    no_progress = row.get("no_progress_count")
    if not isinstance(cycle, int) or cycle < 0:
        raise RuntimeError("LOCAL_QUALITY_STATE_ROW_INVALID")
    if not isinstance(no_progress, int) or no_progress < 0:
        raise RuntimeError("LOCAL_QUALITY_STATE_ROW_INVALID")
    approved_raw = row.get("approved_findings")
    diagnostics_raw = row.get("diagnostics")
    if not isinstance(approved_raw, list) or not isinstance(diagnostics_raw, list):
        raise RuntimeError("LOCAL_QUALITY_STATE_ROW_INVALID")
    try:
        findings = tuple(_review_finding(item) for item in approved_raw)
        diagnostics = tuple(_required_string(item) for item in diagnostics_raw)
    except ValueError as error:
        raise RuntimeError("LOCAL_QUALITY_STATE_ROW_INVALID") from error
    return LocalQualityState(
        work_identity=_required_row_string(row, "work_identity"),
        target_head_sha=_required_row_string(row, "target_head_sha"),
        change_identity=_required_row_string(row, "change_identity"),
        stage=stage,
        review_cycle=cycle,
        no_progress_count=no_progress,
        last_progress_fingerprint=_optional_row_string(row, "last_progress_fingerprint"),
        verification_identity=_optional_row_string(row, "verification_identity"),
        review_request_key=_optional_row_string(row, "review_request_key"),
        review_identity=_optional_row_string(row, "review_identity"),
        local_pass_identity=_optional_row_string(row, "local_pass_identity"),
        approved_findings=findings,
        diagnostics=diagnostics,
    )


def _split_null(raw: str) -> tuple[str, ...]:
    return tuple(item for item in raw.split("\0") if item)


def _hash_change_file(path: Path, digest: object) -> bool:
    update = getattr(digest, "update", None)
    if not callable(update):
        return False
    try:
        if path.is_symlink():
            update(b"symlink\0")
            update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
            return True
        if not path.is_file():
            return False
        update(b"file\0")
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                update(chunk)
    except OSError:
        return False
    return True


def _resolve_working_directory(workspace: Path, relative: str) -> Path | None:
    candidate = workspace if relative == "." else workspace / relative
    resolved = candidate.resolve(strict=False)
    try:
        if not resolved.is_relative_to(workspace):
            return None
    except ValueError:
        return None
    return resolved if resolved.is_dir() else None


def clean_workspace_change_identity(head: str, branch: str) -> str:
    """変更なしWorkspaceのlocal-llm-coder change identityを返す。"""
    if _SHA_RE.fullmatch(head) is None or not branch.strip():
        raise ValueError("CLEAN_WORKSPACE_IDENTITY_INVALID")
    digest = hashlib.sha256()
    digest.update(b"local-llm-coder-change-v2\0")
    digest.update(head.encode("ascii"))
    digest.update(b"\0branch\0")
    digest.update(branch.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0staged-diff\0")
    digest.update(b"")
    digest.update(b"\0unstaged-diff\0")
    digest.update(b"")
    return "sha256:" + digest.hexdigest()


def _safe_scope_path(value: str) -> bool:
    if value in {".", "./"}:
        return True
    return _safe_relative_path(value)


def _safe_relative_path(value: str) -> bool:
    if not value or value != value.strip() or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and value not in {".", "./"}


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
        if name in allowed
        and name not in {
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "OPENAI_API_KEY",
            "OPENAI_API_KEY_REVIEWER",
            "LOOP_POSTGRES_DSN",
            "LOOP_DATABASE_URL",
            "LOOP_TRUSTED_REVIEWER_SOCKET",
        }
        and "REVIEWER" not in name
    }


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


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_summary(output: str, returncode: int) -> str:
    normalized = " ".join(output.strip().split())
    if not normalized:
        normalized = f"returncode={returncode}"
    return normalized[:1000]


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("string invalid")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _required_string(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("string list invalid")
    return tuple(_required_string(item) for item in value)


def _required_row_string(row: dict[str, object], name: str) -> str:
    try:
        return _required_string(row.get(name))
    except ValueError as error:
        raise RuntimeError("LOCAL_QUALITY_STATE_ROW_INVALID") from error


def _optional_row_string(row: dict[str, object], name: str) -> str | None:
    value = row.get(name)
    if value is None:
        return None
    try:
        return _required_string(value)
    except ValueError as error:
        raise RuntimeError("LOCAL_QUALITY_STATE_ROW_INVALID") from error


def _optional_object_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("optional string value invalid")
    return value


def _literal(value: str) -> str:
    if "\x00" in value or len(value) > 100_000:
        raise RuntimeError("LOCAL_QUALITY_VALUE_INVALID")
    return "'" + value.replace("'", "''") + "'"


def _nullable_literal(value: str | None) -> str:
    return "NULL" if value is None else _literal(value)
