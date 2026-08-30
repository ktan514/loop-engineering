import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from loop_engineering.host_runtime import HostTarget, LocalCommandResult
from loop_engineering.trusted_worktree import PreparedWorktree, TrustedWorktree

from .conftest import config

ResponseScript = LocalCommandResult | tuple[LocalCommandResult, ...]
_BRANCH = "management/v2-repository-hygiene-guard"


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


class SuccessfulFinalizeRunner:
    def __init__(self, committed: str) -> None:
        self.committed = committed
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
        if args == ("git", "add", "-A"):
            return LocalCommandResult(0, "")
        if args == ("git", "ls-files", "-u"):
            return LocalCommandResult(0, "")
        if args == ("git", "diff", "--cached", "--check"):
            return LocalCommandResult(0, "")
        if args == ("git", "diff", "--cached", "--quiet"):
            return LocalCommandResult(1, "")
        if args == ("git", "commit", "-m", "#384 を最新基幹へ統合する"):
            return LocalCommandResult(0, "")
        if args == ("git", "rev-parse", "HEAD"):
            return LocalCommandResult(0, self.committed + "\n")
        if args == ("git", "push", "-u", "origin", f"HEAD:{_BRANCH}"):
            return LocalCommandResult(0, "")
        if (
            len(args) == 5
            and args[:3] == ("gh", "api", "repos/ktan514/ai-liver-yura/issues/450/comments")
            and args[3] == "-f"
            and args[4].startswith("body=## Mission Checkpoint")
        ):
            return LocalCommandResult(0, "")
        raise AssertionError(f"想定外のコマンドです: {args}")


def _target(head: str) -> HostTarget:
    return HostTarget(384, True, 441, head, True, False, 1, head)


def _prepared(head: str) -> PreparedWorktree:
    return PreparedWorktree(_BRANCH, head, 441, True)


def _live_pull(head: str, branch: str = _BRANCH) -> LocalCommandResult:
    return LocalCommandResult(
        0,
        json.dumps({"head": {"ref": branch, "sha": head}}),
    )


def _cleanup_after_abort_success(start: str) -> dict[tuple[str, ...], ResponseScript]:
    return {
        ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD"): (
            LocalCommandResult(0, "b" * 40 + "\n"),
            LocalCommandResult(1, ""),
        ),
        ("git", "merge", "--abort"): LocalCommandResult(0, ""),
        ("git", "branch", "--show-current"): LocalCommandResult(0, _BRANCH + "\n"),
        ("git", "rev-parse", "HEAD"): LocalCommandResult(0, start + "\n"),
        ("git", "status", "--porcelain"): LocalCommandResult(0, ""),
    }


def test_finalize_stages_resolution_before_unmerged_gate_and_commits() -> None:
    start = "a" * 40
    committed = "c" * 40
    runner = SuccessfulFinalizeRunner(committed)
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is not None
    assert result.head_sha == committed
    assert result.pr_number == 441
    assert runner.commands.index(("git", "add", "-A")) < runner.commands.index(
        ("git", "ls-files", "-u")
    )
    assert ("git", "diff", "--cached", "--check") in runner.commands
    assert ("git", "merge", "--abort") not in runner.commands


def test_stage_after_codex_still_unmerged_aborts_and_restores_clean_start_head() -> None:
    start = "a" * 40
    responses: dict[tuple[str, ...], ResponseScript] = {
        ("git", "add", "-A"): LocalCommandResult(0, ""),
        ("git", "ls-files", "-u"): LocalCommandResult(
            0, "100644 deadbeef 1\tAGENTS.md\n"
        ),
        **_cleanup_after_abort_success(start),
    }
    runner = ScriptedRunner(responses)
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert not worktree.reconciliation_cleanup_failed
    assert ("git", "merge", "--abort") in runner.commands
    assert not any(command[:2] == ("git", "reset") for command in runner.commands)


def test_cached_diff_check_failure_aborts_and_restores_clean_start_head() -> None:
    start = "a" * 40
    responses: dict[tuple[str, ...], ResponseScript] = {
        ("git", "add", "-A"): LocalCommandResult(0, ""),
        ("git", "ls-files", "-u"): LocalCommandResult(0, ""),
        ("git", "diff", "--cached", "--check"): LocalCommandResult(1, ""),
        **_cleanup_after_abort_success(start),
    }
    runner = ScriptedRunner(responses)
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert not worktree.reconciliation_cleanup_failed
    assert ("git", "merge", "--abort") in runner.commands


def test_abort_failure_uses_hard_reset_only_after_fresh_safety_checks() -> None:
    start = "a" * 40
    merge_head_probe = ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD")
    runner = ScriptedRunner(
        {
            ("git", "add", "-A"): LocalCommandResult(0, ""),
            ("git", "ls-files", "-u"): LocalCommandResult(
                0, "100644 deadbeef 1\tAGENTS.md\n"
            ),
            merge_head_probe: (
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(1, ""),
            ),
            ("git", "merge", "--abort"): LocalCommandResult(128, ""),
            ("git", "branch", "--show-current"): (
                LocalCommandResult(0, _BRANCH + "\n"),
                LocalCommandResult(0, _BRANCH + "\n"),
            ),
            ("git", "rev-parse", "HEAD"): (
                LocalCommandResult(0, start + "\n"),
                LocalCommandResult(0, start + "\n"),
            ),
            ("gh", "api", "repos/ktan514/ai-liver-yura/pulls/441"): _live_pull(start),
            ("git", "reset", "--hard", start): LocalCommandResult(0, ""),
            ("git", "status", "--porcelain"): LocalCommandResult(0, ""),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert not worktree.reconciliation_cleanup_failed
    assert ("git", "reset", "--hard", start) in runner.commands


def test_abort_failure_does_not_reset_when_current_head_moved() -> None:
    start = "a" * 40
    moved = "c" * 40
    merge_head_probe = ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD")
    runner = ScriptedRunner(
        {
            ("git", "add", "-A"): LocalCommandResult(0, ""),
            ("git", "ls-files", "-u"): LocalCommandResult(
                0, "100644 deadbeef 1\tAGENTS.md\n"
            ),
            merge_head_probe: (
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(0, "b" * 40 + "\n"),
            ),
            ("git", "merge", "--abort"): LocalCommandResult(128, ""),
            ("git", "branch", "--show-current"): LocalCommandResult(0, _BRANCH + "\n"),
            ("git", "rev-parse", "HEAD"): LocalCommandResult(0, moved + "\n"),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert worktree.reconciliation_cleanup_failed
    assert not any(command[:2] == ("git", "reset") for command in runner.commands)
    assert not any(command[:2] == ("gh", "api") for command in runner.commands)


def test_abort_failure_does_not_reset_when_live_pr_head_moved() -> None:
    start = "a" * 40
    moved = "c" * 40
    merge_head_probe = ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD")
    runner = ScriptedRunner(
        {
            ("git", "add", "-A"): LocalCommandResult(0, ""),
            ("git", "ls-files", "-u"): LocalCommandResult(
                0, "100644 deadbeef 1\tAGENTS.md\n"
            ),
            merge_head_probe: (
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(0, "b" * 40 + "\n"),
            ),
            ("git", "merge", "--abort"): LocalCommandResult(128, ""),
            ("git", "branch", "--show-current"): LocalCommandResult(0, _BRANCH + "\n"),
            ("git", "rev-parse", "HEAD"): LocalCommandResult(0, start + "\n"),
            ("gh", "api", "repos/ktan514/ai-liver-yura/pulls/441"): _live_pull(moved),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert worktree.reconciliation_cleanup_failed
    assert not any(command[:2] == ("git", "reset") for command in runner.commands)


def test_abort_failure_does_not_reset_when_github_fresh_read_fails() -> None:
    start = "a" * 40
    merge_head_probe = ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD")
    runner = ScriptedRunner(
        {
            ("git", "add", "-A"): LocalCommandResult(0, ""),
            ("git", "ls-files", "-u"): LocalCommandResult(
                0, "100644 deadbeef 1\tAGENTS.md\n"
            ),
            merge_head_probe: (
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(0, "b" * 40 + "\n"),
            ),
            ("git", "merge", "--abort"): LocalCommandResult(128, ""),
            ("git", "branch", "--show-current"): LocalCommandResult(0, _BRANCH + "\n"),
            ("git", "rev-parse", "HEAD"): LocalCommandResult(0, start + "\n"),
            ("gh", "api", "repos/ktan514/ai-liver-yura/pulls/441"): LocalCommandResult(1, ""),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert worktree.reconciliation_cleanup_failed
    assert not any(command[:2] == ("git", "reset") for command in runner.commands)


def test_hard_reset_failure_remains_cleanup_failure() -> None:
    start = "a" * 40
    merge_head_probe = ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD")
    runner = ScriptedRunner(
        {
            ("git", "add", "-A"): LocalCommandResult(0, ""),
            ("git", "ls-files", "-u"): LocalCommandResult(
                0, "100644 deadbeef 1\tAGENTS.md\n"
            ),
            merge_head_probe: (
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(0, "b" * 40 + "\n"),
            ),
            ("git", "merge", "--abort"): LocalCommandResult(128, ""),
            ("git", "branch", "--show-current"): LocalCommandResult(0, _BRANCH + "\n"),
            ("git", "rev-parse", "HEAD"): LocalCommandResult(0, start + "\n"),
            ("gh", "api", "repos/ktan514/ai-liver-yura/pulls/441"): _live_pull(start),
            ("git", "reset", "--hard", start): LocalCommandResult(128, ""),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert worktree.reconciliation_cleanup_failed


def test_fallback_does_not_git_clean_untracked_residue() -> None:
    start = "a" * 40
    merge_head_probe = ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD")
    runner = ScriptedRunner(
        {
            ("git", "add", "-A"): LocalCommandResult(0, ""),
            ("git", "ls-files", "-u"): LocalCommandResult(
                0, "100644 deadbeef 1\tAGENTS.md\n"
            ),
            merge_head_probe: (
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(0, "b" * 40 + "\n"),
                LocalCommandResult(1, ""),
            ),
            ("git", "merge", "--abort"): LocalCommandResult(128, ""),
            ("git", "branch", "--show-current"): (
                LocalCommandResult(0, _BRANCH + "\n"),
                LocalCommandResult(0, _BRANCH + "\n"),
            ),
            ("git", "rev-parse", "HEAD"): (
                LocalCommandResult(0, start + "\n"),
                LocalCommandResult(0, start + "\n"),
            ),
            ("gh", "api", "repos/ktan514/ai-liver-yura/pulls/441"): _live_pull(start),
            ("git", "reset", "--hard", start): LocalCommandResult(0, ""),
            ("git", "status", "--porcelain"): LocalCommandResult(0, "?? extra.txt\n"),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert worktree.reconciliation_cleanup_failed
    assert not any(command[:2] == ("git", "clean") for command in runner.commands)


def test_push_failure_after_merge_commit_never_resets_history() -> None:
    start = "a" * 40
    committed = "c" * 40
    runner = ScriptedRunner(
        {
            ("git", "add", "-A"): LocalCommandResult(0, ""),
            ("git", "ls-files", "-u"): LocalCommandResult(0, ""),
            ("git", "diff", "--cached", "--check"): LocalCommandResult(0, ""),
            ("git", "diff", "--cached", "--quiet"): LocalCommandResult(1, ""),
            ("git", "commit", "-m", "#384 を最新基幹へ統合する"): LocalCommandResult(
                0, ""
            ),
            ("git", "rev-parse", "HEAD"): (
                LocalCommandResult(0, committed + "\n"),
                LocalCommandResult(0, committed + "\n"),
            ),
            ("git", "push", "-u", "origin", f"HEAD:{_BRANCH}"): LocalCommandResult(1, ""),
            ("git", "rev-parse", "-q", "--verify", "MERGE_HEAD"): (
                LocalCommandResult(1, ""),
                LocalCommandResult(1, ""),
            ),
            ("git", "branch", "--show-current"): LocalCommandResult(0, _BRANCH + "\n"),
            ("git", "status", "--porcelain"): LocalCommandResult(0, ""),
        }
    )
    worktree = TrustedWorktree(config(), runner, Path("/repo"), {"PATH": "/usr/bin"})

    result = worktree.finalize(_target(start), _prepared(start), repair=True)

    assert result is None
    assert worktree.reconciliation_cleanup_failed
    assert ("git", "merge", "--abort") not in runner.commands
    assert not any(command[:2] == ("git", "reset") for command in runner.commands)
