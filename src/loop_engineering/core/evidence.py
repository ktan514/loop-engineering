"""Exact-target evidence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .identities import ExecutionTarget, SourceIdentity


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    kind: str
    target_identity: ExecutionTarget
    source_identity: SourceIdentity
    result: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.evidence_id, "evidence_id")
        _require_text(self.kind, "kind")
        _require_text(self.result, "result")
        _require_aware(self.observed_at, "observed_at")
