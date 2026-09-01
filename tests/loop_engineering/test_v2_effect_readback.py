from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field

from loop_engineering.v2_effect_readback import (
    GitHubEffectReadbackAdapter,
    GitHubIssueReportPublisher,
)
from loop_engineering.v2_resume import EffectReadbackStatus
from loop_engineering.work_state import EffectAttempt, IssueReportOutboxItem, WorkRecord


@dataclass
class Runner:
    outputs: list[str]
    calls: list[Sequence[str]] = field(default_factory=list)
    fail_at: int | None = None

    def run(self, args: Sequence[str]) -> str:
        self.calls.append(args)
        if self.fail_at == len(self.calls):
            raise subprocess.CalledProcessError(1, tuple(args))
        if not self.outputs:
            raise AssertionError("想定していないGitHub呼出しです")
        return self.outputs.pop(0)


@dataclass
class ReportState:
    reports: tuple[IssueReportOutboxItem, ...]
    published: list[str] = field(default_factory=list)

    def pending_issue_reports(self, work_identity: str) -> tuple[IssueReportOutboxItem, ...]:
        assert work_identity == record().identity
        return self.reports

    def mark_issue_report_published(self, identity: str) -> None:
        self.published.append(identity)


def record() -> WorkRecord:
    return WorkRecord(
        identity="work:ktan514/loop-engineering:66",
        repository="ktan514/loop-engineering",
        issue_number=66,
        issue_revision="definition:66",
        lifecycle="RUNNING",
    )


def attempt(
    kind: str,
    target: str,
    before: tuple[tuple[str, str], ...],
    after: tuple[tuple[str, str], ...],
    *,
    status: str = "INTENT_RECORDED",
) -> EffectAttempt:
    return EffectAttempt(
        idempotency_key=f"effect:{kind.lower()}:66",
        work_identity=record().identity,
        kind=kind,
        target_identity=target,
        status=status,
        expected_preconditions=before,
        expected_effect=after,
    )


def test_push_readback_distinguishes_confirmed_no_effect_and_unknown() -> None:
    effect = attempt(
        "PUSH",
        "branch:feature/v2",
        (("head", "before"),),
        (("head", "after"),),
    )

    confirmed = GitHubEffectReadbackAdapter(
        Runner([json.dumps({"object": {"sha": "after"}})]), record().repository
    ).readback(effect)
    no_effect = GitHubEffectReadbackAdapter(
        Runner([json.dumps({"object": {"sha": "before"}})]), record().repository
    ).readback(effect)
    unknown = GitHubEffectReadbackAdapter(
        Runner([json.dumps({"object": {"sha": "other"}})]), record().repository
    ).readback(effect)

    assert confirmed is EffectReadbackStatus.CONFIRMED
    assert no_effect is EffectReadbackStatus.NO_EFFECT
    assert unknown is EffectReadbackStatus.UNKNOWN


def test_ready_readback_requires_exact_head_before_draft_comparison() -> None:
    effect = attempt(
        "READY",
        "pr:69",
        (("head", "abc"), ("draft", "true")),
        (("draft", "false"),),
        status="UNCERTAIN",
    )
    ready_payload = {
        "number": 69,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc",
        "baseRefName": "main",
        "mergeCommit": None,
    }
    moved_payload = dict(ready_payload, headRefOid="different")

    confirmed = GitHubEffectReadbackAdapter(
        Runner([json.dumps(ready_payload)]), record().repository
    ).readback(effect)
    moved = GitHubEffectReadbackAdapter(
        Runner([json.dumps(moved_payload)]), record().repository
    ).readback(effect)

    assert confirmed is EffectReadbackStatus.CONFIRMED
    assert moved is EffectReadbackStatus.UNKNOWN


def test_merge_readback_requires_exact_pr_head_and_base() -> None:
    effect = attempt(
        "MERGE",
        "pr:69",
        (("head", "abc"), ("base", "main"), ("state", "OPEN")),
        (("state", "MERGED"),),
    )
    merged = {
        "number": 69,
        "state": "MERGED",
        "isDraft": False,
        "headRefOid": "abc",
        "baseRefName": "main",
        "mergeCommit": {"oid": "merge"},
    }
    still_open = dict(merged, state="OPEN", mergeCommit=None)
    wrong_base = dict(merged, baseRefName="develop")

    assert GitHubEffectReadbackAdapter(
        Runner([json.dumps(merged)]), record().repository
    ).readback(effect) is EffectReadbackStatus.CONFIRMED
    assert GitHubEffectReadbackAdapter(
        Runner([json.dumps(still_open)]), record().repository
    ).readback(effect) is EffectReadbackStatus.NO_EFFECT
    assert GitHubEffectReadbackAdapter(
        Runner([json.dumps(wrong_base)]), record().repository
    ).readback(effect) is EffectReadbackStatus.UNKNOWN


def test_issue_update_readback_uses_only_supported_typed_fields() -> None:
    effect = attempt(
        "ISSUE_UPDATE",
        "issue:66",
        (("state", "OPEN"), ("title", "旧題")),
        (("state", "CLOSED"), ("title", "新題")),
    )
    after = {"number": 66, "state": "CLOSED", "title": "新題"}
    mixed = {"number": 66, "state": "CLOSED", "title": "旧題"}

    assert GitHubEffectReadbackAdapter(
        Runner([json.dumps(after)]), record().repository
    ).readback(effect) is EffectReadbackStatus.CONFIRMED
    assert GitHubEffectReadbackAdapter(
        Runner([json.dumps(mixed)]), record().repository
    ).readback(effect) is EffectReadbackStatus.UNKNOWN

    unsupported = attempt(
        "ISSUE_UPDATE",
        "issue:66",
        (("labels", "old"),),
        (("labels", "new"),),
    )
    runner = Runner([])
    assert GitHubEffectReadbackAdapter(runner, record().repository).readback(
        unsupported
    ) is EffectReadbackStatus.UNKNOWN
    assert runner.calls == []


def test_missing_expectations_report_kind_and_command_failure_fail_closed() -> None:
    missing = EffectAttempt(
        "effect:missing",
        record().identity,
        "PUSH",
        "branch:feature/v2",
        "UNCERTAIN",
    )
    runner = Runner([])
    assert GitHubEffectReadbackAdapter(runner, record().repository).readback(
        missing
    ) is EffectReadbackStatus.UNKNOWN
    assert runner.calls == []

    report = attempt("REPORT", "issue:66", (("marker", "before"),), (("marker", "after"),))
    assert GitHubEffectReadbackAdapter(runner, record().repository).readback(
        report
    ) is EffectReadbackStatus.UNKNOWN
    assert runner.calls == []

    failing = Runner(["unused"], fail_at=1)
    effect = attempt(
        "PUSH", "branch:feature/v2", (("head", "before"),), (("head", "after"),)
    )
    assert GitHubEffectReadbackAdapter(failing, record().repository).readback(
        effect
    ) is EffectReadbackStatus.UNKNOWN


def test_existing_outbox_marker_suppresses_duplicate_post() -> None:
    report = IssueReportOutboxItem(
        "report:66:1", record().identity, "PROGRESS", "checkpoint:66:1", "進捗報告"
    )
    digest = hashlib.sha256(report.identity.encode()).hexdigest()
    marker = f"<!-- loop-engineering:v2-report:{digest} -->"
    runner = Runner([json.dumps([[{"body": f"{marker}\n既存報告"}]])])
    state = ReportState((report,))

    result = GitHubIssueReportPublisher(runner, state).publish_pending(record())

    assert result.attempted == 1
    assert result.published == 1
    assert result.pending == 0
    assert state.published == [report.identity]
    assert len(runner.calls) == 1


def test_new_outbox_report_is_marked_only_after_post_readback() -> None:
    report = IssueReportOutboxItem(
        "report:66:2", record().identity, "PROGRESS", "checkpoint:66:2", "DB確定済みの報告"
    )
    digest = hashlib.sha256(report.identity.encode()).hexdigest()
    marker = f"<!-- loop-engineering:v2-report:{digest} -->"
    runner = Runner(
        [
            json.dumps([[]]),
            json.dumps({"id": 100}),
            json.dumps([[{"body": f"{marker}\nDB確定済みの報告"}]]),
        ]
    )
    state = ReportState((report,))

    result = GitHubIssueReportPublisher(runner, state).publish_pending(record())

    assert result == type(result)(attempted=1, published=1, pending=0)
    assert state.published == [report.identity]
    assert len(runner.calls) == 3
    assert "--method" in runner.calls[1]
    assert marker in " ".join(runner.calls[1])


def test_post_or_readback_failure_leaves_outbox_pending() -> None:
    report = IssueReportOutboxItem(
        "report:66:3", record().identity, "PROGRESS", None, "再試行可能な報告"
    )
    state = ReportState((report,))
    failed_post = Runner([json.dumps([[]])], fail_at=2)

    result = GitHubIssueReportPublisher(failed_post, state).publish_pending(record())

    assert result.published == 0
    assert result.pending == 1
    assert state.published == []

    state = ReportState((report,))
    failed_readback = Runner([json.dumps([[]]), json.dumps({"id": 100})], fail_at=3)
    result = GitHubIssueReportPublisher(failed_readback, state).publish_pending(record())
    assert result.pending == 1
    assert state.published == []
