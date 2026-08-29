"""Loop Engineeringの対象Repository・Project・Mission設定。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LoopEngineConfig:
    """Product固有identityをCoreから分離する設定契約。"""

    repository: str
    owner: str
    project_number: int
    mission_issue: int
    label: str = "loop-engineering"
    trunk_branch: str = "main"
    authority_refs: tuple[str, ...] = ()
    improvement_area: str = "Runtime / Infrastructure"
    issue_level: str = "Work"
    root_issue: int | None = None
    parent_issue: int | None = None
    integration_work: int | None = None
    ci_workflow_name: str = "Deterministic CI"

    def __post_init__(self) -> None:
        text_fields = (
            ("repository", self.repository),
            ("owner", self.owner),
            ("label", self.label),
            ("trunk_branch", self.trunk_branch),
            ("improvement_area", self.improvement_area),
            ("issue_level", self.issue_level),
            ("ci_workflow_name", self.ci_workflow_name),
        )
        for name, value in text_fields:
            if not value.strip():
                raise ValueError(f"{name}を空文字にはできません")
        if "/" not in self.repository:
            raise ValueError("repositoryはowner/name形式で指定してください")
        if self.project_number < 1:
            raise ValueError("project_numberは1以上である必要があります")
        if self.mission_issue < 1:
            raise ValueError("mission_issueは1以上である必要があります")
        for name, value in (
            ("root_issue", self.root_issue),
            ("parent_issue", self.parent_issue),
            ("integration_work", self.integration_work),
        ):
            if value is not None and value < 1:
                raise ValueError(f"{name}は1以上である必要があります")
        if any(not item.strip() for item in self.authority_refs):
            raise ValueError("authority_refsに空文字は指定できません")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "LoopEngineConfig":
        """秘密情報を含まない実行設定を環境変数から構築する。"""

        values = environment if environment is not None else os.environ
        repository = values.get("LOOP_REPOSITORY", "ktan514/loop-engineering")
        owner = values.get("LOOP_PROJECT_OWNER") or repository.split("/", 1)[0]
        project_number = _required_int(values, "LOOP_PROJECT_NUMBER")
        mission_issue = _required_int(values, "LOOP_MISSION_ISSUE")
        return cls(
            repository=repository,
            owner=owner,
            project_number=project_number,
            mission_issue=mission_issue,
            label=values.get("LOOP_LABEL", "loop-engineering"),
            trunk_branch=values.get("LOOP_TRUNK_BRANCH", "main"),
            authority_refs=_csv(values.get("LOOP_AUTHORITY_REFS", "")),
            improvement_area=values.get("LOOP_IMPROVEMENT_AREA", "Runtime / Infrastructure"),
            issue_level=values.get("LOOP_ISSUE_LEVEL", "Work"),
            root_issue=_optional_int(values, "LOOP_ROOT_ISSUE"),
            parent_issue=_optional_int(values, "LOOP_PARENT_ISSUE"),
            integration_work=_optional_int(values, "LOOP_INTEGRATION_WORK"),
            ci_workflow_name=values.get("LOOP_CI_WORKFLOW_NAME", "Deterministic CI"),
        )


def _required_int(values: Mapping[str, str], name: str) -> int:
    raw = values.get(name)
    if raw is None:
        raise ValueError(f"{name}が設定されていません")
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name}は整数で指定してください") from error
    if value < 1:
        raise ValueError(f"{name}は1以上で指定してください")
    return value


def _optional_int(values: Mapping[str, str], name: str) -> int | None:
    raw = values.get(name)
    if raw in {None, ""}:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name}は整数で指定してください") from error
    if value < 1:
        raise ValueError(f"{name}は1以上で指定してください")
    return value


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())
