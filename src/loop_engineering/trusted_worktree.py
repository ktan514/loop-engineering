"""Codexの編集領域と、信頼済みホストのGit操作を分離する。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .config import LoopEngineConfig
from .host_runtime import HostTarget, LocalCommandResult, LocalRunner


@dataclass(frozen=True, slots=True)
class PreparedWorktree:
    """Codexへ渡す前に信頼済みホストが固定した作業系統。"""

    branch: str
    start_head: str
    pr_number: int | None
    reconciliation_started: bool


@dataclass(frozen=True, slots=True)
class FinalizedWorktree:
    """信頼済みホストがcommitとpushを完了した作業結果。"""

    branch: str
    head_sha: str
    pr_number: int


class TrustedWorktree:
    """Git管理情報の変更をCodexから分離し、信頼済みホストだけで実行する。"""

    def __init__(
        self,
        config: LoopEngineConfig,
        runner: LocalRunner,
        root: Path,
        environment: Mapping[str, str],
    ) -> None:
        self._config = config
        self._runner = runner
        self._root = root
        self._environment = _trusted_environment(environment)
        self._last_reconciliation_cleanup_failed = False

    @property
    def reconciliation_cleanup_failed(self) -> bool:
        """直近reconciliation失敗後のcleanupが安全に完了しなかったかを返す。"""

        return self._last_reconciliation_cleanup_failed

    def prepare(self, target: HostTarget) -> PreparedWorktree | None:
        self._last_reconciliation_cleanup_failed = False
        if not self._worktree_is_clean():
            return None

        if target.pr_number is None:
            branch = self._prepare_new_branch(target.work_issue)
            if branch is None:
                return None
            current_head = self._git_output(("rev-parse", "HEAD"))
            if current_head is None:
                return None
            return PreparedWorktree(branch, current_head, None, False)

        if target.head_sha is None:
            return None
        pull = self._api_json(f"repos/{self._config.repository}/pulls/{target.pr_number}")
        if pull is None:
            return None
        head_value = pull.get("head")
        if not isinstance(head_value, dict):
            return None
        branch_value = head_value.get("ref")
        live_head = head_value.get("sha")
        if not isinstance(branch_value, str) or not isinstance(live_head, str):
            return None
        if live_head != target.head_sha:
            return None
        if not self._checkout_existing_branch(branch_value, target.head_sha):
            return None

        reconciliation_started = False
        if _requires_reconciliation(pull):
            reconciliation_started = self._start_trunk_merge()
            if not reconciliation_started:
                return None

        return PreparedWorktree(
            branch_value,
            target.head_sha,
            target.pr_number,
            reconciliation_started,
        )

    def finalize(
        self,
        target: HostTarget,
        prepared: PreparedWorktree,
        *,
        repair: bool,
    ) -> FinalizedWorktree | None:
        unresolved = self._git_output(("diff", "--name-only", "--diff-filter=U"))
        if unresolved is None or unresolved.strip():
            return self._finalize_failed(prepared)
        if not self._git(("diff", "--check")).succeeded:
            return self._finalize_failed(prepared)
        if not self._git(("add", "-A")).succeeded:
            return self._finalize_failed(prepared)

        staged = self._git(("diff", "--cached", "--quiet"))
        if staged.returncode == 0:
            return self._finalize_failed(prepared)
        if staged.returncode != 1:
            return self._finalize_failed(prepared)

        message = _commit_message(target.work_issue, prepared.reconciliation_started, repair)
        if not self._git(("commit", "-m", message), timeout_seconds=180).succeeded:
            return self._finalize_failed(prepared)
        head_sha = self._git_output(("rev-parse", "HEAD"))
        if head_sha is None or len(head_sha) != 40:
            return self._finalize_failed(prepared)
        if not self._git(
            ("push", "-u", "origin", f"HEAD:{prepared.branch}"),
            timeout_seconds=300,
        ).succeeded:
            return self._finalize_failed(prepared)

        pr_number = prepared.pr_number
        if pr_number is None:
            pr_number = self._create_draft_pr(target.work_issue, prepared.branch)
            if pr_number is None:
                return self._finalize_failed(prepared)

        if not self._publish_checkpoint(
            target.work_issue,
            pr_number,
            prepared.branch,
            head_sha,
        ):
            return self._finalize_failed(prepared)
        self._last_reconciliation_cleanup_failed = False
        return FinalizedWorktree(prepared.branch, head_sha, pr_number)

    def abort_merge_if_needed(self, prepared: PreparedWorktree | None) -> bool:
        """reconciliation途中ならabortし、開始前のclean状態へ戻ったことまで確認する。"""

        if prepared is None or not prepared.reconciliation_started:
            self._last_reconciliation_cleanup_failed = False
            return True

        merge_head = self._git(("rev-parse", "-q", "--verify", "MERGE_HEAD"))
        if merge_head.succeeded:
            if not self._git(("merge", "--abort")).succeeded:
                self._last_reconciliation_cleanup_failed = True
                return False
        elif merge_head.returncode != 1:
            self._last_reconciliation_cleanup_failed = True
            return False

        current_head = self._git_output(("rev-parse", "HEAD"))
        merge_head_after = self._git(("rev-parse", "-q", "--verify", "MERGE_HEAD"))
        status = self._git_output(("status", "--porcelain"))
        cleaned = (
            current_head == prepared.start_head
            and merge_head_after.returncode == 1
            and status is not None
            and not status.strip()
        )
        self._last_reconciliation_cleanup_failed = not cleaned
        return cleaned

    def _finalize_failed(self, prepared: PreparedWorktree) -> None:
        if prepared.reconciliation_started:
            self.abort_merge_if_needed(prepared)
        return None

    def _prepare_new_branch(self, work_issue: int) -> str | None:
        trunk = self._config.trunk_branch
        if not self._git(("fetch", "origin", trunk), timeout_seconds=180).succeeded:
            return None
        if not self._git(("switch", trunk)).succeeded:
            return None
        if not self._git(("merge", "--ff-only", f"origin/{trunk}")).succeeded:
            return None
        branch = self._config.work_branch(work_issue)
        local = self._git(("show-ref", "--verify", "--quiet", f"refs/heads/{branch}"))
        if local.returncode == 0:
            return None
        if local.returncode not in {0, 1}:
            return None
        remote = self._git(("ls-remote", "--exit-code", "--heads", "origin", branch))
        if remote.returncode == 0:
            return None
        if remote.returncode not in {0, 2}:
            return None
        if not self._git(("switch", "-c", branch)).succeeded:
            return None
        return branch

    def _checkout_existing_branch(self, branch: str, expected_head: str) -> bool:
        if not self._git(("fetch", "origin", branch), timeout_seconds=180).succeeded:
            return False
        switched = self._git(("switch", branch))
        if not switched.succeeded:
            switched = self._git(("switch", "--track", "-c", branch, f"origin/{branch}"))
        if not switched.succeeded:
            return False
        if not self._git(("merge", "--ff-only", f"origin/{branch}")).succeeded:
            return False
        return self._git_output(("rev-parse", "HEAD")) == expected_head

    def _start_trunk_merge(self) -> bool:
        trunk = self._config.trunk_branch
        if not self._git(("fetch", "origin", trunk), timeout_seconds=180).succeeded:
            return False
        result = self._git(("merge", "--no-commit", "--no-ff", f"origin/{trunk}"))
        if result.returncode == 0:
            return True
        if result.returncode != 1:
            return False
        unresolved = self._git_output(("diff", "--name-only", "--diff-filter=U"))
        return unresolved is not None and bool(unresolved.strip())

    def _worktree_is_clean(self) -> bool:
        output = self._git_output(("status", "--porcelain"))
        return output is not None and not output.strip()

    def _create_draft_pr(self, work_issue: int, branch: str) -> int | None:
        title = f"#{work_issue} の実装を進める"
        body = (
            f"Issue #{work_issue} のLoop Engineeringによる自動実装用の下書きPRです。\n\n"
            "設計・実装・検証はRepository正本とAGENTS.mdに従う。"
        )
        created = self._gh(
            (
                "pr",
                "create",
                "--repo",
                self._config.repository,
                "--base",
                self._config.trunk_branch,
                "--head",
                branch,
                "--draft",
                "--title",
                title,
                "--body",
                body,
            ),
            timeout_seconds=120,
        )
        if not created.succeeded:
            return None
        pulls = self._api_value(
            f"repos/{self._config.repository}/pulls?state=open&head="
            f"{self._config.owner}:{branch}&per_page=10"
        )
        if not isinstance(pulls, list) or len(pulls) != 1:
            return None
        item = pulls[0]
        if not isinstance(item, dict):
            return None
        number = item.get("number")
        return number if isinstance(number, int) else None

    def _publish_checkpoint(
        self,
        work_issue: int,
        pr_number: int,
        branch: str,
        head_sha: str,
    ) -> bool:
        body = (
            "## Mission Checkpoint — ACTIVE / 実装更新\n\n"
            "- Mission state: `ACTIVE`\n"
            f"- current Work: #{work_issue}\n"
            f"- current PR: #{pr_number}\n"
            f"- current branch: `{branch}`\n"
            f"- exact HEAD: `{head_sha}`\n"
            "- 完了済み: Codexによるファイル編集と検証後、"
            "信頼済みホストがcommitとpushを実施\n"
            "- next action: 厳密HEADのCIを確認し、結果に応じて継続する"
        )
        return self._gh(
            (
                "api",
                f"repos/{self._config.repository}/issues/"
                f"{self._config.mission_issue}/comments",
                "-f",
                f"body={body}",
            )
        ).succeeded

    def _api_json(self, endpoint: str) -> dict[str, object] | None:
        value = self._api_value(endpoint)
        if not isinstance(value, dict):
            return None
        return cast(dict[str, object], value)

    def _api_value(self, endpoint: str) -> object | None:
        result = self._gh(("api", endpoint))
        if not result.succeeded:
            return None
        try:
            return cast(object, json.loads(result.output))
        except json.JSONDecodeError:
            return None

    def _git_output(self, arguments: Sequence[str]) -> str | None:
        result = self._git(arguments)
        if not result.succeeded:
            return None
        return result.output.strip()

    def _git(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: int = 120,
    ) -> LocalCommandResult:
        return self._runner.run(
            ("git", *arguments),
            cwd=self._root,
            environment=self._environment,
            timeout_seconds=timeout_seconds,
        )

    def _gh(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: int = 60,
    ) -> LocalCommandResult:
        return self._runner.run(
            ("gh", *arguments),
            cwd=self._root,
            environment=self._environment,
            timeout_seconds=timeout_seconds,
        )


def _requires_reconciliation(pull: Mapping[str, object]) -> bool:
    return pull.get("mergeable") is False or pull.get("mergeable_state") == "dirty"


def _commit_message(work_issue: int, reconciliation: bool, repair: bool) -> str:
    if reconciliation:
        return f"#{work_issue} を最新基幹へ統合する"
    if repair:
        return f"#{work_issue} の動作不具合を修正する"
    return f"#{work_issue} の実装を進める"


def _trusted_environment(environment: Mapping[str, str]) -> dict[str, str]:
    allowed = {"PATH", "HOME", "GH_TOKEN", "LANG", "LC_ALL", "TMPDIR"}
    return {key: value for key, value in environment.items() if key in allowed}
