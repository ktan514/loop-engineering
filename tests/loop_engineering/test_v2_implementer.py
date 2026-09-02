import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from loop_engineering.v2_implementer import (
    CodexProposalImplementer,
    DevelopmentTaskPacket,
    ImplementerStatus,
    ImplementerTransition,
)


@dataclass(frozen=True)
class Result:
    returncode: int
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class FakeRunner:
    def __init__(
        self,
        source: Path,
        *,
        exact_base: str,
        changed_paths: tuple[str, ...] = ("docs/design.md",),
        patch: str = "diff --git a/docs/design.md b/docs/design.md\n+design\n",
        codex_success: bool = True,
        git_mutation: bool = False,
        diff_check_success: bool = True,
    ) -> None:
        self.source = source
        self.exact_base = exact_base
        self.source_head = "f" * 40
        self.changed_paths = changed_paths
        self.patch = patch
        self.codex_success = codex_success
        self.git_mutation = git_mutation
        self.diff_check_success = diff_check_success
        self.codex_environment: Mapping[str, str] | None = None
        self.codex_executed = False
        self.temporary_paths: list[Path] = []

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
        if values[0] != "git":
            self.codex_executed = True
            self.codex_environment = environment
            assert cwd is not None
            return Result(0 if self.codex_success else 1)

        root = Path(values[2])
        args = values[3:]
        if root == self.source:
            return self._source_git(args)
        return self._isolated_git(root, args)

    def _source_git(self, args: tuple[str, ...]) -> Result:
        if args == ("rev-parse", "--show-toplevel"):
            return Result(0, str(self.source))
        if args == ("remote", "get-url", "origin"):
            return Result(0, "https://github.com/owner/sample.git")
        if args == ("status", "--porcelain"):
            return Result(0, "")
        if args[:2] == ("cat-file", "-e"):
            return Result(0)
        if args == ("branch", "--show-current"):
            return Result(0, "main")
        if args == ("rev-parse", "HEAD"):
            return Result(0, self.source_head)
        if args[:3] == ("worktree", "add", "--detach"):
            path = Path(args[3])
            self.temporary_paths.append(path)
            return Result(0)
        if args[:3] == ("worktree", "remove", "--force"):
            path = Path(args[3])
            if path.exists():
                path.rmdir()
            return Result(0)
        if args == ("worktree", "list", "--porcelain"):
            return Result(0, f"worktree {self.source}\nHEAD {self.source_head}\n")
        raise AssertionError(f"unexpected source git command: {args}")

    def _isolated_git(self, root: Path, args: tuple[str, ...]) -> Result:
        assert root in self.temporary_paths
        if args == ("rev-parse", "HEAD"):
            if self.codex_executed and self.git_mutation:
                return Result(0, "b" * 40)
            return Result(0, self.exact_base)
        if args == ("branch", "--show-current"):
            return Result(0, "")
        if args == ("add", "-A"):
            return Result(0)
        if args == ("diff", "--cached", "--name-only", "HEAD"):
            return Result(0, "\n".join(self.changed_paths))
        if args == ("diff", "--cached", "--check", "HEAD"):
            return Result(0 if self.diff_check_success else 1)
        if args == ("diff", "--cached", "--binary", "--no-ext-diff", "HEAD"):
            return Result(0, self.patch)
        raise AssertionError(f"unexpected isolated git command: {args}")


def packet(tmp_path: Path, transition: ImplementerTransition) -> DevelopmentTaskPacket:
    source = tmp_path / "product"
    source.mkdir(exist_ok=True)
    return DevelopmentTaskPacket(
        packet_identity="packet:1",
        work_identity="work:owner/sample:1",
        generation=1,
        transition=transition,
        repository_identity="owner/sample",
        workspace_canonical_path=source,
        exact_base_sha="a" * 40,
        goal_revision="goal-1",
        issue_revision="issue-1",
        scope_paths=("docs", "src"),
        acceptance_checks=("設計と実装が一致する",),
        canonical_design_identities=("design:abc",),
        canonical_design_targets=("docs/design.md",),
        active_lineage_identity="lineage:1",
        authority_refs=("Issue #1",),
    )


def implementer(runner: FakeRunner) -> CodexProposalImplementer:
    environment = {
        "PATH": "/usr/bin",
        "HOME": "/tmp/home",
        "GH_TOKEN": "forbidden",
        "OPENAI_API_KEY_REVIEWER": "forbidden",
        "LOOP_POSTGRES_DSN": "forbidden",
        "LOOP_TRUSTED_REVIEWER_SOCKET": "forbidden",
    }
    return CodexProposalImplementer(runner, ("codex", "exec"), environment)


def test_design_returns_scoped_patch_and_strips_credentials(tmp_path: Path) -> None:
    task = packet(tmp_path, ImplementerTransition.DESIGN)
    runner = FakeRunner(task.workspace_canonical_path, exact_base=task.exact_base_sha)

    result = implementer(runner).execute(task)

    assert result.status is ImplementerStatus.SUCCESS
    assert result.proposal is not None
    assert result.proposal.changed_paths == ("docs/design.md",)
    assert result.proposal.design_targets_changed == ("docs/design.md",)
    assert result.proposal.patch_sha256 == hashlib.sha256(runner.patch.encode()).hexdigest()
    assert runner.codex_environment == {"PATH": "/usr/bin", "HOME": "/tmp/home"}
    assert all(not path.exists() for path in runner.temporary_paths)


def test_implement_requires_canonical_design(tmp_path: Path) -> None:
    task = packet(tmp_path, ImplementerTransition.IMPLEMENT)
    task = DevelopmentTaskPacket(
        **{
            field: getattr(task, field)
            for field in task.__dataclass_fields__
            if field != "canonical_design_identities"
        },
        canonical_design_identities=(),
    )
    runner = FakeRunner(task.workspace_canonical_path, exact_base=task.exact_base_sha)

    result = implementer(runner).execute(task)

    assert result.status is ImplementerStatus.BLOCKED
    assert result.detail == "CANONICAL_DESIGN_REQUIRED"
    assert runner.codex_executed is False


def test_repair_requires_active_lineage(tmp_path: Path) -> None:
    task = packet(tmp_path, ImplementerTransition.REPAIR)
    task = DevelopmentTaskPacket(
        **{
            field: getattr(task, field)
            for field in task.__dataclass_fields__
            if field != "active_lineage_identity"
        },
        active_lineage_identity=None,
    )
    runner = FakeRunner(task.workspace_canonical_path, exact_base=task.exact_base_sha)

    result = implementer(runner).execute(task)

    assert result.status is ImplementerStatus.BLOCKED
    assert result.detail == "ACTIVE_LINEAGE_REQUIRED"


def test_scope_violation_is_rejected_and_worktree_is_removed(tmp_path: Path) -> None:
    task = packet(tmp_path, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(
        task.workspace_canonical_path,
        exact_base=task.exact_base_sha,
        changed_paths=("outside.txt",),
    )

    result = implementer(runner).execute(task)

    assert result.status is ImplementerStatus.BLOCKED
    assert result.detail == "PROPOSAL_SCOPE_VIOLATION"
    assert all(not path.exists() for path in runner.temporary_paths)


def test_codex_commit_is_detected(tmp_path: Path) -> None:
    task = packet(tmp_path, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(
        task.workspace_canonical_path,
        exact_base=task.exact_base_sha,
        git_mutation=True,
    )

    result = implementer(runner).execute(task)

    assert result.status is ImplementerStatus.BLOCKED
    assert result.detail == "CODEX_GIT_MUTATION_DETECTED"


def test_codex_failure_still_removes_isolated_worktree(tmp_path: Path) -> None:
    task = packet(tmp_path, ImplementerTransition.IMPLEMENT)
    runner = FakeRunner(
        task.workspace_canonical_path,
        exact_base=task.exact_base_sha,
        codex_success=False,
    )

    result = implementer(runner).execute(task)

    assert result.status is ImplementerStatus.FAILED
    assert result.detail == "CODEX_EXECUTION_FAILED"
    assert all(not path.exists() for path in runner.temporary_paths)


def test_design_cannot_modify_implementation_files(tmp_path: Path) -> None:
    task = packet(tmp_path, ImplementerTransition.DESIGN)
    runner = FakeRunner(
        task.workspace_canonical_path,
        exact_base=task.exact_base_sha,
        changed_paths=("docs/design.md", "src/app.py"),
    )

    result = implementer(runner).execute(task)

    assert result.status is ImplementerStatus.BLOCKED
    assert result.detail == "DESIGN_SCOPE_VIOLATION"
