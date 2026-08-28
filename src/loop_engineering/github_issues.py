"""Trusted-host GitHub publisher for Loop Engineering improvement Work."""

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

from .config import LoopEngineConfig
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
        """Run a trusted fixed-shape command and return stdout."""


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
                        self.config.repository,
                        "--title",
                        intent.candidate.title,
                        "--body",
                        render_issue_body(intent.candidate, self.config),
                        "--label",
                        self.config.label,
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
                f"repos/{self.config.repository}/issues?state=open&labels={self.config.label}&per_page=100",
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
            self._require_project_gate(
                WriteIntent(
                    "project-item-add",
                    "project",
                    str(self.config.project_number),
                    "add_project_item",
                    (("project_id", project_id), ("item_presence", "absent")),
                    (),
                    "publisher-live-readback",
                ),
                {
                    "project_id": _string(self._project().get("id"), "project.id"),
                    "item_presence": (
                        "present" if self._project_item_id(issue_url) is not None else "absent"
                    ),
                },
            )
            added = _object(
                self.runner.run(
                    (
                        "gh",
                        "project",
                        "item-add",
                        str(self.config.project_number),
                        "--owner",
                        self.config.owner,
                        "--url",
                        issue_url,
                        "--format",
                        "json",
                    )
                )
            )
            item_id = _string(added.get("id"), "project_item.id")

        for field_name, value in self._expected_values(intent).items():
            self._edit_field_with_fresh_gate(issue_url, field_name, value)

        observed = self._project_field_values(issue_url)
        expected = self._expected_values(intent)
        if any(observed.get(name) != value for name, value in expected.items()):
            raise ValueError(ConflictKind.MUTATION_EFFECT_MISMATCH.value)

    def _edit_field_with_fresh_gate(self, issue_url: str, field_name: str, value: str) -> None:
        project = self._project()
        project_id = _string(project.get("id"), "project.id")
        item_id = self._project_item_id(issue_url)
        if item_id is None:
            raise ValueError("project item disappeared before mutation")
        fields = self._fields()
        field = _field(fields, field_name)
        field_id = _string(field.get("id"), f"{field_name}.id")
        preconditions = {
            "project_id": project_id,
            "item_id": item_id,
            "field_id": field_id,
        }
        self._require_project_gate(
            WriteIntent(
                f"project-edit-{field_name}",
                "project",
                str(self.config.project_number),
                "edit_project_field",
                tuple(preconditions.items()),
                (),
                "publisher-live-readback",
            ),
            preconditions,
        )

        if field_name in {"Start date", "Target date"}:
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
        else:
            option_id = _option_id(field, field_name, value)
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

        if self._project_field_values(issue_url).get(field_name) != value:
            raise ValueError(ConflictKind.MUTATION_EFFECT_MISMATCH.value)

    def _require_project_gate(
        self,
        intent: WriteIntent,
        fresh_preconditions: dict[str, str],
    ) -> None:
        result = validate(intent, fresh_preconditions, config=self.config)
        if not result.allowed:
            raise ValueError((result.conflict or ConflictKind.STALE_WRITE_GATE).value)

    def _project(self) -> dict[str, object]:
        return _object(
            self.runner.run(
                (
                    "gh",
                    "project",
                    "view",
                    str(self.config.project_number),
                    "--owner",
                    self.config.owner,
                    "--format",
                    "json",
                )
            )
        )

    def _project_item_id(self, issue_url: str) -> str | None:
        item = self._project_item(issue_url)
        return _optional_string(item.get("id")) if item is not None else None

    def _project_item(self, issue_url: str) -> dict[str, object] | None:
        payload = _object(
            self.runner.run(
                (
                    "gh",
                    "project",
                    "item-list",
                    str(self.config.project_number),
                    "--owner",
                    self.config.owner,
                    "--limit",
                    "100000",
                    "--format",
                    "json",
                )
            )
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("project.items missing")
        for raw in items:
            if not isinstance(raw, dict):
                continue
            content = raw.get("content")
            if isinstance(content, dict) and content.get("url") == issue_url:
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
                    str(self.config.project_number),
                    "--owner",
                    self.config.owner,
                    "--format",
                    "json",
                )
            )
        )
        raw_fields = payload.get("fields")
        if not isinstance(raw_fields, list):
            raise ValueError("project.fields missing")
        return {
            str(raw["name"]): raw
            for raw in raw_fields
            if isinstance(raw, dict) and isinstance(raw.get("name"), str)
        }

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
        if intent.repository != self.config.repository:
            raise ValueError("unexpected repository")
        if intent.project_number != self.config.project_number:
            raise ValueError("unexpected project")
        if intent.label != self.config.label:
            raise ValueError("unexpected improvement label")

    def _web_issue_url(self, number: int) -> str:
        return f"https://github.com/{self.config.repository}/issues/{number}"


def improvement_intent(
    candidate: ImprovementCandidate,
    config: LoopEngineConfig,
) -> ImprovementIssueIntent:
    return ImprovementIssueIntent(
        repository=config.repository,
        project_number=config.project_number,
        label=config.label,
        status="Ready",
        area=config.improvement_area,
        issue_level=config.issue_level,
        candidate=candidate,
    )


@contextmanager
def _improvement_lock(improvement_key: str) -> Iterator[None]:
    is_sha256 = len(improvement_key) == 64 and all(
        char in "0123456789abcdef" for char in improvement_key
    )
    if not is_sha256:
        raise ValueError("invalid improvement key")
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
        raise ValueError("GitHub issue create did not return an issue URL")
    return int(value)


def _object(raw: str) -> dict[str, object]:
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _object_pages(raw: str) -> list[dict[str, object]]:
    value: object = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("expected paginated JSON array")
    result: list[dict[str, object]] = []
    for page in value:
        if not isinstance(page, list):
            raise ValueError("expected JSON page array")
        result.extend(item for item in page if isinstance(item, dict))
    return result


def _field(fields: dict[str, dict[str, object]], name: str) -> dict[str, object]:
    try:
        return fields[name]
    except KeyError as exc:
        raise ValueError(f"project field unavailable: {name}") from exc


def _option_id(field: dict[str, object], field_name: str, option_name: str) -> str:
    options = field.get("options")
    if not isinstance(options, list):
        raise ValueError(f"{field_name}.options missing")
    for raw in options:
        if isinstance(raw, dict) and raw.get("name") == option_name:
            return _string(raw.get("id"), f"{field_name}.option.id")
    raise ValueError(f"{field_name} option unavailable: {option_name}")


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} missing")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{name} missing")
    return value
