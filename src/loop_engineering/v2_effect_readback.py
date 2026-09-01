"""V2の外部効果読戻しとIssue報告outbox投稿を提供する。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from urllib.parse import quote

from .v2_resume import EffectReadbackStatus
from .work_state import EffectAttempt, IssueReportOutboxItem, WorkRecord


class GitHubReadbackCommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str: ...


class IssueReportStatePort(Protocol):
    def pending_issue_reports(self, work_identity: str) -> tuple[IssueReportOutboxItem, ...]: ...

    def mark_issue_report_published(self, identity: str) -> None: ...


class IssueReportPublishStatus(str, Enum):
    PUBLISHED = "PUBLISHED"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class IssueReportPublishResult:
    attempted: int
    published: int
    pending: int


@dataclass(slots=True)
class GitHubEffectReadbackAdapter:
    """DBに記録済みの対象identityだけをGitHubから読戻す。"""

    runner: GitHubReadbackCommandRunner
    repository: str

    def readback(self, attempt: EffectAttempt) -> EffectReadbackStatus:
        if not _valid_repository(self.repository):
            return EffectReadbackStatus.UNKNOWN
        if attempt.status not in {"INTENT_RECORDED", "UNCERTAIN"}:
            return EffectReadbackStatus.UNKNOWN
        before = _pairs(attempt.expected_preconditions)
        after = _pairs(attempt.expected_effect)
        if before is None or after is None or not before or not after or before == after:
            return EffectReadbackStatus.UNKNOWN
        try:
            if attempt.kind == "PUSH":
                return self._read_push(attempt.target_identity, before, after)
            if attempt.kind == "READY":
                return self._read_ready(attempt.target_identity, before, after)
            if attempt.kind == "MERGE":
                return self._read_merge(attempt.target_identity, before, after)
            if attempt.kind == "ISSUE_UPDATE":
                return self._read_issue_update(attempt.target_identity, before, after)
            return EffectReadbackStatus.UNKNOWN
        except (OSError, subprocess.SubprocessError, ValueError):
            return EffectReadbackStatus.UNKNOWN

    def _read_push(
        self,
        target_identity: str,
        before: Mapping[str, str],
        after: Mapping[str, str],
    ) -> EffectReadbackStatus:
        branch = _target_suffix(target_identity, "branch")
        if branch is None or set(before) != {"head"} or set(after) != {"head"}:
            return EffectReadbackStatus.UNKNOWN
        raw = self.runner.run(
            (
                "gh",
                "api",
                f"repos/{self.repository}/git/ref/heads/{quote(branch, safe='')}",
            )
        )
        payload = _json_mapping(raw)
        observed = _nested_string(payload, "object", "sha")
        return _compare_scalar(observed, before["head"], after["head"])

    def _read_ready(
        self,
        target_identity: str,
        before: Mapping[str, str],
        after: Mapping[str, str],
    ) -> EffectReadbackStatus:
        number = _target_number(target_identity, "pr")
        required_before = {"head", "draft"}
        if (
            number is None
            or set(before) != required_before
            or set(after) != {"draft"}
            or before["draft"] not in {"true", "false"}
            or after["draft"] not in {"true", "false"}
        ):
            return EffectReadbackStatus.UNKNOWN
        payload = self._pr(number)
        if payload.get("number") != number or payload.get("headRefOid") != before["head"]:
            return EffectReadbackStatus.UNKNOWN
        draft = payload.get("isDraft")
        if not isinstance(draft, bool):
            return EffectReadbackStatus.UNKNOWN
        return _compare_scalar(_bool_text(draft), before["draft"], after["draft"])

    def _read_merge(
        self,
        target_identity: str,
        before: Mapping[str, str],
        after: Mapping[str, str],
    ) -> EffectReadbackStatus:
        number = _target_number(target_identity, "pr")
        required_before = {"head", "base", "state"}
        if number is None or set(before) != required_before or set(after) != {"state"}:
            return EffectReadbackStatus.UNKNOWN
        payload = self._pr(number)
        if (
            payload.get("number") != number
            or payload.get("headRefOid") != before["head"]
            or payload.get("baseRefName") != before["base"]
        ):
            return EffectReadbackStatus.UNKNOWN
        state = payload.get("state")
        if not isinstance(state, str):
            return EffectReadbackStatus.UNKNOWN
        return _compare_scalar(state, before["state"], after["state"])

    def _read_issue_update(
        self,
        target_identity: str,
        before: Mapping[str, str],
        after: Mapping[str, str],
    ) -> EffectReadbackStatus:
        number = _target_number(target_identity, "issue")
        supported = {"state", "title"}
        if (
            number is None
            or set(before) != set(after)
            or not set(before)
            or not set(before).issubset(supported)
        ):
            return EffectReadbackStatus.UNKNOWN
        raw = self.runner.run(
            (
                "gh",
                "issue",
                "view",
                str(number),
                "--repo",
                self.repository,
                "--json",
                "number,state,title",
            )
        )
        payload = _json_mapping(raw)
        if payload.get("number") != number:
            return EffectReadbackStatus.UNKNOWN
        observed: dict[str, str] = {}
        for key in before:
            value = payload.get(key)
            if not isinstance(value, str):
                return EffectReadbackStatus.UNKNOWN
            observed[key] = value
        if observed == dict(after):
            return EffectReadbackStatus.CONFIRMED
        if observed == dict(before):
            return EffectReadbackStatus.NO_EFFECT
        return EffectReadbackStatus.UNKNOWN

    def _pr(self, number: int) -> Mapping[str, object]:
        raw = self.runner.run(
            (
                "gh",
                "pr",
                "view",
                str(number),
                "--repo",
                self.repository,
                "--json",
                "number,state,isDraft,headRefOid,baseRefName,mergeCommit",
            )
        )
        return _json_mapping(raw)


@dataclass(slots=True)
class GitHubIssueReportPublisher:
    """DB確定済みoutboxをIssue commentへ重複なく投影する。"""

    runner: GitHubReadbackCommandRunner
    state: IssueReportStatePort

    def publish_pending(self, record: WorkRecord) -> IssueReportPublishResult:
        if not _valid_repository(record.repository) or record.issue_number < 1:
            raise ValueError("ISSUE_REPORT_TARGET_INVALID")
        reports = self.state.pending_issue_reports(record.identity)
        published = 0
        pending = 0
        for report in reports:
            if report.work_identity != record.identity:
                raise ValueError("ISSUE_REPORT_WORK_MISMATCH")
            status = self._publish_one(record, report)
            if status is IssueReportPublishStatus.PUBLISHED:
                self.state.mark_issue_report_published(report.identity)
                published += 1
            else:
                pending += 1
        return IssueReportPublishResult(len(reports), published, pending)

    def _publish_one(
        self,
        record: WorkRecord,
        report: IssueReportOutboxItem,
    ) -> IssueReportPublishStatus:
        marker = _report_marker(report.identity)
        try:
            comments = self._comments(record)
            if comments is None:
                return IssueReportPublishStatus.PENDING
            if _contains_marker(comments, marker):
                return IssueReportPublishStatus.PUBLISHED
            body = f"{marker}\n{report.body}"
            self.runner.run(
                (
                    "gh",
                    "api",
                    f"repos/{record.repository}/issues/{record.issue_number}/comments",
                    "--method",
                    "POST",
                    "--raw-field",
                    f"body={body}",
                )
            )
            readback = self._comments(record)
            if readback is not None and _contains_marker(readback, marker):
                return IssueReportPublishStatus.PUBLISHED
        except (OSError, subprocess.SubprocessError, ValueError):
            return IssueReportPublishStatus.PENDING
        return IssueReportPublishStatus.PENDING

    def _comments(self, record: WorkRecord) -> tuple[Mapping[str, object], ...] | None:
        raw = self.runner.run(
            (
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{record.repository}/issues/{record.issue_number}/comments?per_page=100",
            )
        )
        payload = json.loads(raw)
        return _comment_pages(payload)


def _valid_repository(repository: str) -> bool:
    if repository.count("/") != 1 or "\x00" in repository or len(repository) > 200:
        return False
    owner, name = repository.split("/", maxsplit=1)
    return bool(owner and name and owner.strip() == owner and name.strip() == name)


def _pairs(values: tuple[tuple[str, str], ...]) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for key, value in values:
        if not key or key in result or "\x00" in key or "\x00" in value:
            return None
        result[key] = value
    return result


def _target_suffix(identity: str, prefix: str) -> str | None:
    marker = f"{prefix}:"
    if not identity.startswith(marker):
        return None
    value = identity[len(marker) :]
    if not value or "\x00" in value or len(value) > 255:
        return None
    return value


def _target_number(identity: str, prefix: str) -> int | None:
    value = _target_suffix(identity, prefix)
    if value is None or not value.isdigit():
        return None
    number = int(value)
    return number if number > 0 else None


def _json_mapping(raw: str) -> Mapping[str, object]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("GITHUB_READBACK_INVALID")
    return payload


def _nested_string(payload: Mapping[str, object], key: str, nested_key: str) -> str | None:
    nested = payload.get(key)
    if not isinstance(nested, dict):
        return None
    value = nested.get(nested_key)
    return value if isinstance(value, str) else None


def _compare_scalar(observed: str | None, before: str, after: str) -> EffectReadbackStatus:
    if observed == after:
        return EffectReadbackStatus.CONFIRMED
    if observed == before:
        return EffectReadbackStatus.NO_EFFECT
    return EffectReadbackStatus.UNKNOWN


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _report_marker(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"<!-- loop-engineering:v2-report:{digest} -->"


def _comment_pages(payload: object) -> tuple[Mapping[str, object], ...] | None:
    if not isinstance(payload, list):
        return None
    comments: list[Mapping[str, object]] = []
    for page in payload:
        if isinstance(page, list):
            for item in page:
                if not isinstance(item, dict):
                    return None
                comments.append(item)
            continue
        if isinstance(page, dict):
            comments.append(page)
            continue
        return None
    return tuple(comments)


def _contains_marker(comments: tuple[Mapping[str, object], ...], marker: str) -> bool:
    for comment in comments:
        body = comment.get("body")
        if isinstance(body, str) and marker in body:
            return True
    return False
