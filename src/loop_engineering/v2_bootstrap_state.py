"""Work Issue作成前のbootstrap外部effectをPostgreSQLへ安全に記録する。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


class BootstrapStateUnavailable(RuntimeError):
    """bootstrap状態を安全に読み書きできない。"""


class BootstrapStateDatabase(Protocol):
    def execute_sql(self, sql: str) -> bool: ...

    def query_json_rows(self, select_sql: str) -> list[dict[str, object]] | None: ...


@dataclass(frozen=True, slots=True)
class BootstrapEffect:
    idempotency_key: str
    product_key: str
    repository: str
    goal_revision: str
    kind: str
    target_identity: str
    status: str = "INTENT_RECORDED"
    request_identity: str | None = None
    expected_preconditions: tuple[tuple[str, str], ...] = ()
    expected_effect: tuple[tuple[str, str], ...] = ()


class PostgreSQLBootstrapStateStore:
    _STATUSES = frozenset({"INTENT_RECORDED", "CONFIRMED", "NO_EFFECT", "UNCERTAIN"})

    def __init__(self, database: BootstrapStateDatabase) -> None:
        self._database = database

    def ensure_intent(self, effect: BootstrapEffect) -> BootstrapEffect:
        _validate_effect(effect)
        existing = self.get(effect.idempotency_key)
        if existing is not None:
            if not _same_plan(existing, effect):
                raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_CONFLICT")
            return existing
        if not self._database.execute_sql(
            "INSERT INTO loop_bootstrap_effects "
            "(idempotency_key, product_key, repository, goal_revision, kind, target_identity, "
            "status, request_identity, expected_preconditions, expected_effect) VALUES ("
            f"{_literal(effect.idempotency_key)}, {_literal(effect.product_key)}, "
            f"{_literal(effect.repository)}, {_literal(effect.goal_revision)}, "
            f"{_literal(effect.kind)}, {_literal(effect.target_identity)}, 'INTENT_RECORDED', "
            f"{_nullable_literal(effect.request_identity)}, "
            f"{_pairs_json_literal(effect.expected_preconditions)}, "
            f"{_pairs_json_literal(effect.expected_effect)}) "
            "ON CONFLICT (idempotency_key) DO NOTHING"
        ):
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_INTENT_WRITE_FAILED")
        stored = self.get(effect.idempotency_key)
        if stored is None or not _same_plan(stored, effect):
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_INTENT_READBACK_FAILED")
        return stored

    def get(self, idempotency_key: str) -> BootstrapEffect | None:
        rows = self._database.query_json_rows(
            "SELECT idempotency_key, product_key, repository, goal_revision, kind, "
            "target_identity, status, request_identity, expected_preconditions, expected_effect "
            "FROM loop_bootstrap_effects "
            f"WHERE idempotency_key = {_literal(idempotency_key)} LIMIT 1"
        )
        if rows is None:
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_READ_FAILED")
        if not rows:
            return None
        return _effect_from_row(rows[0])

    def record_outcome(self, idempotency_key: str, status: str) -> BootstrapEffect:
        if status not in self._STATUSES or status == "INTENT_RECORDED":
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_OUTCOME_INVALID")
        current = self.get(idempotency_key)
        if current is None:
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_MISSING")
        if current.status == status:
            return current
        if current.status in {"CONFIRMED", "NO_EFFECT"}:
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_ALREADY_TERMINAL")
        if not self._database.execute_sql(
            "UPDATE loop_bootstrap_effects SET "
            f"status = {_literal(status)}, "
            "confirmed_at = CASE WHEN "
            f"{_literal(status)} = 'CONFIRMED' THEN now() ELSE confirmed_at END, "
            "updated_at = now() "
            f"WHERE idempotency_key = {_literal(idempotency_key)} "
            "AND status IN ('INTENT_RECORDED', 'UNCERTAIN')"
        ):
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_OUTCOME_WRITE_FAILED")
        stored = self.get(idempotency_key)
        if stored is None or stored.status != status:
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_OUTCOME_READBACK_FAILED")
        return stored

    def unresolved(
        self,
        *,
        repository: str,
        product_key: str,
        goal_revision: str,
    ) -> tuple[BootstrapEffect, ...]:
        rows = self._database.query_json_rows(
            "SELECT idempotency_key, product_key, repository, goal_revision, kind, "
            "target_identity, status, request_identity, expected_preconditions, expected_effect "
            "FROM loop_bootstrap_effects WHERE "
            f"repository = {_literal(repository)} AND product_key = {_literal(product_key)} "
            f"AND goal_revision = {_literal(goal_revision)} "
            "AND status IN ('INTENT_RECORDED', 'UNCERTAIN') "
            "ORDER BY recorded_at ASC, idempotency_key ASC"
        )
        if rows is None:
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_READ_FAILED")
        return tuple(_effect_from_row(row) for row in rows)


def _validate_effect(effect: BootstrapEffect) -> None:
    values = (
        effect.idempotency_key,
        effect.product_key,
        effect.repository,
        effect.goal_revision,
        effect.kind,
        effect.target_identity,
    )
    if any(not value or "\x00" in value for value in values):
        raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_INVALID")
    if effect.status != "INTENT_RECORDED":
        raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_INVALID")
    if not effect.expected_effect:
        raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_INVALID")


def _same_plan(left: BootstrapEffect, right: BootstrapEffect) -> bool:
    return (
        left.idempotency_key == right.idempotency_key
        and left.product_key == right.product_key
        and left.repository == right.repository
        and left.goal_revision == right.goal_revision
        and left.kind == right.kind
        and left.target_identity == right.target_identity
        and left.request_identity == right.request_identity
        and left.expected_preconditions == right.expected_preconditions
        and left.expected_effect == right.expected_effect
    )


def _effect_from_row(row: dict[str, object]) -> BootstrapEffect:
    status = _required_string(row, "status")
    if status not in PostgreSQLBootstrapStateStore._STATUSES:
        raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_ROW_INVALID")
    return BootstrapEffect(
        idempotency_key=_required_string(row, "idempotency_key"),
        product_key=_required_string(row, "product_key"),
        repository=_required_string(row, "repository"),
        goal_revision=_required_string(row, "goal_revision"),
        kind=_required_string(row, "kind"),
        target_identity=_required_string(row, "target_identity"),
        status=status,
        request_identity=_optional_string(row, "request_identity"),
        expected_preconditions=_pairs(row, "expected_preconditions"),
        expected_effect=_pairs(row, "expected_effect"),
    )


def _required_string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_ROW_INVALID")
    return value


def _optional_string(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_ROW_INVALID")
    return value


def _pairs(row: dict[str, object], key: str) -> tuple[tuple[str, str], ...]:
    value = row.get(key)
    if not isinstance(value, dict):
        raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_ROW_INVALID")
    result: list[tuple[str, str]] = []
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise BootstrapStateUnavailable("BOOTSTRAP_EFFECT_ROW_INVALID")
        result.append((raw_key, raw_value))
    return tuple(sorted(result))


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _nullable_literal(value: str | None) -> str:
    return "NULL" if value is None else _literal(value)


def _pairs_json_literal(values: tuple[tuple[str, str], ...]) -> str:
    payload = json.dumps(dict(values), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _literal(payload) + "::jsonb"
