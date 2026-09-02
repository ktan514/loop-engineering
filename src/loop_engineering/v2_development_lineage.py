"""ChangeProposalを1つのGitHub開発lineageへ安全に反映する。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from .v2_implementer import ChangeProposal
from .work_state import EffectAttempt, RecoveredWork

_SHA_RE = re.compile(r"[0-9a-f]{40}")


class LineageStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


class CommandResultLike(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def output(self) -> str: ...

    @property
    def succeeded(self) -> bool: ...


class LineageCommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> CommandResultLike: ...


class DevelopmentEffectStatePort(Protocol):
    def recover(self, work_identity: str) -> RecoveredWork | None: ...

    def record_effect_intent(self, attempt: EffectAttempt) -> bool: ...

    def record_effect_outcome(self, idempotency_key: str, status: str) -> None: ...


@dataclass(frozen=True, slots=True)
class MaterializedProposal:
    work_identity: str
    exact_base_sha: str
    candidate_sha: str
    changed_paths: tuple[str, ...]
    patch_sha256: str


@dataclass(frozen=True, slots=True)
class LineageIdentity:
    repository: str
    work_identity: str
    issue_number: int
    branch_name: str
    base_branch: str
    packet_generation: int


@dataclass(frozen=True, slots=True)
class PullRequestIdentity:
    number: int
    url: str
    head_sha: str
    head_branch: str
    base_branch: str
    draft: bool


@dataclass(frozen=True, slots=True)
class LineageResult:
    status: LineageStatus
    detail: str
    head_sha: str | None = None
    pull_request: PullRequestIdentity | None = None


class TrustedProposalMaterializer:
    """raw patchを隔離worktreeへ適用し、信頼済みcommitだけを生成する。"""

    def __init__(
        self,
        runner: LineageCommandRunner,
        environment: Mapping[str, str],
    ) -> None:
        self._runner = runner
        self._environment = dict(environment)

    def materialize(
        self,
        *,
        workspace: Path,
        repository: str,
        proposal: ChangeProposal,
        commit_message: str,
    ) -> MaterializedProposal | None:
        root = workspace.resolve(strict=False)
        if (
            not root.is_absolute()
            or not root.is_dir()
            or not commit_message.strip()
            or not _valid_repository(repository)
            or _SHA_RE.fullmatch(proposal.exact_base_sha) is None
        ):
            return None
        if hashlib.sha256(proposal.patch_text.encode("utf-8")).hexdigest() != proposal.patch_sha256:
            return None
        source = self._source_identity(root, repository, proposal.exact_base_sha)
        if source is None:
            return None
        source_branch, source_head = source

        worktree = Path(
            tempfile.mkdtemp(prefix=".loop-lineage-", dir=str(root.parent))
        ).resolve(strict=False)
        patch_path = Path(
            tempfile.mkstemp(prefix=".loop-proposal-", suffix=".patch", dir=str(root.parent))[1]
        ).resolve(strict=False)
        added = False
        result: MaterializedProposal | None = None
        try:
            patch_path.write_text(proposal.patch_text, encoding="utf-8", newline="")
            create = self._git(
                root,
                ("worktree", "add", "--detach", str(worktree), proposal.exact_base_sha),
                timeout_seconds=180,
            )
            if create.succeeded:
                added = True
                result = self._materialize_in_worktree(
                    worktree,
                    patch_path,
                    proposal,
                    commit_message,
                )
        except (OSError, UnicodeError):
            result = None
        finally:
            cleanup = self._cleanup(root, worktree, patch_path, added)

        if not cleanup:
            return None
        if not self._source_unchanged(root, source_branch, source_head):
            return None
        return result

    def _materialize_in_worktree(
        self,
        worktree: Path,
        patch_path: Path,
        proposal: ChangeProposal,
        commit_message: str,
    ) -> MaterializedProposal | None:
        if self._git_output(worktree, ("rev-parse", "HEAD")) != proposal.exact_base_sha:
            return None
        if not self._git(worktree, ("apply", "--check", str(patch_path))).succeeded:
            return None
        if not self._git(worktree, ("apply", "--index", str(patch_path))).succeeded:
            return None
        changed = self._git_output(worktree, ("diff", "--cached", "--name-only", "HEAD"))
        if changed is None:
            return None
        changed_paths = tuple(line for line in changed.splitlines() if line)
        if changed_paths != proposal.changed_paths:
            return None
        if not self._git(worktree, ("diff", "--cached", "--check", "HEAD")).succeeded:
            return None
        if not self._git(
            worktree,
            ("commit", "-m", commit_message),
            timeout_seconds=180,
        ).succeeded:
            return None
        candidate = self._git_output(worktree, ("rev-parse", "HEAD"))
        if candidate is None or _SHA_RE.fullmatch(candidate) is None:
            return None
        return MaterializedProposal(
            proposal.work_identity,
            proposal.exact_base_sha,
            candidate,
            changed_paths,
            proposal.patch_sha256,
        )

    def _source_identity(
        self,
        root: Path,
        repository: str,
        exact_base: str,
    ) -> tuple[str, str] | None:
        top = self._git_output(root, ("rev-parse", "--show-toplevel"))
        remote = self._git_output(root, ("remote", "get-url", "origin"))
        status = self._git_output(root, ("status", "--porcelain"))
        branch = self._git_output(root, ("branch", "--show-current"))
        head = self._git_output(root, ("rev-parse", "HEAD"))
        exists = self._git(root, ("cat-file", "-e", f"{exact_base}^{{commit}}"))
        if (
            top is None
            or Path(top).resolve(strict=False) != root
            or remote is None
            or not _remote_matches(remote, repository)
            or status is None
            or status
            or branch is None
            or head is None
            or not exists.succeeded
        ):
            return None
        return branch, head

    def _source_unchanged(self, root: Path, branch: str, head: str) -> bool:
        return (
            self._git_output(root, ("branch", "--show-current")) == branch
            and self._git_output(root, ("rev-parse", "HEAD")) == head
            and self._git_output(root, ("status", "--porcelain")) == ""
        )

    def _cleanup(
        self,
        root: Path,
        worktree: Path,
        patch_path: Path,
        added: bool,
    ) -> bool:
        if added:
            removed = self._git(
                root,
                ("worktree", "remove", "--force", str(worktree)),
                timeout_seconds=180,
            )
            if not removed.succeeded:
                return False
        elif worktree.exists():
            try:
                worktree.rmdir()
            except OSError:
                return False
        try:
            patch_path.unlink(missing_ok=True)
        except OSError:
            return False
        listing = self._git_output(root, ("worktree", "list", "--porcelain"))
        return (
            listing is not None
            and str(worktree) not in listing
            and not worktree.exists()
            and not patch_path.exists()
        )

    def _git(
        self,
        root: Path,
        args: Sequence[str],
        *,
        timeout_seconds: int = 120,
    ) -> CommandResultLike:
        return self._runner.run(
            ("git", "-C", str(root), *args),
            environment=self._environment,
            timeout_seconds=timeout_seconds,
        )

    def _git_output(self, root: Path, args: Sequence[str]) -> str | None:
        result = self._git(root, args)
        return result.output.strip() if result.succeeded else None


class GitHubDevelopmentLineageEffects:
    """remote branchとPRをDB intent→readbackで1回だけ確定する。"""

    def __init__(
        self,
        runner: LineageCommandRunner,
        state: DevelopmentEffectStatePort,
        workspace: Path,
        environment: Mapping[str, str],
    ) -> None:
        self._runner = runner
        self._state = state
        self._workspace = workspace.resolve(strict=False)
        self._environment = dict(environment)

    def publish(
        self,
        lineage: LineageIdentity,
        materialized: MaterializedProposal,
    ) -> LineageResult:
        if not _valid_lineage(lineage, materialized):
            return LineageResult(LineageStatus.BLOCKED, "LINEAGE_INPUT_INVALID")
        branch_result = self._ensure_branch(lineage, materialized)
        if branch_result.status is not LineageStatus.CONFIRMED:
            return branch_result
        pr = self._ensure_pull_request(lineage, materialized.candidate_sha)
        if isinstance(pr, LineageResult):
            return pr
        return LineageResult(
            LineageStatus.CONFIRMED,
            "LINEAGE_PUBLISHED",
            materialized.candidate_sha,
            pr,
        )

    def _ensure_branch(
        self,
        lineage: LineageIdentity,
        materialized: MaterializedProposal,
    ) -> LineageResult:
        observed = self._remote_branch_head(lineage.branch_name)
        if observed == materialized.candidate_sha:
            return LineageResult(LineageStatus.CONFIRMED, "BRANCH_ALREADY_CURRENT", observed)
        if observed not in {None, materialized.exact_base_sha}:
            return LineageResult(LineageStatus.BLOCKED, "STALE_REMOTE_BRANCH", observed)
        kind = "BRANCH_CREATE" if observed is None else "PUSH"
        before_head = "<absent>" if observed is None else observed
        key = _effect_key(lineage, kind, materialized.candidate_sha)
        pending = self._pending_effect(key, lineage.work_identity)
        if pending == "UNCERTAIN":
            return LineageResult(LineageStatus.UNCERTAIN, "BRANCH_EFFECT_UNCERTAIN", observed)
        attempt = EffectAttempt(
            idempotency_key=key,
            work_identity=lineage.work_identity,
            kind=kind,
            target_identity=f"branch:{lineage.branch_name}",
            status="INTENT_RECORDED",
            packet_generation=lineage.packet_generation,
            expected_preconditions=(("head", before_head),),
            expected_effect=(("head", materialized.candidate_sha),),
        )
        if not self._state.record_effect_intent(attempt):
            fresh = self._remote_branch_head(lineage.branch_name)
            if fresh == materialized.candidate_sha:
                return LineageResult(LineageStatus.CONFIRMED, "BRANCH_EFFECT_CONFIRMED", fresh)
            return LineageResult(LineageStatus.BLOCKED, "BRANCH_EFFECT_STATE_CONFLICT", fresh)
        fresh_before = self._remote_branch_head(lineage.branch_name)
        if fresh_before != observed:
            self._state.record_effect_outcome(key, "NO_EFFECT")
            return LineageResult(LineageStatus.BLOCKED, "BRANCH_PRECONDITION_CHANGED", fresh_before)
        push = self._run(
            (
                "git",
                "push",
                "origin",
                f"{materialized.candidate_sha}:refs/heads/{lineage.branch_name}",
            ),
            timeout_seconds=300,
        )
        fresh = self._remote_branch_head(lineage.branch_name)
        if fresh == materialized.candidate_sha:
            self._state.record_effect_outcome(key, "CONFIRMED")
            return LineageResult(LineageStatus.CONFIRMED, "BRANCH_EFFECT_CONFIRMED", fresh)
        if push.succeeded and fresh == observed:
            self._state.record_effect_outcome(key, "NO_EFFECT")
            return LineageResult(LineageStatus.FAILED, "BRANCH_EFFECT_NO_EFFECT", fresh)
        self._state.record_effect_outcome(key, "UNCERTAIN")
        return LineageResult(LineageStatus.UNCERTAIN, "BRANCH_EFFECT_READBACK_UNPROVEN", fresh)

    def _ensure_pull_request(
        self,
        lineage: LineageIdentity,
        head_sha: str,
    ) -> PullRequestIdentity | LineageResult:
        pulls = self._open_pulls(lineage)
        if pulls is None:
            return LineageResult(LineageStatus.UNCERTAIN, "PR_READBACK_UNAVAILABLE", head_sha)
        if len(pulls) > 1:
            return LineageResult(LineageStatus.BLOCKED, "COMPETING_LINEAGE", head_sha)
        if pulls:
            pull = pulls[0]
            if pull.head_sha != head_sha:
                return LineageResult(LineageStatus.BLOCKED, "PR_HEAD_STALE", pull.head_sha)
            return pull

        key = _effect_key(lineage, "PR_CREATE", head_sha)
        pending = self._pending_effect(key, lineage.work_identity)
        if pending == "UNCERTAIN":
            return LineageResult(LineageStatus.UNCERTAIN, "PR_CREATE_UNCERTAIN", head_sha)
        attempt = EffectAttempt(
            idempotency_key=key,
            work_identity=lineage.work_identity,
            kind="PR_CREATE",
            target_identity=f"pr-lineage:{lineage.branch_name}:{lineage.base_branch}",
            status="INTENT_RECORDED",
            packet_generation=lineage.packet_generation,
            expected_preconditions=(("count", "0"), ("head", head_sha)),
            expected_effect=(("count", "1"), ("head", head_sha)),
        )
        if not self._state.record_effect_intent(attempt):
            fresh = self._open_pulls(lineage)
            if fresh is not None and len(fresh) == 1 and fresh[0].head_sha == head_sha:
                return fresh[0]
            return LineageResult(LineageStatus.BLOCKED, "PR_CREATE_STATE_CONFLICT", head_sha)
        if self._open_pulls(lineage) != ():
            self._state.record_effect_outcome(key, "NO_EFFECT")
            return LineageResult(LineageStatus.BLOCKED, "PR_CREATE_PRECONDITION_CHANGED", head_sha)
        created = self._run(
            (
                "gh",
                "pr",
                "create",
                "--repo",
                lineage.repository,
                "--base",
                lineage.base_branch,
                "--head",
                lineage.branch_name,
                "--draft",
                "--title",
                f"#{lineage.issue_number} の実装",
                "--body",
                f"Issue #{lineage.issue_number} のLoop Engineering管理lineageです。",
            ),
            timeout_seconds=120,
        )
        fresh = self._open_pulls(lineage)
        if fresh is not None and len(fresh) == 1 and fresh[0].head_sha == head_sha:
            self._state.record_effect_outcome(key, "CONFIRMED")
            return fresh[0]
        if created.succeeded and fresh == ():
            self._state.record_effect_outcome(key, "NO_EFFECT")
            return LineageResult(LineageStatus.FAILED, "PR_CREATE_NO_EFFECT", head_sha)
        self._state.record_effect_outcome(key, "UNCERTAIN")
        return LineageResult(LineageStatus.UNCERTAIN, "PR_CREATE_READBACK_UNPROVEN", head_sha)

    def _remote_branch_head(self, branch: str) -> str | None:
        result = self._run(
            ("git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"),
        )
        if not result.succeeded:
            return None
        lines = [line for line in result.output.splitlines() if line.strip()]
        if not lines:
            return None
        if len(lines) != 1:
            return "<ambiguous>"
        sha = lines[0].split(maxsplit=1)[0]
        return sha if _SHA_RE.fullmatch(sha) is not None else "<ambiguous>"

    def _open_pulls(self, lineage: LineageIdentity) -> tuple[PullRequestIdentity, ...] | None:
        result = self._run(
            (
                "gh",
                "pr",
                "list",
                "--repo",
                lineage.repository,
                "--state",
                "open",
                "--head",
                lineage.branch_name,
                "--base",
                lineage.base_branch,
                "--json",
                "number,url,headRefOid,headRefName,baseRefName,isDraft",
            )
        )
        if not result.succeeded:
            return None
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, list):
            return None
        pulls: list[PullRequestIdentity] = []
        for raw in payload:
            if not isinstance(raw, dict):
                return None
            number = raw.get("number")
            url = raw.get("url")
            head = raw.get("headRefOid")
            head_branch = raw.get("headRefName")
            base = raw.get("baseRefName")
            draft = raw.get("isDraft")
            if (
                not isinstance(number, int)
                or not isinstance(url, str)
                or not isinstance(head, str)
                or not isinstance(head_branch, str)
                or not isinstance(base, str)
                or not isinstance(draft, bool)
            ):
                return None
            pulls.append(PullRequestIdentity(number, url, head, head_branch, base, draft))
        return tuple(pulls)

    def _pending_effect(self, key: str, work_identity: str) -> str | None:
        recovered = self._state.recover(work_identity)
        if recovered is None:
            return None
        for effect in recovered.pending_effects:
            if effect.idempotency_key == key:
                return effect.status
        return None

    def _run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: int = 120,
    ) -> CommandResultLike:
        try:
            return self._runner.run(
                command,
                cwd=self._workspace,
                environment=self._environment,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return _FailedResult()


@dataclass(frozen=True, slots=True)
class _FailedResult:
    returncode: int = 127
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return False


def _valid_lineage(lineage: LineageIdentity, proposal: MaterializedProposal) -> bool:
    return (
        _valid_repository(lineage.repository)
        and lineage.work_identity == proposal.work_identity
        and lineage.issue_number > 0
        and lineage.packet_generation > 0
        and bool(lineage.branch_name)
        and bool(lineage.base_branch)
        and lineage.branch_name != lineage.base_branch
        and lineage.branch_name not in {"main", "master"}
        and _SHA_RE.fullmatch(proposal.exact_base_sha) is not None
        and _SHA_RE.fullmatch(proposal.candidate_sha) is not None
        and proposal.exact_base_sha != proposal.candidate_sha
    )


def _valid_repository(repository: str) -> bool:
    if repository.count("/") != 1 or "\x00" in repository:
        return False
    owner, name = repository.split("/", 1)
    return bool(owner and name and owner.strip() == owner and name.strip() == name)


def _remote_matches(remote: str, repository: str) -> bool:
    expected = repository.removesuffix(".git")
    value = remote.strip().removesuffix(".git")
    return value == expected or value.endswith("/" + expected) or value.endswith(":" + expected)


def _effect_key(lineage: LineageIdentity, kind: str, head: str) -> str:
    payload = (
        f"{lineage.repository}|{lineage.work_identity}|{lineage.packet_generation}|"
        f"{kind}|{lineage.branch_name}|{lineage.base_branch}|{head}"
    )
    return "dev-effect:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
