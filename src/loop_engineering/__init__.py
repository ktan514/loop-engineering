"""Standalone Loop Engineering control-plane package."""

from .config import LoopEngineConfig
from .health import advance_health, plan_improvements
from .health_state import decode_health_state, encode_health_state
from .maintenance import LoopMaintenanceCycle, SelfImprovementController
from .models import (
    CanonicalDesignSnapshot,
    ConflictKind,
    ExistingImprovementIssue,
    ImprovementCandidate,
    ImprovementIssueIntent,
    ImprovementPublishFailure,
    ImprovementPublishResult,
    ImprovementSeverity,
    LineageClassification,
    LineageSnapshot,
    LoopHealthEvent,
    LoopHealthKind,
    MissionSnapshot,
    ObservationEpoch,
    ResumeCertificate,
    RunDisposition,
    SourceIdentity,
    SupervisorDecision,
    TaskPacket,
    WorkSnapshot,
    WriteGateResult,
    WriteIntent,
)
from .supervisor import MissionSupervisor

__all__ = [
    "CanonicalDesignSnapshot",
    "ConflictKind",
    "ExistingImprovementIssue",
    "ImprovementCandidate",
    "ImprovementIssueIntent",
    "ImprovementPublishFailure",
    "ImprovementPublishResult",
    "ImprovementSeverity",
    "LineageClassification",
    "LineageSnapshot",
    "LoopEngineConfig",
    "LoopHealthEvent",
    "LoopHealthKind",
    "LoopMaintenanceCycle",
    "MissionSnapshot",
    "MissionSupervisor",
    "ObservationEpoch",
    "ResumeCertificate",
    "RunDisposition",
    "SelfImprovementController",
    "SourceIdentity",
    "SupervisorDecision",
    "TaskPacket",
    "WorkSnapshot",
    "WriteGateResult",
    "WriteIntent",
    "advance_health",
    "decode_health_state",
    "encode_health_state",
    "plan_improvements",
]
