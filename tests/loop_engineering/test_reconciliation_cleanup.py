from collections.abc import Mapping, Sequence
from pathlib import Path

from loop_engineering.host_runtime import HostTarget, LocalCommandResult
from loop_engineering.trusted_worktree import PreparedWorktree, TrustedWorktree

from .conftest import config

ResponseScript = LocalCommandResult | tuple[LocalCommandResult, ...]


class ScriptedRunner:
    def __init__(self, responses: Mapping[tuple[str, ...], ResponseScript]) -> None:
        self._responses: dict[tuple[str, ...], list[LocalCommandResult]] = {}
        for command, response in responses.items():
            self._responses[command] = list(response) if isinstance(response, tuple) else [response]
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> LocalCommandResult:
        del cwd, environment, timeout_seconds, capture_output
        args = tuple(command)
        self.commands.append(args)
        scripted = self._responses.get(args)
        if not scripted:
            raise AssertionError(f"想定外のコマンドです: {args}")
        response = scripted.pop(0)
        if not scripted:
            self._responses.pop(args, None)
        return response


def _target(head: str) -> HostTarget:
    return HostTarget(384, True, 441, head, True, False, 1, head)


def _prepared(head: str) -> PreparedWorktree:
    return PreparedWorktree(
        "management/v2-repository-hygiene-guard",
        head,
        441,
        True,
    )


def test_finalize_unresolved_conflict_aborts_and_restores_clean_start_head() -> None:
    start = "a" * 40
    merge_head_probe = ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD")
    runner = ScriptedRunner(
        {
            ("git", "diff", "--name-only", "--diff-filter=U"): LocalCommandResult(
                0, "AGENTS.md\n"
            ),
            merge_head_probe: (
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(1, ""),
            ),
            ("git", "merge", "--abort"): LocalCommandResult(0, ""),
            ("git", "rev-parse", "HEAD"): LocalCommandResult(0, start + "\n"),
            ("git", "status", "--porcelain"): LocalCommandResult(0, ""),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert not worktree.reconciliation_cleanup_failed
    assert ("git", "merge", "--abort") in runner.commands
    assert runner.commands.count(merge_head_probe) == 2


def test_abort_failure_is_exposed_as_cleanup_failure() -> None:
    start = "a" * 40
    runner = ScriptedRunner(
        {
            ("git", "diff", "--name-only", "--diff-filter=U"): LocalCommandResult(
                0, "AGENTS.md\n"
            ),
            ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD"): LocalCommandResult(
                0, "b" * 40 + "\n"
            ),
            ("git", "merge", "--abort"): LocalCommandResult(1, ""),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert worktree.reconciliation_cleanup_failed


def test_push_failure_after_merge_commit_never_resets_history() -> None:
    start = "a" * 40
    committed = "c" * 40
    runner = ScriptedRunner(
        {
            ("git", "diff", "--name-only", "--diff-filter=U"): LocalCommandResult(0, ""),
            ("git", "diff", "--check"): LocalCommandResult(0, ""),
            ("git", "add", "-A"): LocalCommandResult(0, ""),
            ("git", "diff", "--cached", "--quiet"): LocalCommandResult(1, ""),
            ("git", "commit", "-m", "#384 を最新基幹へ統合する"): LocalCommandResult(
                0, ""
            ),
            ("git", "rev-parse", "HEAD"): (
                LocalCommandResult(0, committed + "\n"),
                LocalCommandResult(0, committed + "\n"),
            ),
            (
                "git",
                "push",
                "-u",
                "origin",
                "HEAD:management/v2-repository-hygiene-guard",
            ): LocalCommandResult(1, ""),
            ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD"): (
                LocalCommandResult(1, ""),
                LocalCommandResult(1, ""),
            ),
            ("git", "status", "--porcelain"): LocalCommandResult(0, ""),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert worktree.reconciliation_cleanup_failed
    assert ("git", "merge", "--abort") not in runner.commands
    assert not any(command[:2] == ("git", "reset") for command in runner.commands)
