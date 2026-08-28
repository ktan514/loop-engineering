"""Typed conflict contracts for reconciliation and gate decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .identities import SourceIdentity


class ConflictKind(str, Enum):
    AUTHORITY_UNAVAILABLE = "AUTHORITY_UNAVAILABLE"
    AUTHORITY_CONTRADICTION = "AUTHORITY_CONTRADICTION"
    PROFILE_UNRESOLVED = "PROFILE_UNRESOLVED"
    CANONICAL_UNRESOLVED = "CANONICAL_UNRESOLVED"
    CANONICAL_MISMATCH = "CANONICAL_MISMATCH"
    MULTIPLE_ACTIVE_LINEAGES = "MULTIPLE_ACTIVE_LINEAGES"
    UNKNOWN_LINEAGE = "UNKNOWN_LINEAGE"
    BASE_IDENTITY_MISMATCH = "BASE_IDENTITY_MISMATCH"
    HEAD_IDENTITY_MISMATCH = "HEAD_IDENTITY_MISMATCH"
    UNEXPLAINED_TARGET_ADVANCE = "UNEXPLAINED_TARGET_ADVANCE"
    CHECKPOINT_LIVE_MISMATCH = "CHECKPOINT_LIVE_MISMATCH"
    CI_TARGET_MISMATCH = "CI_TARGET_MISMATCH"
    REVIEW_TARGET_MISMATCH = "REVIEW_TARGET_MISMATCH"
    VERIFICATION_MISMATCH = "VERIFICATION_MISMATCH"
    LEASE_CONFLICT = "LEASE_CONFLICT"
    FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
    RUNTIME_STATE_UNAVAILABLE = "RUNTIME_STATE_UNAVAILABLE"


class ConflictScope(str, Enum):
    GLOBAL = "GLOBAL"
    PROJECT = "PROJECT"
    WORK = "WORK"
    LINEAGE = "LINEAGE"
    TRANSITION = "TRANSITION"
    WORKSPACE = "WORKSPACE"
    SECURITY = "SECURITY"


class ConflictSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


@dataclass(frozen=True, slots=True)
class Conflict:
    kind: ConflictKind
    scope: ConflictScope
    severity: ConflictSeverity
    subject_identity: SourceIdentity | None = None
    evidence_refs: tuple[SourceIdentity, ...] = ()
    resolution_policy: str = "manual-reconcile"

    def __post_init__(self) -> None:
        if not self.resolution_policy.strip():
            raise ValueError("resolution_policy must not be blank")
