from loop_engineering.v2_bootstrap_state import (
    BootstrapEffect,
    BootstrapStateUnavailable,
    PostgreSQLBootstrapStateStore,
)


class ScriptedDatabase:
    def __init__(
        self,
        query_results: list[list[dict[str, object]] | None],
        execute_results: list[bool] | None = None,
    ) -> None:
        self.query_results = list(query_results)
        self.execute_results = list(execute_results or [True] * 10)
        self.executed_sql: list[str] = []

    def execute_sql(self, sql: str) -> bool:
        self.executed_sql.append(sql)
        return self.execute_results.pop(0)

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None:
        self.executed_sql.append(select_sql)
        return self.query_results.pop(0)


def row(*, status: str = "INTENT_RECORDED", target: str = "issue-marker:x") -> dict[str, object]:
    return {
        "idempotency_key": "key-1",
        "product_key": "sample",
        "repository": "owner/sample",
        "goal_revision": "rev-1",
        "kind": "ISSUE_CREATE",
        "target_identity": target,
        "status": status,
        "request_identity": None,
        "expected_preconditions": {"present": "false"},
        "expected_effect": {"marker": "x"},
    }


def effect() -> BootstrapEffect:
    return BootstrapEffect(
        idempotency_key="key-1",
        product_key="sample",
        repository="owner/sample",
        goal_revision="rev-1",
        kind="ISSUE_CREATE",
        target_identity="issue-marker:x",
        expected_preconditions=(("present", "false"),),
        expected_effect=(("marker", "x"),),
    )


def test_ensure_intent_writes_then_reads_back_same_plan() -> None:
    database = ScriptedDatabase([[], [row()]])
    store = PostgreSQLBootstrapStateStore(database)

    stored = store.ensure_intent(effect())

    assert stored.status == "INTENT_RECORDED"
    assert any("INSERT INTO loop_bootstrap_effects" in sql for sql in database.executed_sql)


def test_ensure_intent_rejects_same_key_with_different_plan() -> None:
    database = ScriptedDatabase([[row(target="issue-marker:other")]])
    store = PostgreSQLBootstrapStateStore(database)

    try:
        store.ensure_intent(effect())
    except BootstrapStateUnavailable as error:
        assert str(error) == "BOOTSTRAP_EFFECT_CONFLICT"
    else:
        raise AssertionError("conflicting bootstrap effect was accepted")


def test_record_outcome_confirms_intent_with_readback() -> None:
    database = ScriptedDatabase([[row()], [row(status="CONFIRMED")]])
    store = PostgreSQLBootstrapStateStore(database)

    stored = store.record_outcome("key-1", "CONFIRMED")

    assert stored.status == "CONFIRMED"
    assert any("confirmed_at" in sql for sql in database.executed_sql)
