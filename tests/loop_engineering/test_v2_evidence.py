import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from loop_engineering.v2_evidence import (
    EvidenceBundle,
    EvidenceTarget,
    GitHubExactHeadCIAdapter,
    GitHubHumanVerificationAdapter,
    GitHubTargetReader,
    MachineEvidence,
    PostgreSQLReviewEvidenceStore,
    ReviewCoordinator,
    TrustedReviewerBrokerAdapter,
    apply_evidence,
    review_request_key,
)
from loop_engineering.v2_supervisor import (
    EvidenceState,
    V2Transition,
    V2WorkObservation,
    derive_transition,
)


@dataclass(frozen=True)
class Result:
    returncode: int = 0
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class FakeDatabase:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def execute_sql(self, sql: str) -> bool:
        if sql.startswith("INSERT INTO loop_review_evidence"):
            values = _quoted_values(sql)
            key = values[0]
            self.rows.setdefault(
                key,
                {
                    "request_key": values[0],
                    "work_identity": values[1],
                    "head_sha": values[2],
                    "status": "REQUESTED",
                    "reviewer_identity": None,
                    "findings": [],
                },
            )
            return True
        if sql.startswith("UPDATE loop_review_evidence SET"):
            values = _quoted_values(sql)
            status, reviewer, findings_json, key, work_identity = values
            row = self.rows[key]
            assert row["work_identity"] == work_identity
            row["status"] = status
            row["reviewer_identity"] = reviewer
            row["findings"] = json.loads(findings_json)
            return True
        raise AssertionError(sql)

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None:
        key = select_sql.split("request_key = '", 1)[1].split("'", 1)[0]
        row = self.rows.get(key)
        return [] if row is None else [dict(row)]


class EvidenceRunner:
    def __init__(self, target: EvidenceTarget) -> None:
        self.target = target
        self.current_head = target.head_sha
        self.ci_runs: list[dict[str, object]] = []
        self.comments: list[dict[str, object]] = []
        self.broker_calls = 0
        self.broker_verdict = "PASS"
        self.move_head_after_review = False

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> Result:
        del cwd, environment, timeout_seconds, capture_output
        values = tuple(command)
        if values[:2] == ("gh", "api") and "actions/runs?" in values[-1]:
            return Result(output=json.dumps({"workflow_runs": self.ci_runs}))
        if values[:3] == ("gh", "pr", "view"):
            return Result(
                output=json.dumps(
                    {
                        "number": self.target.pr_number,
                        "headRefOid": self.current_head,
                        "baseRefName": self.target.base_branch,
                    }
                )
            )
        if values[:3] == ("gh", "api", "--paginate"):
            return Result(output=json.dumps([self.comments]))
        if values[0] == "reviewer-broker":
            self.broker_calls += 1
            request_path = Path(values[-1])
            request = json.loads(request_path.read_text(encoding="utf-8"))
            output = json.dumps(
                {
                    "request_key": request["request_key"],
                    "target_head_sha": request["head_sha"],
                    "verdict": self.broker_verdict,
                    "findings": ["修正が必要"] if self.broker_verdict == "REQUEST_CHANGES" else [],
                    "reviewer_identity": "independent-reviewer",
                }
            )
            if self.move_head_after_review:
                self.current_head = "c" * 40
            return Result(output=output)
        raise AssertionError(values)


def target() -> EvidenceTarget:
    return EvidenceTarget(
        repository="owner/sample",
        work_identity="work:owner/sample:1",
        issue_number=1,
        pr_number=7,
        head_sha="a" * 40,
        base_branch="main",
        canonical_design_identities=("design:1",),
        acceptance_digest="digest-1",
    )


def work() -> V2WorkObservation:
    return V2WorkObservation(
        work_identity="work:owner/sample:1",
        issue_number=1,
        issue_revision="issue-rev",
        issue_state="OPEN",
        lifecycle="RUNNING",
        project_status="In progress",
        priority="P1",
        dependency_states=(),
        acceptance_digest="digest-1",
        canonical_design_identities=("design:1",),
        active_lineage_identity="lineage:1",
        exact_head_sha="a" * 40,
    )


def ci_success(run_id: int = 10, head: str = "a" * 40) -> dict[str, object]:
    return {
        "id": run_id,
        "name": "CI",
        "head_sha": head,
        "status": "completed",
        "conclusion": "success",
    }


def review_components(runner: EvidenceRunner, database: FakeDatabase) -> ReviewCoordinator:
    store = PostgreSQLReviewEvidenceStore(database)
    broker = TrustedReviewerBrokerAdapter(runner, ("reviewer-broker",), {})
    reader = GitHubTargetReader(runner, {})
    return ReviewCoordinator(store, broker, reader, "policy-v1")


def test_ci_uses_only_exact_head() -> None:
    item = target()
    runner = EvidenceRunner(item)
    runner.ci_runs = [ci_success(11, "b" * 40), ci_success(10, item.head_sha)]

    evidence = GitHubExactHeadCIAdapter(runner, {}).read(item, "CI")

    assert evidence.state is EvidenceState.PASS
    assert evidence.identity == f"ci:10:{item.head_sha}"


def test_review_request_key_changes_with_head() -> None:
    item = target()
    first = review_request_key(item, "ci:1", "policy-v1")
    second = review_request_key(
        replace(item, head_sha="b" * 40),
        "ci:1",
        "policy-v1",
    )
    assert first != second


def test_same_exact_review_is_not_called_twice() -> None:
    item = target()
    runner = EvidenceRunner(item)
    database = FakeDatabase()
    coordinator = review_components(runner, database)
    ci = MachineEvidence(EvidenceState.PASS, "ci:10")

    first = coordinator.ensure_review(item, ci)
    second = coordinator.ensure_review(item, ci)

    assert first.state is EvidenceState.PASS
    assert second.state is EvidenceState.PASS
    assert runner.broker_calls == 1


def test_request_changes_returns_supervisor_to_repair() -> None:
    item = target()
    runner = EvidenceRunner(item)
    runner.broker_verdict = "REQUEST_CHANGES"
    coordinator = review_components(runner, FakeDatabase())
    ci = MachineEvidence(EvidenceState.PASS, "ci:10")
    review = coordinator.ensure_review(item, ci)
    observation = replace(
        work(),
        verification_state=EvidenceState.PASS,
        verification_identity="ci:10",
        review_state=review.state,
        review_identity=review.identity,
    )

    assert review.state is EvidenceState.REQUEST_CHANGES
    assert derive_transition(observation) is V2Transition.REPAIR


def test_review_result_is_stale_when_head_moves_during_call() -> None:
    item = target()
    runner = EvidenceRunner(item)
    runner.move_head_after_review = True
    coordinator = review_components(runner, FakeDatabase())

    result = coordinator.ensure_review(item, MachineEvidence(EvidenceState.PASS, "ci:10"))

    assert result.state is EvidenceState.NOT_RUN
    assert result.identity is not None and result.identity.startswith("stale:")


def test_human_verification_is_exact_head_bound() -> None:
    item = target()
    runner = EvidenceRunner(item)
    runner.comments = [
        {
            "body": "<!-- loop-engineering-human-verification:v1 -->\n"
            + json.dumps(
                {
                    "work_identity": item.work_identity,
                    "head_sha": "b" * 40,
                    "result": "PASS",
                }
            )
        },
        {
            "body": "<!-- loop-engineering-human-verification:v1 -->\n"
            + json.dumps(
                {
                    "work_identity": item.work_identity,
                    "head_sha": item.head_sha,
                    "result": "PASS",
                }
            )
        },
    ]

    evidence = GitHubHumanVerificationAdapter(runner, {}).read(item, True)

    assert evidence.state is EvidenceState.PASS
    assert evidence.identity is not None


def test_old_head_human_pass_is_not_current_pass() -> None:
    item = target()
    runner = EvidenceRunner(item)
    runner.comments = [
        {
            "body": "<!-- loop-engineering-human-verification:v1 -->\n"
            + json.dumps(
                {
                    "work_identity": item.work_identity,
                    "head_sha": "b" * 40,
                    "result": "PASS",
                }
            )
        }
    ]

    evidence = GitHubHumanVerificationAdapter(runner, {}).read(item, True)

    assert evidence.state is EvidenceState.PENDING


def test_apply_evidence_requires_same_head() -> None:
    item = target()
    bundle = EvidenceBundle(
        item,
        MachineEvidence(EvidenceState.PASS, "ci"),
        MachineEvidence(EvidenceState.PASS, "review"),
        MachineEvidence(EvidenceState.NOT_REQUIRED, None),
    )
    updated = apply_evidence(work(), bundle)

    assert updated.verification_state is EvidenceState.PASS
    assert updated.review_state is EvidenceState.PASS


def _quoted_values(sql: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(sql):
        if sql[index] != "'":
            index += 1
            continue
        index += 1
        current: list[str] = []
        while index < len(sql):
            if sql[index] == "'" and index + 1 < len(sql) and sql[index + 1] == "'":
                current.append("'")
                index += 2
                continue
            if sql[index] == "'":
                index += 1
                break
            current.append(sql[index])
            index += 1
        values.append("".join(current))
    return values
