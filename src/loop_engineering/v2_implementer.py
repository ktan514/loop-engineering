"""V2 TaskPacketをCodex proposal modeで安全に実行する。"""

from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Protocol

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_MAX_PATCH_BYTES = 2_000_000
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
        "CODEX_HOME",
    }
)


class ImplementerTransition(str, Enum):
    DESIGN = "DESIGN"
    IMPLEMENT = "IMPLEMENT"
    REPAIR = "REPAIR"


class ImplementerStatus(str, Enum):
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class CommandResultLike(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def output(self) -> str: ...

    @property
    def succeeded(self) -> bool: ...


class ImplementerCommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> CommandResultLike: ...


@dataclass(frozen=True, slots=True)
class DevelopmentTaskPacket:
    packet_identity: str
    work_identity: str
    generation: int
    transition: ImplementerTransition
    repository_identity: str
    workspace_canonical_path: Path
    exact_base_sha: str
    goal_revision: str
    issue_revision: str
    scope_paths: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    canonical_design_identities: tuple[str, ...] = ()
    canonical_design_targets: tuple[str, ...] = ()
    active_lineage_identity: str | None = None
    authority_refs: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    safety_constraints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChangeProposal:
    packet_identity: str
    work_identity: str
    transition: ImplementerTransition
    exact_base_sha: str
    changed_paths: tuple[str, ...]
    patch_sha256: str
    patch_text: str
    design_targets_changed: tuple[str, ...]
    diff_check_passed: bool = True


@dataclass(frozen=True, slots=True)
class ImplementerResult:
    status: ImplementerStatus
    detail: str
    proposal: ChangeProposal | None = None


class CodexProposalImplementer:
    """exact HEADの隔離worktreeでCodexを実行し、patchだけを返す。"""

    def __init__(
        self,
        runner: ImplementerCommandRunner,
        codex_argv_prefix: Sequence[str],
        environment: Mapping[str, str],
        *,
        timeout_seconds: int = 1200,
        environment_allowlist: Sequence[str] = (),
    ) -> None:
        if not codex_argv_prefix or any(not item for item in codex_argv_prefix):
            raise ValueError("CODEX_COMMAND_INVALID")
        if timeout_seconds < 1 or timeout_seconds > 7200:
            raise ValueError("CODEX_TIMEOUT_INVALID")
        self._runner = runner
        self._codex_argv_prefix = tuple(codex_argv_prefix)
        self._environment = _sanitized_environment(environment, environment_allowlist)
        self._timeout_seconds = timeout_seconds

    def execute(self, packet: DevelopmentTaskPacket) -> ImplementerResult:
        validation = _validate_packet(packet)
        if validation is not None:
            return ImplementerResult(ImplementerStatus.BLOCKED, validation)

        workspace = packet.workspace_canonical_path.resolve(strict=False)
        source = self._inspect_source(workspace, packet)
        if source is None:
            return ImplementerResult(ImplementerStatus.BLOCKED, "WORKSPACE_PREFLIGHT_FAILED")
        source_branch, source_head = source

        temporary = Path(
            tempfile.mkdtemp(prefix=".loop-codex-", dir=str(workspace.parent))
        ).resolve(strict=False)
        worktree_added = False
        result: ImplementerResult
        try:
            added = self._run_git(
                workspace,
                ("worktree", "add", "--detach", str(temporary), packet.exact_base_sha),
                timeout_seconds=180,
            )
            if not added.succeeded:
                result = ImplementerResult(ImplementerStatus.FAILED, "WORKTREE_CREATE_FAILED")
            else:
                worktree_added = True
                result = self._execute_in_worktree(temporary, packet)
        finally:
            cleanup_ok = self._cleanup_worktree(workspace, temporary, worktree_added)

        source_ok = self._source_unchanged(
            workspace,
            expected_branch=source_branch,
            expected_head=source_head,
        )
        if not cleanup_ok:
            return ImplementerResult(ImplementerStatus.BLOCKED, "IMPLEMENTER_CLEANUP_UNPROVEN")
        if not source_ok:
            return ImplementerResult(ImplementerStatus.BLOCKED, "SOURCE_WORKSPACE_CHANGED")
        return result

    def _execute_in_worktree(
        self,
        worktree: Path,
        packet: DevelopmentTaskPacket,
    ) -> ImplementerResult:
        before_head = self._git_output(worktree, ("rev-parse", "HEAD"))
        branch = self._git_output(worktree, ("branch", "--show-current"))
        if before_head != packet.exact_base_sha or branch is None or branch.strip():
            return ImplementerResult(ImplementerStatus.BLOCKED, "ISOLATED_WORKTREE_IDENTITY_INVALID")

        codex = self._runner.run(
            (*self._codex_argv_prefix, _instruction(packet)),
            cwd=worktree,
            environment=self._environment,
            timeout_seconds=self._timeout_seconds,
        )
        if not codex.succeeded:
            return ImplementerResult(ImplementerStatus.FAILED, "CODEX_EXECUTION_FAILED")

        after_head = self._git_output(worktree, ("rev-parse", "HEAD"))
        after_branch = self._git_output(worktree, ("branch", "--show-current"))
        if after_head != packet.exact_base_sha or after_branch is None or after_branch.strip():
            return ImplementerResult(ImplementerStatus.BLOCKED, "CODEX_GIT_MUTATION_DETECTED")

        staged = self._run_git(worktree, ("add", "-A"))
        if not staged.succeeded:
            return ImplementerResult(ImplementerStatus.FAILED, "PROPOSAL_STAGE_FAILED")
        changed = self._git_output(worktree, ("diff", "--cached", "--name-only", "HEAD"))
        if changed is None:
            return ImplementerResult(ImplementerStatus.FAILED, "PROPOSAL_PATH_READBACK_FAILED")
        paths = tuple(line.strip() for line in changed.splitlines() if line.strip())
        if not paths:
            return ImplementerResult(ImplementerStatus.FAILED, "CODEX_NO_CHANGE")
        if any(not _path_in_scope(path, packet.scope_paths) for path in paths):
            return ImplementerResult(ImplementerStatus.BLOCKED, "PROPOSAL_SCOPE_VIOLATION")

        design_changed = tuple(
            path
            for path in paths
            if _path_in_scope(path, packet.canonical_design_targets)
        )
        if packet.transition is ImplementerTransition.DESIGN:
            if not design_changed or len(design_changed) != len(paths):
                return ImplementerResult(ImplementerStatus.BLOCKED, "DESIGN_SCOPE_VIOLATION")

        diff_check = self._run_git(worktree, ("diff", "--cached", "--check", "HEAD"))
        if not diff_check.succeeded:
            return ImplementerResult(ImplementerStatus.FAILED, "PROPOSAL_DIFF_CHECK_FAILED")
        patch = self._git_output(
            worktree,
            ("diff", "--cached", "--binary", "--no-ext-diff", "HEAD"),
        )
        if patch is None or not patch:
            return ImplementerResult(ImplementerStatus.FAILED, "PROPOSAL_PATCH_UNAVAILABLE")
        if len(patch.encode("utf-8")) > _MAX_PATCH_BYTES:
            return ImplementerResult(ImplementerStatus.BLOCKED, "PROPOSAL_PATCH_TOO_LARGE")

        proposal = ChangeProposal(
            packet_identity=packet.packet_identity,
            work_identity=packet.work_identity,
            transition=packet.transition,
            exact_base_sha=packet.exact_base_sha,
            changed_paths=paths,
            patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            patch_text=patch,
            design_targets_changed=design_changed,
        )
        return ImplementerResult(ImplementerStatus.SUCCESS, "CHANGE_PROPOSAL_READY", proposal)

    def _inspect_source(
        self,
        workspace: Path,
        packet: DevelopmentTaskPacket,
    ) -> tuple[str, str] | None:
        if not workspace.is_absolute() or not workspace.is_dir():
            return None
        top = self._git_output(workspace, ("rev-parse", "--show-toplevel"))
        if top is None or Path(top).resolve(strict=False) != workspace:
            return None
        remote = self._git_output(workspace, ("remote", "get-url", "origin"))
        if remote is None or not _remote_matches(remote, packet.repository_identity):
            return None
        status = self._git_output(workspace, ("status", "--porcelain"))
        if status is None or status.strip():
            return None
        object_check = self._run_git(
            workspace,
            ("cat-file", "-e", f"{packet.exact_base_sha}^{{commit}}"),
        )
        if not object_check.succeeded:
            return None
        branch = self._git_output(workspace, ("branch", "--show-current"))
        head = self._git_output(workspace, ("rev-parse", "HEAD"))
        if branch is None or head is None:
            return None
        return branch, head

    def _source_unchanged(
        self,
        workspace: Path,
        *,
        expected_branch: str,
        expected_head: str,
    ) -> bool:
        branch = self._git_output(workspace, ("branch", "--show-current"))
        head = self._git_output(workspace, ("rev-parse", "HEAD"))
        status = self._git_output(workspace, ("status", "--porcelain"))
        return branch == expected_branch and head == expected_head and status == ""

    def _cleanup_worktree(
        self,
        workspace: Path,
        temporary: Path,
        worktree_added: bool,
    ) -> bool:
        if worktree_added:
            removed = self._run_git(
                workspace,
                ("worktree", "remove", "--force", str(temporary)),
                timeout_seconds=180,
            )
            if not removed.succeeded:
                return False
        elif temporary.exists():
            try:
                temporary.rmdir()
            except OSError:
                return False
        listing = self._git_output(workspace, ("worktree", "list", "--porcelain"))
        if listing is None or str(temporary) in listing:
            return False
        return not temporary.exists()

    def _run_git(
        self,
        root: Path,
        arguments: Sequence[str],
        *,
        timeout_seconds: int = 120,
    ) -> CommandResultLike:
        return self._runner.run(
            ("git", "-C", str(root), *arguments),
            environment=self._environment,
            timeout_seconds=timeout_seconds,
        )

    def _git_output(self, root: Path, arguments: Sequence[str]) -> str | None:
        result = self._run_git(root, arguments)
        return result.output.strip() if result.succeeded else None


def _validate_packet(packet: DevelopmentTaskPacket) -> str | None:
    if (
        not packet.packet_identity
        or not packet.work_identity
        or packet.generation < 1
        or "/" not in packet.repository_identity
        or not packet.goal_revision
        or not packet.issue_revision
        or _SHA_RE.fullmatch(packet.exact_base_sha) is None
    ):
        return "TASK_PACKET_IDENTITY_INVALID"
    if not packet.workspace_canonical_path.is_absolute():
        return "TASK_PACKET_WORKSPACE_INVALID"
    if not packet.scope_paths or any(not _safe_relative_path(path) for path in packet.scope_paths):
        return "TASK_PACKET_SCOPE_INVALID"
    if any(not _safe_relative_path(path) for path in packet.canonical_design_targets):
        return "TASK_PACKET_DESIGN_TARGET_INVALID"
    if not packet.acceptance_checks or any(not item.strip() for item in packet.acceptance_checks):
        return "TASK_PACKET_ACCEPTANCE_INVALID"
    if packet.transition is ImplementerTransition.DESIGN and not packet.canonical_design_targets:
        return "DESIGN_TARGET_REQUIRED"
    if packet.transition in {ImplementerTransition.IMPLEMENT, ImplementerTransition.REPAIR}:
        if not packet.canonical_design_identities:
            return "CANONICAL_DESIGN_REQUIRED"
    if packet.transition is ImplementerTransition.REPAIR and not packet.active_lineage_identity:
        return "ACTIVE_LINEAGE_REQUIRED"
    return None


def _safe_relative_path(value: str) -> bool:
    if not value or value != value.strip() or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and value not in {".", "./"}


def _path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    for scope in scopes:
        normalized = scope.rstrip("/")
        if path == normalized or path.startswith(normalized + "/"):
            return True
    return False


def _remote_matches(remote: str, repository_identity: str) -> bool:
    expected = repository_identity.removesuffix(".git")
    value = remote.strip().removesuffix(".git")
    if value == expected:
        return True
    if value.endswith("/" + expected):
        return True
    return value.endswith(":" + expected)


def _sanitized_environment(
    environment: Mapping[str, str],
    extra_allowlist: Sequence[str],
) -> dict[str, str]:
    allowed = set(_BASE_ENV_NAMES)
    allowed.update(extra_allowlist)
    allowed.update(name for name in environment if name.startswith("LC_"))
    allowed.update(name for name in environment if name.startswith("XDG_"))
    forbidden = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "LOOP_POSTGRES_DSN",
        "LOOP_DATABASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_API_KEY_REVIEWER",
        "LOOP_TRUSTED_REVIEWER_SOCKET",
    }
    return {
        name: value
        for name, value in environment.items()
        if name in allowed and name not in forbidden and "REVIEWER" not in name
    }


def _instruction(packet: DevelopmentTaskPacket) -> str:
    authority = ", ".join(packet.authority_refs) or packet.repository_identity
    scope = ", ".join(packet.scope_paths)
    acceptance = "\n".join(f"- {item}" for item in packet.acceptance_checks)
    non_goals = "\n".join(f"- {item}" for item in packet.non_goals) or "- なし"
    safety = "\n".join(f"- {item}" for item in packet.safety_constraints) or "- 追加なし"
    design_targets = ", ".join(packet.canonical_design_targets) or "なし"
    return (
        f"Loop Engineeringの{packet.transition.value}遷移を1回だけ実行してください。\n"
        f"Work: {packet.work_identity}\n"
        f"exact base: {packet.exact_base_sha}\n"
        f"Authority: {authority}\n"
        f"変更可能scope: {scope}\n"
        f"canonical design target: {design_targets}\n"
        "受入条件:\n"
        f"{acceptance}\n"
        "対象外:\n"
        f"{non_goals}\n"
        "追加安全条件:\n"
        f"{safety}\n"
        "設計→実装の順序を守り、人間向け文章は日本語で記述してください。\n"
        "Git branch作成・切替、commit、push、rebase、force push、PR作成・更新、merge、"
        "GitHub Issue/Projectへの書込みを行わないでください。\n"
        "レビューワー認証情報やDB credentialを探索・利用せず、このworktree内の"
        "ファイル編集と必要なローカル検証だけを行ってください。"
    )
