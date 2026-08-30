"""信頼済みホストからLoop Engineering改善WorkをGitHubへ公開する。"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import LoopEngineConfig, SelfImprovementConfig
from .health import marker, render_issue_body
from .models import (
    ConflictKind,
    ImprovementCandidate,
    ImprovementIssueIntent,
    ImprovementPublishResult,
    WriteIntent,
)
from .write_gate import validate


class CommandRunner(Protocol):
    def run(self, args: Sequence[str]) -> str:
        """信頼済みの固定形コマンドを実行し、標準出力を返す。"""


@dataclass(slots=True)
class SubprocessCommandRunner:
    timeout_seconds: float = 30.0

    def run(self, args: Sequence[str]) -> str:
        completed = subprocess.run(
            tuple(args),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=self.timeout_seconds,
        )
        return completed.stdout


@dataclass(slots=True)
class GitHubImprovementIssuePublisher:
    config: LoopEngineConfig
    runner: CommandRunner

    def publish(self, intent: ImprovementIssueIntent) -> ImprovementPublishResult:
        sink = self.config.self_improvement
        if not sink.enabled:
            raise ValueError("自己改善公開先が無効です")
        self._validate_intent(intent)
        with _improvement_lock(intent.candidate.improvement_key):
            existing = self._find_open_issue(intent.candidate.improvement_key)
            if existing is None:
                created_issue_url = self.runner.run(
                    (
                        "gh",
                        "issue",
                        "create",
                        "--repo",
                        sink.repository or "",
                        "--title",
                        intent.candidate.title,
                        "--body",
                        render_issue_body(intent.candidate, self.config),
                        "--label",
                        sink.label or "",
                    )
                )
                issue_number = _issue_number(created_issue_url.strip())
                issue_url = self._web_issue_url(issue_number)
                created = True
            else:
                issue_number, issue_url = existing
                created = False

            self._ensure_project_configuration(issue_url, intent)
            return ImprovementPublishResult(issue_number, issue_url, created, True)

    def _find_open_issue(self, improvement_key: str) -> tuple[int, str] | None:
        raw = self.runner.run(
            (
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{self._sink().repository or ''}/issues?state=open&labels="
                f"{self._sink().label or ''}&per_page=100",
            )
        )
        for item in _object_pages(raw):
            body = _optional_string(item.get("body")) or ""
            if marker(improvement_key) not in body:
                continue
            number = _integer(item.get("number"), "issue.number")
            return number, self._web_issue_url(number)
        return None

    def _ensure_project_configuration(
        self,
        issue_url: str,
        intent: ImprovementIssueIntent,
    ) -> None:
        project = self._project()
        project_id = _string(project.get("id"), "project.id")
        item_id = self._project_item_id(issue_url)
        if item_id is None:
            self._require_item_add_gate(project_id, issue_url)
            added = _object(
                self.runner.run(
                    (
                        "gh",
                        "project",
                        "item-add",
                        str(self._sink().project_number or 0),
                        "--owner",
                        self._sink().owner or "",
                        "--url",
                        issue_url,
                        "--format",
                        "json",
                    )
                )
            )
            item_id = _string(added.get("id"), "project_item.id")
            self._require_write_gate(
                WriteIntent(
                    "improvement-project-item-add-effect",
                    "project",
                    str(self._sink().project_number or 0),
                    "verify_effect",
                    (),
                    (("item_id", item_id),),
                    "publisher-live-readback",
                ),
                {},
                {"item_id": self._project_item_id(issue_url) or ""},
            )

        fields = self._fields()
        expected_preconditions = self._configuration_preconditions(
            project_id, item_id, fields, intent
        )
        for field_name, value in self._expected_values(intent).items():
            self._edit_field_with_fresh_gate(
                issue_url,
                intent,
                expected_preconditions,
                field_name,
                value,
            )

        readback = self._project_field_values(issue_url)
        self._require_write_gate(
            WriteIntent(
                "improvement-project-effect",
                "project",
                str(self._sink().project_number or 0),
                "verify_effect",
                (),
                tuple(
                    (f"value:{name}", value)
                    for name, value in self._expected_values(intent).items()
                ),
                "publisher-live-readback",
            ),
            {},
            {f"value:{name}": value for name, value in readback.items()},
        )

    def _fresh_configuration_snapshot(
        self,
        issue_url: str,
        intent: ImprovementIssueIntent,
    ) -> tuple[str, str, dict[str, dict[str, object]], dict[str, str]]:
        project = self._project()
        item_id = self._project_item_id(issue_url)
        if item_id is None:
            raise ValueError("Project項目が変更前に消失しました")
        project_id = _string(project.get("id"), "project.id")
        fields = self._fields()
        return (
            project_id,
            item_id,
            fields,
            self._configuration_preconditions(project_id, item_id, fields, intent),
        )

    def _edit_field_with_fresh_gate(
        self,
        issue_url: str,
        intent: ImprovementIssueIntent,
        expected_preconditions: dict[str, str],
        field_name: str,
        value: str,
    ) -> None:
        project_id, item_id, fields, fresh_preconditions = (
            self._fresh_configuration_snapshot(issue_url, intent)
        )
        self._require_write_gate(
            WriteIntent(
                f"improvement-project-edit-{field_name}",
                "project",
                str(self._sink().project_number or 0),
                "edit_improvement_field",
                tuple(expected_preconditions.items()),
                (),
                "publisher-live-readback",
            ),
            fresh_preconditions,
        )
        if field_name in {"Start date", "Target date"}:
            self._edit_date(project_id, item_id, fields, field_name, value)
        else:
            self._edit_single_select(project_id, item_id, fields, field_name, value)
        self._require_write_gate(
            WriteIntent(
                f"improvement-project-edit-{field_name}-effect",
                "project",
                str(self._sink().project_number),
                "verify_effect",
                (),
                ((f"value:{field_name}", value),),
                "publisher-live-readback",
            ),
            {},
            {
                f"value:{name}": observed
                for name, observed in self._project_field_values(issue_url).items()
            },
        )

    def _require_item_add_gate(self, project_id: str, issue_url: str) -> None:
        fresh_project = self._project()
        fresh_item_id = self._project_item_id(issue_url)
        self._require_write_gate(
            WriteIntent(
                "improvement-project-item-add",
                "project",
                str(self._sink().project_number),
                "add_improvement_item",
                (("project_id", project_id), ("item_presence", "absent")),
                (),
                "publisher-live-readback",
            ),
            {
                "project_id": _string(fresh_project.get("id"), "project.id"),
                "item_presence": "present" if fresh_item_id is not None else "absent",
            },
        )

    def _configuration_preconditions(
        self,
        project_id: str,
        item_id: str,
        fields: dict[str, dict[str, object]],
        intent: ImprovementIssueIntent,
    ) -> dict[str, str]:
        values = self._expected_values(intent)
        result = {"project_id": project_id, "item_id": item_id}
        for field_name, option_name in values.items():
            field = _field(fields, field_name)
            result[f"field:{field_name}"] = _string(
                field.get("id"), f"{field_name}.id"
            )
            if field_name in {"Start date", "Target date"}:
                continue
            options = field.get("options")
            if not isinstance(options, list):
                raise ValueError(f"{field_name}.optionsが見つかりません")
            option = next(
                (
                    item
                    for item in options
                    if isinstance(item, dict) and item.get("name") == option_name
                ),
                None,
            )
            if option is None:
                raise ValueError(f"{field_name}の選択肢を利用できません: {option_name}")
            result[f"option:{field_name}"] = _string(
                option.get("id"), f"{field_name}.option.id"
            )
        return result

    def _require_write_gate(
        self,
        intent: WriteIntent,
        fresh_preconditions: dict[str, str],
        readback_effect: dict[str, str] | None = None,
    ) -> None:
        result = validate(
            intent,
            fresh_preconditions,
            readback_effect,
            config=self.config,
        )
        if not result.allowed:
            conflict = result.conflict or ConflictKind.STALE_WRITE_GATE
            raise ValueError(conflict.value)

    def _project(self) -> dict[str, object]:
        return _object(
            self.runner.run(
                (
                    "gh",
                    "project",
                    "view",
                    str(self._sink().project_number or 0),
                    "--owner",
                    self._sink().owner or "",
                    "--format",
                    "json",
                )
            )
        )

    def _project_item_id(self, issue_url: str) -> str | None:
        snapshot = self._project_item(issue_url)
        return _optional_string(snapshot.get("id")) if snapshot is not None else None

    def _project_item(self, issue_url: str) -> dict[str, object] | None:
        payload = _object(
            self.runner.run(
                (
                    "gh",
                    "project",
                    "item-list",
                    str(self._sink().project_number or 0),
                    "--owner",
                    self._sink().owner or "",
                    "--limit",
                    "100000",
                    "--format",
                    "json",
                )
            )
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("project.itemsが見つかりません")
        for raw in items:
            if not isinstance(raw, dict):
                continue
            content = raw.get("content")
            if not isinstance(content, dict) or content.get("url") != issue_url:
                continue
            _string(raw.get("id"), "project_item.id")
            return raw
        return None

    def _project_field_values(self, issue_url: str) -> dict[str, str]:
        item = self._project_item(issue_url)
        if item is None:
            return {}
        raw_values = item.get("fieldValues")
        if not isinstance(raw_values, list):
            return {}
        values: dict[str, str] = {}
        for raw in raw_values:
            if not isinstance(raw, dict):
                continue
            field = raw.get("field")
            name = field.get("name") if isinstance(field, dict) else None
            if not isinstance(name, str):
                continue
            value = raw.get("name")
            if not isinstance(value, str):
                value = raw.get("date")
            if isinstance(value, str):
                values[name] = value
        return values

    def _fields(self) -> dict[str, dict[str, object]]:
        payload = _object(
            self.runner.run(
                (
                    "gh",
                    "project",
                    "field-list",
                    str(self._sink().project_number or 0),
                    "--owner",
                    self._sink().owner or "",
                    "--format",
                    "json",
                )
            )
        )
        raw_fields = payload.get("fields")
        if not isinstance(raw_fields, list):
            raise ValueError("project.fieldsが見つかりません")
        result: dict[str, dict[str, object]] = {}
        for raw in raw_fields:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            if isinstance(name, str):
                result[name] = raw
        return result

    def _edit_single_select(
        self,
        project_id: str,
        item_id: str,
        fields: dict[str, dict[str, object]],
        field_name: str,
        option_name: str,
    ) -> None:
        field = _field(fields, field_name)
        field_id = _string(field.get("id"), f"{field_name}.id")
        options = field.get("options")
        if not isinstance(options, list):
            raise ValueError(f"{field_name}.optionsが見つかりません")
        option_id: str | None = None
        for raw in options:
            if isinstance(raw, dict) and raw.get("name") == option_name:
                option_id = _string(raw.get("id"), f"{field_name}.option.id")
                break
        if option_id is None:
            raise ValueError(f"{field_name}の選択肢を利用できません: {option_name}")
        self.runner.run(
            (
                "gh",
                "project",
                "item-edit",
                "--id",
                item_id,
                "--project-id",
                project_id,
                "--field-id",
                field_id,
                "--single-select-option-id",
                option_id,
            )
        )

    def _edit_date(
        self,
        project_id: str,
        item_id: str,
        fields: dict[str, dict[str, object]],
        field_name: str,
        value: str,
    ) -> None:
        field = _field(fields, field_name)
        field_id = _string(field.get("id"), f"{field_name}.id")
        self.runner.run(
            (
                "gh",
                "project",
                "item-edit",
                "--id",
                item_id,
                "--project-id",
                project_id,
                "--field-id",
                field_id,
                "--date",
                value,
            )
        )

    @staticmethod
    def _expected_values(intent: ImprovementIssueIntent) -> dict[str, str]:
        return {
            "Status": intent.status,
            "Priority": intent.candidate.severity.value,
            "Area": intent.area,
            "Issue level": intent.issue_level,
            "Start date": intent.candidate.start_date,
            "Target date": intent.candidate.target_date,
        }

    def _validate_intent(self, intent: ImprovementIssueIntent) -> None:
        sink = self._sink()
        if intent.repository != sink.repository:
            raise ValueError("想定外のRepositoryです")
        if intent.project_number != sink.project_number:
            raise ValueError("想定外のProjectです")
        if intent.label != sink.label:
            raise ValueError("想定外の改善ラベルです")

    def _sink(self) -> SelfImprovementConfig:
        sink = self.config.self_improvement
        if not sink.enabled:
            raise ValueError("自己改善公開先が無効です")
        return sink

    def _web_issue_url(self, number: int) -> str:
        return f"https://github.com/{self._sink().repository or ''}/issues/{number}"


def improvement_intent(
    candidate: ImprovementCandidate,
    config: LoopEngineConfig,
) -> ImprovementIssueIntent:
    sink = config.self_improvement
    if not sink.enabled:
        raise ValueError("自己改善公開先が無効です")
    return ImprovementIssueIntent(
        repository=sink.repository or "",
        project_number=sink.project_number or 0,
        label=sink.label or "",
        status="Ready",
        area=sink.area or "",
        issue_level=sink.issue_level or "",
        candidate=candidate,
    )


@contextmanager
def _improvement_lock(improvement_key: str) -> Iterator[None]:
    """単一の信頼済み公開ホスト上で、同一keyの処理を直列化する。"""
    is_sha256 = len(improvement_key) == 64 and all(
        char in "0123456789abcdef" for char in improvement_key
    )
    if not is_sha256:
        raise ValueError("改善keyが不正です")
    directory = Path(tempfile.gettempdir()) / "loop-engineering-improvement-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / f"{improvement_key}.lock"
    with path.open("a", encoding="utf-8") as lock_file:
        os.chmod(path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _issue_number(url: str) -> int:
    value = url.rstrip("/").rsplit("/", 1)[-1]
    if not value.isdigit():
        raise ValueError("GitHub Issue作成結果にIssue URLがありません")
    return int(value)


def _object(raw: str) -> dict[str, object]:
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSONオブジェクトが必要です")
    return value


def _object_pages(raw: str) -> list[dict[str, object]]:
    value: object = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("ページ分割JSON配列が必要です")
    result: list[dict[str, object]] = []
    for page in value:
        if not isinstance(page, list):
            raise ValueError("JSONページ配列が必要です")
        result.extend(item for item in page if isinstance(item, dict))
    return result


def _field(
    fields: dict[str, dict[str, object]],
    name: str,
) -> dict[str, object]:
    try:
        return fields[name]
    except KeyError as error:
        raise ValueError(f"Projectのfieldを利用できません: {name}") from error


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name}が見つかりません")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{name}が見つかりません")
    return value
