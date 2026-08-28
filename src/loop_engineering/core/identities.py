"""Provider-independent identity contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from .serialization import canonical_digest


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    provider_kind: str
    object_kind: str
    stable_id: str
    revision_id: str
    observed_at: datetime = field(compare=False, hash=False)

    def __post_init__(self) -> None:
        _require_text(self.provider_kind, "provider_kind")
        _require_text(self.object_kind, "object_kind")
        _require_text(self.stable_id, "stable_id")
        _require_text(self.revision_id, "revision_id")
        _require_aware(self.observed_at, "observed_at")

    def identity_payload(self) -> dict[str, str]:
        return {
            "provider_kind": self.provider_kind,
            "object_kind": self.object_kind,
            "stable_id": self.stable_id,
            "revision_id": self.revision_id,
        }

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.identity_payload())

    @property
    def identity_sort_key(self) -> tuple[str, str, str, str]:
        return (
            self.provider_kind,
            self.object_kind,
            self.stable_id,
            self.revision_id,
        )


@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    repository_identity: SourceIdentity
    workspace_identity: SourceIdentity | None = None
    ref_identity: SourceIdentity | None = None
    base_identity: SourceIdentity | None = None
    head_identity: SourceIdentity | None = None
    canonical_generation_digest: str | None = None

    def __post_init__(self) -> None:
        if self.canonical_generation_digest is not None:
            _require_text(self.canonical_generation_digest, "canonical_generation_digest")

    def identity_payload(self) -> dict[str, object]:
        return {
            "repository_identity": self.repository_identity.identity_payload(),
            "workspace_identity": _identity_payload(self.workspace_identity),
            "ref_identity": _identity_payload(self.ref_identity),
            "base_identity": _identity_payload(self.base_identity),
            "head_identity": _identity_payload(self.head_identity),
            "canonical_generation_digest": self.canonical_generation_digest,
        }

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.identity_payload())


@dataclass(frozen=True, slots=True)
class CanonicalGeneration:
    refs: tuple[SourceIdentity, ...]
    normalized_digest: str = field(init=False)

    def __post_init__(self) -> None:
        normalized_refs = _normalize_refs(self.refs)
        if not normalized_refs:
            raise ValueError("CanonicalGeneration requires at least one reference")
        object.__setattr__(self, "refs", normalized_refs)
        object.__setattr__(
            self,
            "normalized_digest",
            canonical_digest(tuple(ref.identity_payload() for ref in normalized_refs)),
        )

    @classmethod
    def from_refs(cls, refs: Iterable[SourceIdentity]) -> CanonicalGeneration:
        return cls(tuple(refs))


def _identity_payload(identity: SourceIdentity | None) -> dict[str, str] | None:
    return None if identity is None else identity.identity_payload()


def _normalize_refs(refs: tuple[SourceIdentity, ...]) -> tuple[SourceIdentity, ...]:
    unique: dict[tuple[str, str, str, str], SourceIdentity] = {}
    for ref in refs:
        key = ref.identity_sort_key
        current = unique.get(key)
        if current is None or ref.observed_at < current.observed_at:
            unique[key] = ref
    return tuple(unique[key] for key in sorted(unique))
