import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from loop_engineering.config import ReviewLevelConfig
from loop_engineering.v2_external_review import (
    ExternalReviewCoordinator,
    ExternalReviewOutcome,
    ExternalReviewState,
    ExternalReviewStatus,
    ExternalReviewTarget,
    OpenAICompatibleExternalReviewer,
    review_request_key,
)
from loop_engineering.v2_implementer import ImplementerFinding


class MemoryStore:
    def __init__(self) -> None:
        self.state: ExternalReviewState | None = None
        self.saved: list[ExternalReviewState] = []

    def get(self, work_identity: str) -> ExternalReviewState | None:
        if self.state is not None:
            assert self.state.work_identity == work_identity
        return self.state

    def save(self, state: ExternalReviewState) -> None:
        self.state = state
        self.saved.append(state)


class SequenceReviewer:
    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = verdicts
        self.calls: list[tuple[int, int, str]] = []

    def review(
        self,
        target: ExternalReviewTarget,
        policy: ReviewLevelConfig,
        pass_index: int,
    ) -> ExternalReviewOutcome:
        verdict = self.verdicts[len(self.calls)]
        key = review_request_key(target, policy, pass_index)
        self.calls.append((policy.level, pass_index, key))
        findings = (blocking_finding(),) if verdict == "REQUEST_CHANGES" else ()
        return ExternalReviewOutcome(
            key,
            target.exact_head_sha,
            target.change_identity,
            verdict,
            findings,
            f"{policy.provider}:{policy.model}",
            (),
        )


def policy(
    level: int,
    *,
    passes: int = 1,
    required: bool = True,
    escalation: str = "BLOCK",
) -> ReviewLevelConfig:
    return ReviewLevelConfig(
        level=level,
        provider="openai",
        model=f"reviewer-{level}",
        api_base="https://api.example.test/v1",
        credential_env="REVIEW_KEY",
        required=required,
        timeout_seconds=30,
        context_policy="default",
        escalation_policy=escalation,
        passes_required=passes,
    )


def target(
    *,
    head: str = "a" * 40,
    change: str = "sha256:" + "b" * 64,
    local_pass: str = "local-pass:1",
) -> ExternalReviewTarget:
    return ExternalReviewTarget(
        repository_identity="owner/product",
        work_identity="work:owner/product:10",
        issue_number=10,
        pr_number=20,
        exact_head_sha=head,
        change_identity=change,
        active_lineage_identity="pr:20",
        canonical_design_identities=("design:1",),
        acceptance_digest="acceptance:1",
        scope_paths=("src", "tests"),
        local_pass_identity=local_pass,
    )


def blocking_finding() -> ImplementerFinding:
    return ImplementerFinding(
        "finding:1",
        "BLOCKING",
        "src/app.py",
        "L10",
        "logic bug",
        "design",
        "observed behavior",
        "acceptance failure",
        "fix logic",
    )


def test_one_level_one_pass_creates_external_pass() -> None:
    reviewer = SequenceReviewer(["PASS"])
    result = ExternalReviewCoordinator(MemoryStore(), reviewer).run(
        target(),
        (policy(1),),
    )

    assert result.status is ExternalReviewStatus.PASS
    assert result.external_pass_identity is not None
    assert [(level, index) for level, index, _key in reviewer.calls] == [(1, 1)]


def test_multiple_levels_and_fresh_passes_run_in_order() -> None:
    reviewer = SequenceReviewer(["PASS", "PASS", "PASS", "PASS"])
    result = ExternalReviewCoordinator(MemoryStore(), reviewer).run(
        target(),
        (policy(1, passes=2), policy(2, passes=2)),
    )

    assert result.status is ExternalReviewStatus.PASS
    assert [(level, index) for level, index, _key in reviewer.calls] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
    ]
    assert len({key for _level, _index, key in reviewer.calls}) == 4


def test_request_changes_returns_only_valid_blocking_findings() -> None:
    reviewer = SequenceReviewer(["REQUEST_CHANGES"])
    result = ExternalReviewCoordinator(MemoryStore(), reviewer).run(
        target(),
        (policy(1),),
    )

    assert result.status is ExternalReviewStatus.REQUEST_CHANGES
    assert result.approved_findings == (blocking_finding(),)
    assert result.level == 1
    assert result.pass_index == 1


def test_new_target_after_repair_restarts_from_level_one_pass_one() -> None:
    store = MemoryStore()
    first_reviewer = SequenceReviewer(["REQUEST_CHANGES"])
    first = ExternalReviewCoordinator(store, first_reviewer).run(
        target(),
        (policy(1), policy(2)),
    )
    assert first.status is ExternalReviewStatus.REQUEST_CHANGES

    second_reviewer = SequenceReviewer(["PASS", "PASS"])
    repaired = target(
        head="c" * 40,
        change="sha256:" + "d" * 64,
        local_pass="local-pass:2",
    )
    second = ExternalReviewCoordinator(store, second_reviewer).run(
        repaired,
        (policy(1), policy(2)),
    )

    assert second.status is ExternalReviewStatus.PASS
    assert [(level, index) for level, index, _key in second_reviewer.calls] == [
        (1, 1),
        (2, 1),
    ]


def test_optional_not_run_is_skipped_but_required_not_run_waits() -> None:
    optional_reviewer = SequenceReviewer(["NOT_RUN", "PASS"])
    optional = ExternalReviewCoordinator(MemoryStore(), optional_reviewer).run(
        target(),
        (policy(1, required=False), policy(2)),
    )
    assert optional.status is ExternalReviewStatus.PASS

    required_reviewer = SequenceReviewer(["NOT_RUN"])
    required = ExternalReviewCoordinator(MemoryStore(), required_reviewer).run(
        target(),
        (policy(1),),
    )
    assert required.status is ExternalReviewStatus.WAITING


def test_escalate_next_level_and_human_policy() -> None:
    next_reviewer = SequenceReviewer(["ESCALATE", "PASS"])
    next_result = ExternalReviewCoordinator(MemoryStore(), next_reviewer).run(
        target(),
        (policy(1, escalation="NEXT_LEVEL"), policy(2)),
    )
    assert next_result.status is ExternalReviewStatus.PASS
    assert [(level, index) for level, index, _key in next_reviewer.calls] == [
        (1, 1),
        (2, 1),
    ]

    human_reviewer = SequenceReviewer(["ESCALATE"])
    human = ExternalReviewCoordinator(MemoryStore(), human_reviewer).run(
        target(),
        (policy(1, escalation="HUMAN"),),
    )
    assert human.status is ExternalReviewStatus.ESCALATE


def test_same_completed_target_does_not_call_provider_again() -> None:
    store = MemoryStore()
    reviewer = SequenceReviewer(["PASS"])
    coordinator = ExternalReviewCoordinator(store, reviewer)

    first = coordinator.run(target(), (policy(1),))
    second = coordinator.run(target(), (policy(1),))

    assert first.status is ExternalReviewStatus.PASS
    assert second.status is ExternalReviewStatus.PASS
    assert len(reviewer.calls) == 1


def test_restart_resumes_same_level_and_pass() -> None:
    store = MemoryStore()
    first_reviewer = SequenceReviewer(["PASS", "NOT_RUN"])
    first = ExternalReviewCoordinator(store, first_reviewer).run(
        target(),
        (policy(1, passes=2),),
    )
    assert first.status is ExternalReviewStatus.WAITING
    assert store.state is not None
    assert store.state.level_index == 0
    assert store.state.pass_index == 2

    second_reviewer = SequenceReviewer(["PASS"])
    second = ExternalReviewCoordinator(store, second_reviewer).run(
        target(),
        (policy(1, passes=2),),
    )
    assert second.status is ExternalReviewStatus.PASS
    assert [(level, index) for level, index, _key in second_reviewer.calls] == [(1, 2)]


def test_review_request_key_changes_with_pass_and_target() -> None:
    item = target()
    config = policy(1, passes=2)

    first = review_request_key(item, config, 1)
    second = review_request_key(item, config, 2)
    changed = review_request_key(
        target(head="c" * 40, change="sha256:" + "d" * 64),
        config,
        1,
    )

    assert first != second
    assert first != changed


@dataclass(frozen=True)
class CommandResult:
    returncode: int = 0
    output: str = ""

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class ProviderRunner:
    def __init__(self, item: ExternalReviewTarget) -> None:
        self.item = item
        self.head = item.exact_head_sha
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 120,
        capture_output: bool = True,
    ) -> CommandResult:
        del environment, timeout_seconds, capture_output
        values = tuple(command)
        self.calls.append(values)
        if values[:3] == ("gh", "pr", "view"):
            return CommandResult(output=json.dumps({"headRefOid": self.head}))
        if values[:3] == ("gh", "pr", "diff"):
            return CommandResult(output="diff --git a/src/app.py b/src/app.py\n+fixed\n")
        raise AssertionError(values)


class FakeTransport:
    def __init__(self, item: ExternalReviewTarget) -> None:
        self.item = item
        self.headers: dict[str, str] | None = None
        self.payload: dict[str, object] | None = None
        self.calls = 0

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: int,
    ) -> object:
        del url, timeout_seconds
        self.calls += 1
        self.headers = dict(headers)
        self.payload = dict(payload)
        request_key = str(headers["Idempotency-Key"])
        content = {
            "schema_version": 1,
            "request_key": request_key,
            "target_head_sha": self.item.exact_head_sha,
            "target_change_identity": self.item.change_identity,
            "verdict": "PASS",
            "findings": [],
            "diagnostics": [],
        }
        return {"choices": [{"message": {"content": json.dumps(content)}}]}


def test_openai_compatible_adapter_keeps_secret_out_of_payload() -> None:
    item = target()
    runner = ProviderRunner(item)
    transport = FakeTransport(item)
    reviewer = OpenAICompatibleExternalReviewer(
        runner,
        {"REVIEW_KEY": "secret-review-key"},
        transport,
    )

    result = reviewer.review(item, policy(1), 1)

    assert result.verdict == "PASS"
    assert transport.calls == 1
    assert transport.headers is not None
    assert transport.headers["Authorization"] == "Bearer secret-review-key"
    assert transport.payload is not None
    assert "secret-review-key" not in json.dumps(transport.payload)


def test_openai_compatible_adapter_missing_credential_is_not_run() -> None:
    item = target()
    transport = FakeTransport(item)
    reviewer = OpenAICompatibleExternalReviewer(
        ProviderRunner(item),
        {},
        transport,
    )

    result = reviewer.review(item, policy(1), 1)

    assert result.verdict == "NOT_RUN"
    assert result.diagnostics == ("EXTERNAL_REVIEW_CREDENTIAL_MISSING",)
    assert transport.calls == 0
