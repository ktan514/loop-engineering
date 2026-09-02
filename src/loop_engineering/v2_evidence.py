"""exact HEADに結び付くCI・Review・Human Verification evidenceを提供する。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .v2_supervisor import EvidenceState, V2WorkObservation

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_HUMAN_MARKER = "<!-- loop-engineering-human-verification:v1 -->"
_REVIEW_TERMINAL = frozenset({"PASS", "REQUEST_CHANGES", "ESCALATE", "NOT_RUN"})


class CommandResultLike(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def output(self) -> str: ...

    @property
    def succeeded(self) -> bool: ...


class EvidenceCommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> CommandResultLike: ...


class ReviewEvidenceDatabase(Protocol):
    def execute_sql(self, sql: str) -> bool: ...

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None: ...


@dataclass(frozen=True, slots=True)
class EvidenceTarget:
    repository: str
    work_identity: str
    issue_number: int
    pr_number: int
    head_sha: str
    base_branch: str
    canonical_design_identities: tuple[str, ...]
    acceptance_digest: str


@dataclass(frozen=True, slots=True)
class MachineEvidence:
    state: EvidenceState
    identity: str | None


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    request_key: str
    target: EvidenceTarget
    ci_identity: str
    reviewer_policy_identity: str


@dataclass(frozen=True, slots=True)
class ReviewResult:
    request_key: str
    target_head_sha: str
    verdict: str
    findings: tuple[str, ...]
    reviewer_identity: str


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    target: EvidenceTarget
    ci: MachineEvidence
    review: MachineEvidence
    human: MachineEvidence
    review_escalated: bool = False


@dataclass(frozen=True, slots=True)
class StoredReview:
    request_key: str
    work_identity: str
    head_sha: str
    status: str
    reviewer_identity: str | None
    findings: tuple[str, ...]


class PostgreSQLReviewEvidenceStore:
    def __init__(self, database: ReviewEvidenceDatabase) -> None:
        self._database = database

    def get(self, request_key: str) -> StoredReview | None:
        rows = self._database.query_json_rows(
            "SELECT request_key, work_identity, head_sha, status, reviewer_identity, findings "
            "FROM loop_review_evidence "
            f"WHERE request_key = {_literal(request_key)} LIMIT 1"
        )
        if rows is None:
            raise RuntimeError("REVIEW_EVIDENCE_READ_FAILED")
        if not rows:
            return None
        row = rows[0]
        findings = row.get("findings")
        if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
            raise RuntimeError("REVIEW_EVIDENCE_ROW_INVALID")
        return StoredReview(
            _required_string(row, "request_key"),
            _required_string(row, "work_identity"),
            _required_string(row, "head_sha"),
            _required_string(row, "status"),
            _optional_string(row, "reviewer_identity"),
            tuple(findings),
        )

    def mark_requested(self, request: ReviewRequest) -> bool:
        sql = (
            "INSERT INTO loop_review_evidence "
            "(request_key, work_identity, head_sha, status) VALUES ("
            f"{_literal(request.request_key)}, {_literal(request.target.work_identity)}, "
            f"{_literal(request.target.head_sha)}, 'REQUESTED') "
            "ON CONFLICT (request_key) DO NOTHING"
        )
        return self._database.execute_sql(sql)

    def save_result(self, result: ReviewResult, work_identity: str) -> None:
        if result.verdict not in _REVIEW_TERMINAL:
            raise RuntimeError("REVIEW_RESULT_INVALID")
        findings_json = _literal(
            json.dumps(result.findings, ensure_ascii=False, separators=(",", ":"))
        )
        sql = (
            "UPDATE loop_review_evidence SET "
            f"status = {_literal(result.verdict)}, "
            f"reviewer_identity = {_literal(result.reviewer_identity)}, "
            f"findings = {findings_json}::jsonb, updated_at = now() "
            f"WHERE request_key = {_literal(result.request_key)} "
            f"AND work_identity = {_literal(work_identity)}"
        )
        if not self._database.execute_sql(sql):
            raise RuntimeError("REVIEW_EVIDENCE_WRITE_FAILED")


class GitHubExactHeadCIAdapter:
    def __init__(self, runner: EvidenceCommandRunner, environment: Mapping[str, str]) -> None:
        self._runner = runner
        self._environment = dict(environment)

    def read(self, target: EvidenceTarget, workflow_name: str) -> MachineEvidence:
        if not workflow_name.strip() or not _valid_target(target):
            return MachineEvidence(EvidenceState.NOT_RUN, None)
        result = self._run(
            (
                "gh",
                "api",
                f"repos/{target.repository}/actions/runs?head_sha={target.head_sha}&per_page=100",
            )
        )
        if not result.succeeded:
            return MachineEvidence(EvidenceState.PENDING, None)
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return MachineEvidence(EvidenceState.PENDING, None)
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            return MachineEvidence(EvidenceState.PENDING, None)
        candidates: list[dict[str, object]] = []
        for raw in payload["workflow_runs"]:
            if not isinstance(raw, dict):
                continue
            if raw.get("name") != workflow_name or raw.get("head_sha") != target.head_sha:
                continue
            if not isinstance(raw.get("id"), int):
                continue
            candidates.append(raw)
        if not candidates:
            return MachineEvidence(EvidenceState.NOT_RUN, None)
        latest = max(candidates, key=lambda item: int(item["id"]))
        run_id = latest["id"]
        identity = f"ci:{run_id}:{target.head_sha}"
        if latest.get("status") != "completed":
            return MachineEvidence(EvidenceState.PENDING, identity)
        conclusion = latest.get("conclusion")
        if conclusion == "success":
            return MachineEvidence(EvidenceState.PASS, identity)
        return MachineEvidence(EvidenceState.FAIL, identity)

    def _run(self, command: Sequence[str]) -> CommandResultLike:
        try:
            return self._runner.run(command, environment=self._environment)
        except (OSError, subprocess.SubprocessError):
            return _FailedResult()


class TrustedReviewerBrokerAdapter:
    """reviewer credentialをHost broker側だけに閉じ込めるcommand adapter。"""

    def __init__(
        self,
        runner: EvidenceCommandRunner,
        argv_prefix: Sequence[str],
        environment: Mapping[str, str],
        *,
        timeout_seconds: int = 1200,
    ) -> None:
        if not argv_prefix or any(not item for item in argv_prefix):
            raise ValueError("REVIEWER_COMMAND_INVALID")
        self._runner = runner
        self._argv_prefix = tuple(argv_prefix)
        self._environment = dict(environment)
        self._timeout_seconds = timeout_seconds

    def request(self, request: ReviewRequest) -> ReviewResult | None:
        payload = {
            "request_key": request.request_key,
            "repository": request.target.repository,
            "work_identity": request.target.work_identity,
            "issue_number": request.target.issue_number,
            "pr_number": request.target.pr_number,
            "head_sha": request.target.head_sha,
            "base_branch": request.target.base_branch,
            "canonical_design_identities": list(request.target.canonical_design_identities),
            "acceptance_digest": request.target.acceptance_digest,
            "ci_identity": request.ci_identity,
            "reviewer_policy_identity": request.reviewer_policy_identity,
        }
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                delete=False,
                prefix="loop-review-",
                suffix=".json",
            ) as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                path = Path(stream.name)
            result = self._runner.run(
                (*self._argv_prefix, "--request", str(path)),
                environment=self._environment,
                timeout_seconds=self._timeout_seconds,
            )
            if not result.succeeded:
                return None
            return _review_result(result.output, request)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            return None
        finally:
            if path is not None:
                path.unlink(missing_ok=True)


class GitHubTargetReader:
    def __init__(self, runner: EvidenceCommandRunner, environment: Mapping[str, str]) -> None:
        self._runner = runner
        self._environment = dict(environment)

    def current_head(self, target: EvidenceTarget) -> str | None:
        try:
            result = self._runner.run(
                (
                    "gh",
                    "pr",
                    "view",
                    str(target.pr_number),
                    "--repo",
                    target.repository,
                    "--json",
                    "number,headRefOid,baseRefName",
                ),
                environment=self._environment,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if not result.succeeded:
            return None
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("number") != target.pr_number
            or payload.get("baseRefName") != target.base_branch
        ):
            return None
        head = payload.get("headRefOid")
        return head if isinstance(head, str) else None


class ReviewCoordinator:
    def __init__(
        self,
        store: PostgreSQLReviewEvidenceStore,
        broker: TrustedReviewerBrokerAdapter,
        target_reader: GitHubTargetReader,
        reviewer_policy_identity: str,
    ) -> None:
        if not reviewer_policy_identity.strip():
            raise ValueError("REVIEWER_POLICY_INVALID")
        self._store = store
        self._broker = broker
        self._target_reader = target_reader
        self._policy = reviewer_policy_identity

    def ensure_review(self, target: EvidenceTarget, ci: MachineEvidence) -> MachineEvidence:
        if ci.state is not EvidenceState.PASS or ci.identity is None:
            return MachineEvidence(EvidenceState.NOT_RUN, None)
        key = review_request_key(target, ci.identity, self._policy)
        stored = self._store.get(key)
        if stored is not None:
            return _stored_evidence(stored)
        if self._target_reader.current_head(target) != target.head_sha:
            return MachineEvidence(EvidenceState.NOT_RUN, f"stale:{key}")
        request = ReviewRequest(key, target, ci.identity, self._policy)
        if not self._store.mark_requested(request):
            return MachineEvidence(EvidenceState.PENDING, key)
        result = self._broker.request(request)
        if result is None:
            return MachineEvidence(EvidenceState.PENDING, key)
        if self._target_reader.current_head(target) != target.head_sha:
            stale = ReviewResult(key, target.head_sha, "NOT_RUN", (), result.reviewer_identity)
            self._store.save_result(stale, target.work_identity)
            return MachineEvidence(EvidenceState.NOT_RUN, f"stale:{key}")
        self._store.save_result(result, target.work_identity)
        return _result_evidence(result)


class GitHubHumanVerificationAdapter:
    def __init__(self, runner: EvidenceCommandRunner, environment: Mapping[str, str]) -> None:
        self._runner = runner
        self._environment = dict(environment)

    def read(self, target: EvidenceTarget, required: bool) -> MachineEvidence:
        if not required:
            return MachineEvidence(EvidenceState.NOT_REQUIRED, None)
        try:
            result = self._runner.run(
                (
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{target.repository}/issues/{target.issue_number}/comments?per_page=100",
                ),
                environment=self._environment,
            )
        except (OSError, subprocess.SubprocessError):
            return MachineEvidence(EvidenceState.PENDING, None)
        if not result.succeeded:
            return MachineEvidence(EvidenceState.PENDING, None)
        records = _human_records(result.output)
        matches = [
            record
            for record in records
            if record.get("work_identity") == target.work_identity
            and record.get("head_sha") == target.head_sha
        ]
        if not matches:
            return MachineEvidence(EvidenceState.PENDING, None)
        latest = matches[-1]
        value = latest.get("result")
        identity = "human:" + hashlib.sha256(
            json.dumps(latest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if value == "PASS":
            return MachineEvidence(EvidenceState.PASS, identity)
        if value == "FAIL":
            return MachineEvidence(EvidenceState.FAIL, identity)
        return MachineEvidence(EvidenceState.PENDING, None)


class V2EvidenceCoordinator:
    def __init__(
        self,
        ci: GitHubExactHeadCIAdapter,
        review: ReviewCoordinator,
        human: GitHubHumanVerificationAdapter,
        workflow_name: str,
    ) -> None:
        self._ci = ci
        self._review = review
        self._human = human
        self._workflow_name = workflow_name

    def observe(self, target: EvidenceTarget, human_required: bool) -> EvidenceBundle:
        ci = self._ci.read(target, self._workflow_name)
        review = self._review.ensure_review(target, ci)
        human = self._human.read(target, human_required)
        escalated = review.identity is not None and review.identity.startswith("escalate:")
        return EvidenceBundle(target, ci, review, human, escalated)


def apply_evidence(work: V2WorkObservation, bundle: EvidenceBundle) -> V2WorkObservation:
    if work.work_identity != bundle.target.work_identity:
        raise ValueError("EVIDENCE_WORK_MISMATCH")
    if work.exact_head_sha != bundle.target.head_sha:
        raise ValueError("EVIDENCE_HEAD_MISMATCH")
    return replace(
        work,
        verification_state=bundle.ci.state,
        verification_identity=bundle.ci.identity,
        review_state=bundle.review.state,
        review_identity=bundle.review.identity,
        human_verification_state=bundle.human.state,
        human_verification_identity=bundle.human.identity,
        unresolved_conflict=work.unresolved_conflict or bundle.review_escalated,
    )


def review_request_key(target: EvidenceTarget, ci_identity: str, policy: str) -> str:
    payload = {
        "policy": policy,
        "repository": target.repository,
        "work_identity": target.work_identity,
        "head_sha": target.head_sha,
        "base_branch": target.base_branch,
        "canonical_design_identities": target.canonical_design_identities,
        "acceptance_digest": target.acceptance_digest,
        "ci_identity": ci_identity,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "review:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stored_evidence(stored: StoredReview) -> MachineEvidence:
    identity = f"review:{stored.request_key}:{stored.head_sha}"
    if stored.status == "PASS":
        return MachineEvidence(EvidenceState.PASS, identity)
    if stored.status == "REQUEST_CHANGES":
        return MachineEvidence(EvidenceState.REQUEST_CHANGES, identity)
    if stored.status == "ESCALATE":
        return MachineEvidence(EvidenceState.FAIL, "escalate:" + identity)
    if stored.status == "NOT_RUN":
        return MachineEvidence(EvidenceState.NOT_RUN, identity)
    return MachineEvidence(EvidenceState.PENDING, identity)


def _result_evidence(result: ReviewResult) -> MachineEvidence:
    stored = StoredReview(
        result.request_key,
        "",
        result.target_head_sha,
        result.verdict,
        result.reviewer_identity,
        result.findings,
    )
    return _stored_evidence(stored)


def _review_result(raw: str, request: ReviewRequest) -> ReviewResult | None:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return None
    key = payload.get("request_key")
    head = payload.get("target_head_sha")
    verdict = payload.get("verdict")
    reviewer = payload.get("reviewer_identity")
    findings = payload.get("findings")
    if (
        key != request.request_key
        or head != request.target.head_sha
        or verdict not in _REVIEW_TERMINAL
        or not isinstance(reviewer, str)
        or not reviewer.strip()
        or not isinstance(findings, list)
        or len(findings) > 64
        or not all(isinstance(item, str) and len(item) <= 1000 for item in findings)
    ):
        return None
    return ReviewResult(key, head, verdict, tuple(findings), reviewer)


def _human_records(raw: str) -> tuple[dict[str, object], ...]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    pages = payload if isinstance(payload, list) else []
    comments: list[Mapping[str, object]] = []
    for page in pages:
        if isinstance(page, list):
            comments.extend(item for item in page if isinstance(item, dict))
        elif isinstance(page, dict):
            comments.append(page)
    records: list[dict[str, object]] = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str) or _HUMAN_MARKER not in body:
            continue
        after = body.split(_HUMAN_MARKER, 1)[1].strip()
        try:
            record = json.loads(after)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if (
            isinstance(record.get("work_identity"), str)
            and isinstance(record.get("head_sha"), str)
            and _SHA_RE.fullmatch(str(record["head_sha"])) is not None
            and record.get("result") in {"PASS", "FAIL"}
        ):
            records.append(record)
    return tuple(records)


def _valid_target(target: EvidenceTarget) -> bool:
    return (
        target.repository.count("/") == 1
        and bool(target.work_identity)
        and target.issue_number > 0
        and target.pr_number > 0
        and _SHA_RE.fullmatch(target.head_sha) is not None
        and bool(target.base_branch)
        and bool(target.acceptance_digest)
    )


def _literal(value: str) -> str:
    if "\x00" in value or len(value) > 4096:
        raise RuntimeError("REVIEW_EVIDENCE_VALUE_INVALID")
    return "'" + value.replace("'", "''") + "'"


def _required_string(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise RuntimeError("REVIEW_EVIDENCE_ROW_INVALID")
    return value


def _optional_string(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError("REVIEW_EVIDENCE_ROW_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class _FailedResult:
    returncode: int = 127
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return False
