"""副作用を持たない書込み判定。接続層はこの確認後だけ変更を実行する。"""

from __future__ import annotations

from collections.abc import Mapping

from .config import LoopEngineConfig
from .models import ConflictKind, WriteGateResult, WriteIntent


def validate_preconditions(
    intent: WriteIntent,
    fresh_preconditions: Mapping[str, str],
    *,
    config: LoopEngineConfig,
) -> WriteGateResult:
    """外部変更前にtarget identityと事前条件だけを検証する。"""
    if intent.target_kind == "project":
        allowed_projects = {str(config.project_number)}
        sink = config.self_improvement
        if sink.enabled and sink.project_number is not None:
            allowed_projects.add(str(sink.project_number))
        if intent.target_identity not in allowed_projects:
            return WriteGateResult(False, ConflictKind.FORBIDDEN_PROJECT_IDENTITY)
    if intent.target_kind == "branch" and intent.target_identity == config.trunk_branch:
        return WriteGateResult(False, ConflictKind.DIRECT_TRUNK_WRITE_FORBIDDEN)
    if intent.target_kind == "branch" and intent.mutation_kind == "content":
        required = {"branch_ref", "pr_number", "head_sha"}
        if required.difference(dict(intent.expected_preconditions)):
            return WriteGateResult(False, ConflictKind.UNKNOWN_WRITE_IDENTITY_FORBIDDEN)
    if intent.mutation_kind != "verify_effect" and not intent.expected_preconditions:
        return WriteGateResult(False, ConflictKind.NO_OP_MUTATION_FORBIDDEN)
    if any(fresh_preconditions.get(key) != value for key, value in intent.expected_preconditions):
        return WriteGateResult(False, ConflictKind.STALE_WRITE_GATE)
    if intent.mutation_kind == "verify_effect" and not intent.expected_effect:
        return WriteGateResult(False, ConflictKind.NO_OP_MUTATION_FORBIDDEN)
    return WriteGateResult(True, None)


def validate(
    intent: WriteIntent,
    fresh_preconditions: Mapping[str, str],
    readback_effect: Mapping[str, str] | None = None,
    *,
    config: LoopEngineConfig,
) -> WriteGateResult:
    """互換入口として事前条件と変更後効果をまとめて検証する。"""
    precondition_result = validate_preconditions(intent, fresh_preconditions, config=config)
    if not precondition_result.allowed:
        return precondition_result
    if intent.expected_effect and readback_effect is None:
        return WriteGateResult(False, ConflictKind.MUTATION_EFFECT_MISMATCH)
    if readback_effect is not None and any(
        readback_effect.get(key) != value for key, value in intent.expected_effect
    ):
        return WriteGateResult(False, ConflictKind.MUTATION_EFFECT_MISMATCH)
    return WriteGateResult(True, None)
