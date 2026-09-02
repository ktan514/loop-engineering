import json
from pathlib import Path

from loop_engineering.v2_autonomous_runtime import (
    AutonomousDispatch,
    PostgreSQLAutonomousRuntimeStore,
    runtime_identity,
)
from loop_engineering.v2_goal_planning import (
    BootstrapResult,
    PlannedWork,
    ProductDevelopmentRegistration,
    ProjectedPlan,
    ProjectedWork,
    WorkPlanProposal,
    proposal_identity,
)


class FakeDatabase:
    def __init__(self) -> None:
        self.runtimes: dict[str, dict[str, object]] = {}
        self.dispatches: dict[str, dict[str, object]] = {}
        self.plans: dict[str, dict[str, object]] = {}

    def execute_sql(self, sql: str) -> bool:
        values = _quoted_values(sql)
        if sql.startswith("INSERT INTO loop_autonomous_runtimes"):
            identity, product, repository, revision = values[:4]
            self.runtimes.setdefault(
                identity,
                {
                    "runtime_identity": identity,
                    "product_key": product,
                    "repository": repository,
                    "goal_revision": revision,
                    "status": "ACTIVE",
                    "current_work_identity": None,
                    "last_schedule_key": None,
                    "last_progress_fingerprint": None,
                    "no_progress_count": 0,
                    "last_detail": "",
                },
            )
            return True
        if sql.startswith("UPDATE loop_autonomous_runtimes SET"):
            identity = values[-1]
            row = self.runtimes[identity]
            row["status"] = values[0]
            row["current_work_identity"] = (
                None if "current_work_identity = NULL" in sql else values[1]
            )
            offset = 1 if row["current_work_identity"] is None else 2
            row["last_schedule_key"] = (
                None if "last_schedule_key = NULL" in sql else values[offset]
            )
            if row["last_schedule_key"] is not None:
                offset += 1
            row["last_progress_fingerprint"] = (
                None if "last_progress_fingerprint = NULL" in sql else values[offset]
            )
            row["no_progress_count"] = int(
                sql.split("no_progress_count = ", 1)[1].split(",", 1)[0]
            )
            row["last_detail"] = values[-2]
            return True
        if sql.startswith("INSERT INTO loop_autonomous_dispatches"):
            key, runtime, work, transition, status, detail = values[:6]
            self.dispatches.setdefault(
                key,
                {
                    "schedule_key": key,
                    "runtime_identity": runtime,
                    "work_identity": work,
                    "transition": transition,
                    "status": status,
                    "detail": detail,
                },
            )
            return True
        if sql.startswith("UPDATE loop_autonomous_dispatches SET"):
            status, detail, key = values
            self.dispatches[key]["status"] = status
            self.dispatches[key]["detail"] = detail
            return True
        if sql.startswith("INSERT INTO loop_goal_plans"):
            runtime, proposal_identity_value, proposal_json, projection_json = values[:4]
            self.plans.setdefault(
                runtime,
                {
                    "proposal_identity": proposal_identity_value,
                    "proposal_json": json.loads(proposal_json),
                    "projection_json": json.loads(projection_json),
                },
            )
            return True
        raise AssertionError(sql)

    def query_json_rows(self, sql: str) -> list[dict[str, object]] | None:
        if "FROM loop_autonomous_runtimes" in sql:
            key = sql.split("runtime_identity = '", 1)[1].split("'", 1)[0]
            row = self.runtimes.get(key)
            return [] if row is None else [dict(row)]
        if "FROM loop_autonomous_dispatches" in sql and "schedule_key =" in sql:
            key = sql.split("schedule_key = '", 1)[1].split("'", 1)[0]
            row = self.dispatches.get(key)
            return [] if row is None else [dict(row)]
        if "FROM loop_autonomous_dispatches" in sql:
            runtime = sql.split("runtime_identity = '", 1)[1].split("'", 1)[0]
            return [
                {"schedule_key": key}
                for key, row in self.dispatches.items()
                if row["runtime_identity"] == runtime
                and row["status"] in {"DISPATCHED", "COMPLETED", "WAITING"}
            ]
        if "FROM loop_goal_plans" in sql:
            runtime = sql.split("runtime_identity = '", 1)[1].split("'", 1)[0]
            row = self.plans.get(runtime)
            return [] if row is None else [dict(row)]
        raise AssertionError(sql)


def registration() -> ProductDevelopmentRegistration:
    return ProductDevelopmentRegistration(
        product_key="sample",
        workspace_canonical_path=Path("/tmp/sample"),
        repository_identity="owner/sample",
        project_owner="owner",
        project_number=10,
        trunk_branch="main",
        goal_definition_identity="goal:sample",
        goal_revision="rev-1",
        goal_text="sample",
        acceptance_criteria=("done",),
        work_branch_template="feature/work-{issue}",
        ci_workflow_name="CI",
        initial_project_status="Backlog",
    )


def bootstrap() -> BootstrapResult:
    reg = registration()
    work = PlannedWork(
        "first",
        "First",
        "purpose",
        ("done",),
        canonical_design_targets=("docs/design.md",),
    )
    proposal = WorkPlanProposal(
        proposal_identity(reg, (work,)),
        reg.goal_revision,
        (work,),
        ("done",),
    )
    projection = ProjectedPlan(
        1,
        "https://example/goal",
        "ITEM-GOAL",
        (
            ProjectedWork(
                "first",
                2,
                "https://example/work",
                "ITEM-WORK",
                "digest",
                (),
            ),
        ),
    )
    return BootstrapResult(proposal, projection)


def test_runtime_and_plan_survive_store_reconstruction() -> None:
    database = FakeDatabase()
    first = PostgreSQLAutonomousRuntimeStore(database)
    state = first.ensure_runtime(registration())
    first.save_plan(state.runtime_identity, bootstrap())
    first.dispatch(
        AutonomousDispatch(
            "schedule:1",
            state.runtime_identity,
            "work:owner/sample:2",
            "DESIGN",
            "DISPATCHED",
            "test",
        )
    )

    restarted = PostgreSQLAutonomousRuntimeStore(database)

    assert restarted.get(state.runtime_identity) == state
    assert restarted.plan(state.runtime_identity) == bootstrap()
    assert restarted.dispatched_schedule_keys(state.runtime_identity) == frozenset({"schedule:1"})


def test_runtime_identity_is_goal_revision_bound() -> None:
    reg = registration()
    assert runtime_identity(reg).startswith("runtime:")
    changed = ProductDevelopmentRegistration(
        product_key=reg.product_key,
        workspace_canonical_path=reg.workspace_canonical_path,
        repository_identity=reg.repository_identity,
        project_owner=reg.project_owner,
        project_number=reg.project_number,
        trunk_branch=reg.trunk_branch,
        goal_definition_identity=reg.goal_definition_identity,
        goal_revision="rev-2",
        goal_text=reg.goal_text,
        acceptance_criteria=reg.acceptance_criteria,
        work_branch_template=reg.work_branch_template,
        ci_workflow_name=reg.ci_workflow_name,
        initial_project_status=reg.initial_project_status,
    )
    assert runtime_identity(reg) != runtime_identity(changed)


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
