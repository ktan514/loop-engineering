"""Configuration for the extracted Loop Engineering control plane."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LoopEngineConfig:
    repository: str
    owner: str
    project_number: int
    label: str = "loop-engineering"
    trunk_branch: str = "main"
    authority_refs: tuple[str, ...] = ()
    improvement_area: str = "Infrastructure / Development Tooling"
    issue_level: str = "Work"

    def __post_init__(self) -> None:
        text_fields = (
            ("repository", self.repository),
            ("owner", self.owner),
            ("label", self.label),
            ("trunk_branch", self.trunk_branch),
            ("improvement_area", self.improvement_area),
            ("issue_level", self.issue_level),
        )
        for name, value in text_fields:
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.project_number < 1:
            raise ValueError("project_number must be positive")
        if any(not item.strip() for item in self.authority_refs):
            raise ValueError("authority_refs must not contain blank values")
