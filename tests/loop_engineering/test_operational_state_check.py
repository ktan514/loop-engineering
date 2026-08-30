from __future__ import annotations

from loop_engineering.operational_state_check import check_operational_state_round_trip


class FakeDatabase:
    def __init__(self, *, fail_write: bool = False) -> None:
        self.fail_write = fail_write
        self.rows: dict[str, dict[str, object]] = {}

    def execute_sql(self, sql: str) -> bool:
        if self.fail_write:
            return False
        if "INSERT INTO loop_runs" in sql:
            identity = _between(sql, "VALUES ('", "', '")
            values = sql.split("VALUES (", 1)[1].rsplit(")", 1)[0]
            parts = [part.strip().strip("'") for part in values.split(",")]
            self.rows[identity] = {
                "identity": parts[0],
                "project_key": parts[1],
                "repository": parts[2],
                "status": parts[3],
            }
        elif "UPDATE loop_runs SET status = 'COMPLETED'" in sql:
            identity = _between(sql, "WHERE identity = '", "'")
            self.rows[identity]["status"] = "COMPLETED"
        return True

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None:
        identity = _between(select_sql, "WHERE identity = '", "'")
        row = self.rows.get(identity)
        return [dict(row)] if row is not None else []


def _between(value: str, start: str, end: str) -> str:
    return value.split(start, 1)[1].split(end, 1)[0]


def test_round_trip_writes_reads_and_finalizes_without_product_effect() -> None:
    database = FakeDatabase()

    result = check_operational_state_round_trip(
        database,
        project_key="ai-liver-yura",
        repository="ktan514/ai-liver-yura",
    )

    assert result.succeeded
    assert result.detail == "ROUND_TRIP_PASS"
    assert len(database.rows) == 1
    row = next(iter(database.rows.values()))
    assert row["project_key"] == "health:ai-liver-yura"
    assert row["repository"] == "ktan514/ai-liver-yura"
    assert row["status"] == "COMPLETED"


def test_round_trip_write_failure_is_typed() -> None:
    database = FakeDatabase(fail_write=True)

    result = check_operational_state_round_trip(
        database,
        project_key="ai-liver-yura",
        repository="ktan514/ai-liver-yura",
    )

    assert not result.succeeded
    assert result.detail == "WRITE_FAILED"
