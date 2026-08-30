"""Host遷移のOperational State永続化とrestart reconciliation。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from .host_runtime import HostTarget, HostTransitionResult, HostTransitionStatus
from .postgres_runtime import PostgreSQLCommandAdapter

_TERMINAL_RUN_STATUS = frozenset(
    {
        HostTransitionStatus.COMPLETED.value,
        HostTransitionStatus.YIELD_EXTERNAL.value,
        HostTransitionStatus.INTERVENTION_REQUIRED.value,
    }
)


class OperationalStateUnavailable(RuntimeError):
    """required Operational Storeを安全に読み書きできない。"""


@dataclass(frozen=True, slots=True)
class UnfinishedRun:
    run_identity: str
    checkpoint_revision: str | None
    work_issue: int | None
    pr_number: int | None
    head_sha: str | None
    transition_status: str | None


class OperationalStatePort(Protocol):
    def latest_unfinished(self, project_key: str, repository: str) -> UnfinishedRun | None: ...

    def begin_run(self, run_identity: str, project_key: str, repository: str) -> None: ...

    def finish_run(self, run_identity: str, status: str) -> None: ...

    def mark_reconciled(self, run_identity: str) -> None: ...

    def record_checkpoint(
        self,
        run_identity: str,
        project_key: str,
        target: HostTarget | None,
    ) -> None: ...

    def record_transition(
        self,
        run_identity: str,
        sequence_number: int,
        result: HostTransitionResult,
    ) -> None: ...

    def acquire_lease(self, project_key: str, run_identity: str) -> bool: ...

    def release_lease(self, project_key: str, run_identity: str) -> None: ...

    def resolve_open_states(
        self,
        project_key: str,
        repository: str,
        current_run_identity: str,
    ) -> None: ...

    def record_blocker(self, run_identity: str, result: HostTransitionResult) -> None: ...

    def record_external_wait(self, run_identity: str, result: HostTransitionResult) -> None: ...


class FreshTargetPort(Protocol):
    def current_target(self) -> HostTarget | None: ...


class TransitionController(Protocol):
    def run_once(self) -> HostTransitionResult: ...


class PostgreSQLRuntimeOperationalStore:
    """#44管理schemaへ上限付きのHost実行状態だけを保存する。"""

    def __init__(self, database: PostgreSQLCommandAdapter) -> None:
        self._database = database

    def latest_unfinished(self, project_key: str, repository: str) -> UnfinishedRun | None:
        project = _literal(project_key)
        repo = _literal(repository)
        rows = self._query(
            "SELECT r.identity AS run_identity, "
            "c.source_revision AS checkpoint_revision, c.work_issue, c.pr_number, c.head_sha, "
            "t.status AS transition_status "
            "FROM loop_runs AS r "
            "LEFT JOIN LATERAL ("
            "SELECT source_revision, work_issue, pr_number, head_sha "
            "FROM loop_checkpoints WHERE run_identity = r.identity "
            "ORDER BY recorded_at DESC LIMIT 1"
            ") AS c ON true "
            "LEFT JOIN LATERAL ("
            "SELECT status FROM loop_transitions WHERE run_identity = r.identity "
            "ORDER BY sequence_number DESC LIMIT 1"
            ") AS t ON true "
            f"WHERE r.project_key = {project} AND r.repository = {repo} AND r.status = 'RUNNING' "
            "ORDER BY r.started_at DESC LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        run_identity = _row_string(row, "run_identity")
        if run_identity is None:
            raise OperationalStateUnavailable("OPERATIONAL_RUN_ID_INVALID")
        return UnfinishedRun(
            run_identity=run_identity,
            checkpoint_revision=_row_string(row, "checkpoint_revision"),
            work_issue=_row_int(row, "work_issue"),
            pr_number=_row_int(row, "pr_number"),
            head_sha=_row_string(row, "head_sha"),
            transition_status=_row_string(row, "transition_status"),
        )

    def begin_run(self, run_identity: str, project_key: str, repository: str) -> None:
        self._execute(
            "INSERT INTO loop_runs (identity, project_key, repository, status) VALUES ("
            f"{_literal(run_identity)}, {_literal(project_key)}, "
            f"{_literal(repository)}, 'RUNNING') "
            "ON CONFLICT (identity) DO NOTHING"
        )

    def finish_run(self, run_identity: str, status: str) -> None:
        if status not in _TERMINAL_RUN_STATUS and status != "RECONCILED":
            raise OperationalStateUnavailable("OPERATIONAL_RUN_STATUS_INVALID")
        self._execute(
            "UPDATE loop_runs SET "
            f"status = {_literal(status)}, finished_at = now() "
            f"WHERE identity = {_literal(run_identity)}"
        )

    def mark_reconciled(self, run_identity: str) -> None:
        self.finish_run(run_identity, "RECONCILED")

    def record_checkpoint(
        self,
        run_identity: str,
        project_key: str,
        target: HostTarget | None,
    ) -> None:
        identity = f"checkpoint:{run_identity}"
        revision = str(target.checkpoint_comment_id) if target is not None else None
        work_issue = target.work_issue if target is not None else None
        pr_number = target.pr_number if target is not None else None
        head_sha = target.head_sha if target is not None else None
        self._execute(
            "INSERT INTO loop_checkpoints "
            "(identity, run_identity, project_key, work_issue, "
            "source_revision, pr_number, head_sha) "
            "VALUES ("
            f"{_literal(identity)}, {_literal(run_identity)}, {_literal(project_key)}, "
            f"{_nullable_int(work_issue)}, {_nullable_literal(revision)}, "
            f"{_nullable_int(pr_number)}, {_nullable_literal(head_sha)}) "
            "ON CONFLICT (identity) DO UPDATE SET "
            "work_issue = EXCLUDED.work_issue, source_revision = EXCLUDED.source_revision, "
            "pr_number = EXCLUDED.pr_number, head_sha = EXCLUDED.head_sha, recorded_at = now()"
        )

    def record_transition(
        self,
        run_identity: str,
        sequence_number: int,
        result: HostTransitionResult,
    ) -> None:
        if sequence_number < 1:
            raise OperationalStateUnavailable("OPERATIONAL_SEQUENCE_INVALID")
        identity = f"transition:{run_identity}:{sequence_number}"
        self._execute(
            "INSERT INTO loop_transitions "
            "(identity, run_identity, sequence_number, status, detail, "
            "work_issue, pr_number, head_sha) "
            "VALUES ("
            f"{_literal(identity)}, {_literal(run_identity)}, {sequence_number}, "
            f"{_literal(result.status.value)}, {_literal(result.detail)}, "
            f"{_nullable_int(result.work_issue)}, {_nullable_int(result.pr_number)}, "
            f"{_nullable_literal(result.head_sha)}) "
            "ON CONFLICT (identity) DO NOTHING"
        )

    def acquire_lease(self, project_key: str, run_identity: str) -> bool:
        identity = f"host-transition:{project_key}"
        self._execute(
            "INSERT INTO loop_leases "
            "(identity, scope, subject_identity, holder_identity, status) VALUES ("
            f"{_literal(identity)}, 'host-transition', {_literal(project_key)}, "
            f"{_literal(run_identity)}, 'ACTIVE') "
            "ON CONFLICT (identity) DO UPDATE SET "
            "holder_identity = EXCLUDED.holder_identity, status = 'ACTIVE', "
            "acquired_at = now(), released_at = NULL "
            "WHERE loop_leases.status <> 'ACTIVE'"
        )
        rows = self._query(
            "SELECT holder_identity, status FROM loop_leases "
            f"WHERE identity = {_literal(identity)} LIMIT 1"
        )
        if not rows:
            return False
        return (
            _row_string(rows[0], "holder_identity") == run_identity
            and _row_string(rows[0], "status") == "ACTIVE"
        )

    def release_lease(self, project_key: str, run_identity: str) -> None:
        identity = f"host-transition:{project_key}"
        self._execute(
            "UPDATE loop_leases SET status = 'RELEASED', released_at = now() "
            f"WHERE identity = {_literal(identity)} AND holder_identity = {_literal(run_identity)} "
            "AND status = 'ACTIVE'"
        )

    def resolve_open_states(
        self,
        project_key: str,
        repository: str,
        current_run_identity: str,
    ) -> None:
        project = _literal(project_key)
        repo = _literal(repository)
        current = _literal(current_run_identity)
        prior_runs = (
            "SELECT identity FROM loop_runs "
            f"WHERE project_key = {project} AND repository = {repo} "
            f"AND identity <> {current}"
        )
        self._execute(
            "UPDATE loop_blockers SET status = 'RESOLVED', resolved_at = now() "
            "WHERE status = 'OPEN' AND run_identity IN ("
            f"{prior_runs})"
        )
        self._execute(
            "UPDATE loop_external_waits SET status = 'RESOLVED', resolved_at = now() "
            "WHERE status = 'OPEN' AND run_identity IN ("
            f"{prior_runs})"
        )

    def record_blocker(self, run_identity: str, result: HostTransitionResult) -> None:
        target = _result_target_identity(result)
        identity = f"blocker:{run_identity}:1"
        self._execute(
            "INSERT INTO loop_blockers "
            "(identity, run_identity, scope, subject_identity, kind, reason_code, status) VALUES ("
            f"{_literal(identity)}, {_literal(run_identity)}, 'host-transition', "
            f"{_literal(target)}, 'INTERVENTION_REQUIRED', {_literal(result.detail)}, 'OPEN') "
            "ON CONFLICT (identity) DO UPDATE SET status = 'OPEN', resolved_at = NULL"
        )

    def record_external_wait(self, run_identity: str, result: HostTransitionResult) -> None:
        target = _result_target_identity(result)
        identity = f"external-wait:{run_identity}:1"
        self._execute(
            "INSERT INTO loop_external_waits "
            "(identity, run_identity, kind, target_identity, status) VALUES ("
            f"{_literal(identity)}, {_literal(run_identity)}, {_literal(result.detail)}, "
            f"{_literal(target)}, 'OPEN') "
            "ON CONFLICT (identity) DO UPDATE SET status = 'OPEN', resolved_at = NULL"
        )

    def _execute(self, sql: str) -> None:
        if not self._database.execute_sql(sql):
            raise OperationalStateUnavailable("OPERATIONAL_STORE_WRITE_FAILED")

    def _query(self, sql: str) -> list[dict[str, object]]:
        rows = self._database.query_json_rows(sql)
        if rows is None:
            raise OperationalStateUnavailable("OPERATIONAL_STORE_READ_FAILED")
        return rows


class DurableHostTransitionCoordinator:
    """fresh GitHub stateとdurable Operational Stateを調整して1遷移を実行する。"""

    def __init__(
        self,
        *,
        project_key: str,
        repository: str,
        mission: FreshTargetPort,
        controller: TransitionController,
        store: OperationalStatePort,
        required: bool,
    ) -> None:
        self._project_key = project_key
        self._repository = repository
        self._mission = mission
        self._controller = controller
        self._store = store
        self._required = required

    def run_once(self) -> HostTransitionResult:
        run_identity = uuid.uuid4().hex
        try:
            previous = self._store.latest_unfinished(self._project_key, self._repository)
        except OperationalStateUnavailable:
            if self._required:
                return HostTransitionResult(
                    HostTransitionStatus.INTERVENTION_REQUIRED,
                    "OPERATIONAL_STORE_UNAVAILABLE",
                )
            return self._controller.run_once()

        try:
            fresh = self._mission.current_target()
        except RuntimeError:
            return HostTransitionResult(
                HostTransitionStatus.INTERVENTION_REQUIRED,
                "GITHUB_OBSERVE_FAILED",
            )

        try:
            if previous is not None:
                reconciliation = self._reconcile_previous(previous, fresh)
                if reconciliation is not None:
                    return reconciliation

            self._store.begin_run(run_identity, self._project_key, self._repository)
            self._store.record_checkpoint(run_identity, self._project_key, fresh)
            if not self._store.acquire_lease(self._project_key, run_identity):
                result = HostTransitionResult(
                    HostTransitionStatus.INTERVENTION_REQUIRED,
                    "OPERATIONAL_LEASE_UNAVAILABLE",
                    fresh.work_issue if fresh else None,
                    fresh.pr_number if fresh else None,
                    fresh.head_sha if fresh else None,
                )
                self._store.record_transition(run_identity, 1, result)
                self._store.record_blocker(run_identity, result)
                self._store.finish_run(run_identity, result.status.value)
                return result
        except OperationalStateUnavailable:
            if self._required:
                return HostTransitionResult(
                    HostTransitionStatus.INTERVENTION_REQUIRED,
                    "OPERATIONAL_STORE_UNAVAILABLE",
                )
            return self._controller.run_once()

        result = self._controller.run_once()
        try:
            self._store.record_transition(run_identity, 1, result)
            self._store.resolve_open_states(
                self._project_key,
                self._repository,
                run_identity,
            )
            if result.status is HostTransitionStatus.INTERVENTION_REQUIRED:
                self._store.record_blocker(run_identity, result)
            elif result.status is HostTransitionStatus.YIELD_EXTERNAL:
                self._store.record_external_wait(run_identity, result)
            self._store.finish_run(run_identity, result.status.value)
            self._store.release_lease(self._project_key, run_identity)
        except OperationalStateUnavailable:
            if self._required:
                return HostTransitionResult(
                    HostTransitionStatus.INTERVENTION_REQUIRED,
                    "OPERATIONAL_STORE_COMMIT_UNCERTAIN",
                    result.work_issue,
                    result.pr_number,
                    result.head_sha,
                )
        return result

    def _reconcile_previous(
        self,
        previous: UnfinishedRun,
        fresh: HostTarget | None,
    ) -> HostTransitionResult | None:
        if previous.transition_status in _TERMINAL_RUN_STATUS:
            self._store.finish_run(previous.run_identity, previous.transition_status)
            self._store.release_lease(self._project_key, previous.run_identity)
            return None

        if _checkpoint_advanced(previous, fresh):
            self._store.mark_reconciled(previous.run_identity)
            self._store.release_lease(self._project_key, previous.run_identity)
            return None

        result = HostTransitionResult(
            HostTransitionStatus.INTERVENTION_REQUIRED,
            "OPERATIONAL_STATE_UNCERTAIN",
            fresh.work_issue if fresh else previous.work_issue,
            fresh.pr_number if fresh else previous.pr_number,
            fresh.head_sha if fresh else previous.head_sha,
        )
        self._store.record_blocker(previous.run_identity, result)
        return result


def _checkpoint_advanced(previous: UnfinishedRun, fresh: HostTarget | None) -> bool:
    if previous.checkpoint_revision is None:
        return False
    if fresh is None:
        return False
    return (
        str(fresh.checkpoint_comment_id) != previous.checkpoint_revision
        or fresh.work_issue != previous.work_issue
        or fresh.pr_number != previous.pr_number
        or fresh.head_sha != previous.head_sha
    )


def _result_target_identity(result: HostTransitionResult) -> str:
    return (
        f"work:{result.work_issue or '-'}|pr:{result.pr_number or '-'}|"
        f"head:{result.head_sha or '-'}"
    )


def _literal(value: str) -> str:
    if "\x00" in value or len(value) > 1024:
        raise OperationalStateUnavailable("OPERATIONAL_VALUE_INVALID")
    return "'" + value.replace("'", "''") + "'"


def _nullable_literal(value: str | None) -> str:
    return "NULL" if value is None else _literal(value)


def _nullable_int(value: int | None) -> str:
    if value is None:
        return "NULL"
    if value < 0:
        raise OperationalStateUnavailable("OPERATIONAL_VALUE_INVALID")
    return str(value)


def _row_string(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    return value if isinstance(value, str) else None


def _row_int(row: dict[str, object], key: str) -> int | None:
    value = row.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
