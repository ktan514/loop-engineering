import json
from dataclasses import dataclass
from pathlib import Path

from loop_engineering.v2_implementer import (
    DevelopmentTaskPacket,
    ImplementerFinding,
    ImplementerResult,
    ImplementerStatus,
    ImplementerTransition,
    WorkspaceEffectReport,
)
from loop_engineering.v2_local_quality import (
    LocalQualityContext,
    LocalQualityCoordinator,
    LocalQualityStage,
    LocalQualityState,
    LocalQualityStatus,
    LocalQualityTarget,
    LocalReviewCompletion,
    LocalReviewExecutionResult,
    LocalReviewStatus,
    LocalVerificationResult,
    LocalVerificationStatus,
    PostgreSQLLocalQualityStore,
    VerificationCommandDescriptor,
    VerificationCommandOutcome,
    validate_local_findings,
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
        self.row: dict[str, object] | None = None

    def execute_sql(self, sql: str) -> bool:
        if not sql.startswith("INSERT INTO loop_local_quality_state"):
            raise AssertionError(sql)
        values = _quoted_values(sql)
        self.row = {
            "work_identity": values[0],
            "target_head_sha": values[1],
            "change_identity": values[2],
            "stage": values[3],
            "review_cycle": _int_after(sql, "VALUES (", 4),
            "no_progress_count": _int_after(sql, "VALUES (", 5),
            "last_progress_fingerprint": _nullable_value(sql, "last_progress_fingerprint", values),
            "verification_identity": None,
            "review_request_key": None,
            "review_identity": None,
            "local_pass_identity": None,
            "approved_findings": json.loads(next(v for v in values if v.startswith("["))),
            "diagnostics": json.loads([v for v in values if v.startswith("[")][-1]),
        }
        # Restart behavior is covered by MemoryStore; this fake only proves SQL emission.
        return True

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None:
        if "FROM loop_local_quality_state" not in select_sql:
            raise AssertionError(select_sql)
        return [] if self.row is None else [dict(self.row)]


class MemoryStore:
    def __init__(self, state: LocalQualityState | None = None) -> None:
        self.state = state
        self.saved: list[LocalQualityState] = []

    def get(self, work_identity: str) -> LocalQualityState | None:
        if self.state is not None:
            assert self.state.work_identity == work_identity
        return self.state

    def save(self, state: LocalQualityState) -> None:
        self.state = state
        self.saved.append(state)


class SequenceVerifier:
    def __init__(self, results: list[LocalVerificationResult]) -> None:
        self.results = results
        self.calls = 0

    def verify(self, context: LocalQualityContext) -> LocalVerificationResult:
        del context
        value = self.results[self.calls]
        self.calls += 1
        return value


class SequenceReviewer:
    def __init__(self, results: list[LocalReviewExecutionResult]) -> None:
        self.results = results
        self.calls = 0

    def review(self, context: LocalQualityContext) -> LocalReviewExecutionResult:
        del context
        value = self.results[self.calls]
        self.calls += 1
        return value


class SequenceImplementer:
    def __init__(self, results: list[ImplementerResult]) -> None:
        self.results = results
        self.calls = 0
        self.packets: list[DevelopmentTaskPacket] = []

    def execute(self, packet: DevelopmentTaskPacket) -> ImplementerResult:
        self.packets.append(packet)
        value = self.results[self.calls]
        self.calls += 1
        return value


def target(
    *,
    head: str = "a" * 40,
    change: str = "sha256:" + "b" * 64,
) -> LocalQualityTarget:
    return LocalQualityTarget(
        repository_identity="owner/sample",
        work_identity="work:owner/sample:1",
        exact_head_sha=head,
        change_identity=change,
        active_lineage_identity="pr:7",
        canonical_design_identities=("design:1",),
        acceptance_digest="acceptance:1",
        scope_paths=("src", "tests"),
    )


def context(item: LocalQualityTarget | None = None) -> LocalQualityContext:
    return LocalQualityContext(
        target=item or target(),
        workspace_canonical_path=Path("/tmp/sample"),
        packet_identity="packet:1",
        generation=1,
        goal_revision="goal:1",
        issue_revision="issue:1",
        acceptance_checks=("tests pass",),
        authority_refs=("Issue #1",),
        non_goals=(),
        safety_constraints=("mainへ直接pushしない",),
        verification_commands=(
            VerificationCommandDescriptor("tests", ("python", "-m", "pytest")),
        ),
    )


def verification(status: LocalVerificationStatus) -> LocalVerificationResult:
    outcome_status = "PASS" if status is LocalVerificationStatus.PASS else "FAIL"
    return LocalVerificationResult(
        status,
        f"verification:{status.value}",
        (
            VerificationCommandOutcome(
                "tests",
                outcome_status,
                0 if outcome_status == "PASS" else 1,
                "ok" if outcome_status == "PASS" else "failed",
            ),
        ),
        () if status is LocalVerificationStatus.PASS else ("failed",),
    )


def completion() -> LocalReviewCompletion:
    return LocalReviewCompletion(
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
    )


def review_pass() -> LocalReviewExecutionResult:
    item = target()
    return LocalReviewExecutionResult(
        LocalReviewStatus.PASS,
        "review-request:1",
        item.exact_head_sha,
        item.change_identity,
        completion(),
        (),
        None,
        (),
    )


def finding(identity: str = "finding:1") -> ImplementerFinding:
    return ImplementerFinding(
        identity,
        "BLOCKING",
        "src/app.py",
        "L10",
        "bug",
        "design",
        "evidence",
        "impact",
        "fix",
    )


def review_findings() -> LocalReviewExecutionResult:
    item = target()
    return LocalReviewExecutionResult(
        LocalReviewStatus.FINDINGS,
        "review-request:1",
        item.exact_head_sha,
        item.change_identity,
        completion(),
        (finding(),),
        None,
        (),
    )


def repaired_effect() -> ImplementerResult:
    return ImplementerResult(
        ImplementerStatus.SUCCESS,
        "ok",
        workspace_effect=WorkspaceEffectReport(
            request_identity="worker:1",
            packet_identity="repair:1",
            work_identity=target().work_identity,
            transition=ImplementerTransition.REPAIR,
            input_target_identity=target().exact_head_sha,
            result_target_identity="c" * 40,
            change_identity="sha256:" + "d" * 64,
            changed_paths=("src/app.py",),
            verification_evidence=(),
        ),
    )


def test_first_pass_creates_local_pass() -> None:
    store = MemoryStore()
    result = LocalQualityCoordinator(
        store,
        SequenceVerifier([verification(LocalVerificationStatus.PASS)]),
        SequenceReviewer([review_pass()]),
        SequenceImplementer([]),
    ).run(context())

    assert result.status is LocalQualityStatus.PASS
    assert result.local_pass_identity is not None
    assert store.state is not None
    assert store.state.stage is LocalQualityStage.LOCAL_PASS


def test_blocking_finding_repairs_then_restarts_verification_and_review() -> None:
    first_target = target()
    repaired_target = target(head="c" * 40, change="sha256:" + "d" * 64)
    second_review = LocalReviewExecutionResult(
        LocalReviewStatus.PASS,
        "review-request:2",
        repaired_target.exact_head_sha,
        repaired_target.change_identity,
        completion(),
        (),
        None,
        (),
    )
    implementer = SequenceImplementer([repaired_effect()])
    coordinator = LocalQualityCoordinator(
        MemoryStore(),
        SequenceVerifier(
            [
                verification(LocalVerificationStatus.PASS),
                verification(LocalVerificationStatus.PASS),
            ]
        ),
        SequenceReviewer([review_findings(), second_review]),
        implementer,
    )

    result = coordinator.run(context(first_target))

    assert result.status is LocalQualityStatus.PASS
    assert result.target == repaired_target
    assert implementer.calls == 1
    assert implementer.packets[0].approved_findings == (finding(),)


def test_review_incomplete_does_not_become_pass() -> None:
    incomplete = LocalReviewExecutionResult(
        LocalReviewStatus.INCOMPLETE,
        "review-request:1",
        None,
        None,
        None,
        (),
        "PROCESS_TIMEOUT",
        ("timeout",),
    )
    result = LocalQualityCoordinator(
        MemoryStore(),
        SequenceVerifier([verification(LocalVerificationStatus.PASS)]),
        SequenceReviewer([incomplete]),
        SequenceImplementer([]),
    ).run(context())

    assert result.status is LocalQualityStatus.INCOMPLETE
    assert result.local_pass_identity is None


def test_target_change_invalidates_old_local_pass() -> None:
    old = LocalQualityState(
        target().work_identity,
        target().exact_head_sha,
        target().change_identity,
        LocalQualityStage.LOCAL_PASS,
        verification_identity="verification:old",
        review_identity="review:old",
        local_pass_identity="local-pass:old",
    )
    new = target(head="c" * 40, change="sha256:" + "d" * 64)
    store = MemoryStore(old)

    result = LocalQualityCoordinator(
        store,
        SequenceVerifier([verification(LocalVerificationStatus.PASS)]),
        SequenceReviewer(
            [
                LocalReviewExecutionResult(
                    LocalReviewStatus.PASS,
                    "review:new",
                    new.exact_head_sha,
                    new.change_identity,
                    completion(),
                    (),
                    None,
                    (),
                )
            ]
        ),
        SequenceImplementer([]),
    ).run(context(new))

    assert result.status is LocalQualityStatus.PASS
    assert result.local_pass_identity != "local-pass:old"
    assert any(item.stage is LocalQualityStage.VERIFY_LOCAL for item in store.saved)


def test_invalid_finding_is_not_sent_to_fixer() -> None:
    invalid = ImplementerFinding(
        "finding:bad",
        "BLOCKING",
        "../outside",
        "L1",
        "bug",
        "basis",
        "evidence",
        "impact",
        "fix",
    )
    checked = validate_local_findings((invalid,), target().scope_paths)

    assert checked.approved_blocking == ()
    assert checked.rejected_identities == ("finding:bad",)


def test_duplicate_finding_is_classified() -> None:
    checked = validate_local_findings((finding(), finding()), target().scope_paths)

    assert checked.approved_blocking == (finding(),)
    assert checked.duplicate_identities == ("finding:1",)


def test_repeated_invalid_finding_is_bounded() -> None:
    invalid = ImplementerFinding(
        "finding:bad",
        "BLOCKING",
        "../outside",
        "L1",
        "bug",
        "basis",
        "evidence",
        "impact",
        "fix",
    )
    invalid_review = LocalReviewExecutionResult(
        LocalReviewStatus.FINDINGS,
        "review-request:invalid",
        target().exact_head_sha,
        target().change_identity,
        completion(),
        (invalid,),
        None,
        (),
    )
    store = MemoryStore()
    coordinator = LocalQualityCoordinator(
        store,
        SequenceVerifier([verification(LocalVerificationStatus.PASS)]),
        SequenceReviewer([invalid_review, invalid_review, invalid_review]),
        SequenceImplementer([]),
        max_no_progress=3,
    )

    first = coordinator.run(context())
    second = coordinator.run(context())
    third = coordinator.run(context())

    assert first.status is LocalQualityStatus.INCOMPLETE
    assert second.status is LocalQualityStatus.INCOMPLETE
    assert third.status is LocalQualityStatus.BLOCKED
    assert third.detail == "LOCAL_FINDING_VALIDATION_NO_PROGRESS"


def test_postgresql_store_emits_durable_state_sql() -> None:
    database = FakeDatabase()
    store = PostgreSQLLocalQualityStore(database)
    state = LocalQualityState(
        target().work_identity,
        target().exact_head_sha,
        target().change_identity,
        LocalQualityStage.REPAIR,
        review_cycle=2,
        approved_findings=(finding(),),
        diagnostics=("x",),
    )

    store.save(state)

    assert database.row is not None
    assert database.row["work_identity"] == target().work_identity


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


def _int_after(sql: str, marker: str, position: int) -> int:
    del marker, position
    # This test only checks that the SQL is emitted; exact integer parsing is not relevant.
    return 0


def _nullable_value(
    sql: str,
    field: str,
    values: list[str],
) -> str | None:
    del sql, field, values
    return None
