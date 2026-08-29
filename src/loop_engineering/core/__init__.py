"""Provider-independent Loop Engineering Core contracts."""

from .conflicts import Conflict, ConflictKind, ConflictScope, ConflictSeverity
from .evidence import Evidence
from .identities import CanonicalGeneration, ExecutionTarget, SourceIdentity
from .models import (
    Checkpoint,
    Lineage,
    LineageClassification,
    ObservationEpoch,
    RunGoal,
    RunGoalKind,
    WorkItem,
)
from .serialization import canonical_digest, canonical_json

__all__ = [
    "CanonicalGeneration",
    "Checkpoint",
    "Conflict",
    "ConflictKind",
    "ConflictScope",
    "ConflictSeverity",
    "Evidence",
    "ExecutionTarget",
    "Lineage",
    "LineageClassification",
    "ObservationEpoch",
    "RunGoal",
    "RunGoalKind",
    "SourceIdentity",
    "WorkItem",
    "canonical_digest",
    "canonical_json",
]
