"""決定論的なLoop Engineering健全性判定と自己改善方針。"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from .config import LoopEngineConfig
from .health_state import canonicalize_event, durable_identity
from .models import (
    ConflictKind,
    ExistingImprovementIssue,
    ImprovementCandidate,
    ImprovementSeverity,
    LoopHealthEvent,
    LoopHealthKind,
    RunDisposition,
)

_THRESHOLDS = {
    LoopHealthKind.REPEATED_FAILURE: 3,
    LoopHealthKind.NO_PROGRESS: 2,
    LoopHealthKind.MANUAL_INTERVENTION: 2,
    LoopHealthKind.MANUAL_OPERATION_REPEAT: 2,
    LoopHealthKind.STALE_STATE_RECURRENCE: 2,
    LoopHealthKind.DUPLICATE_SCHEDULING: 2,
    LoopHealthKind.RECOVERY_REPETITION: 2,
}

_TARGET_DAYS = {
    ImprovementSeverity.P0: 2,
    ImprovementSeverity.P1: 4,
    ImprovementSeverity.P2: 7,
}

_TITLES = {
    LoopHealthKind.REPEATED_FAILURE: "Loop改善: 反復失敗を自動回復可能にする",
    LoopHealthKind.NO_PROGRESS: "Loop改善: 進捗停止の反復を解消する",
    LoopHealthKind.MANUAL_INTERVENTION: "Loop改善: 反復する人間介入を削減する",
    LoopHealthKind.MANUAL_OPERATION_REPEAT: "Loop改善: 反復手動操作を自動化する",
    LoopHealthKind.STALE_STATE_RECURRENCE: "Loop改善: 古い状態の再発を防止する",
    LoopHealthKind.DUPLICATE_SCHEDULING: "Loop改善: 重複割当の再発を防止する",
    LoopHealthKind.RECOVERY_REPETITION: "Loop改善: 反復復旧手順を自動化する",
}


def advance_health(
    previous: tuple[LoopHealthEvent, ...],
    *,
    conflicts: tuple[ConflictKind, ...],
    disposition: RunDisposition,
    duplicate_suppressed: bool,
    selected_work_id: int | None,
) -> tuple[LoopHealthEvent, ...]:
    """監督判断1回後の累積健全性スナップショットを返す。"""
    current = {
        (item.kind, item.fingerprint): item
        for item in (canonicalize_event(item) for item in previous)
    }
    affected = (selected_work_id,) if selected_work_id is not None else ()

    if duplicate_suppressed:
        _increment(
            current,
            LoopHealthKind.DUPLICATE_SCHEDULING,
            durable_identity("same-schedule-key"),
            affected,
            ("supervisor:duplicate-suppressed",),
        )

    stale_conflicts = tuple(
        item
        for item in conflicts
        if item
        in {
            ConflictKind.MISSION_CHECKPOINT_STALE,
            ConflictKind.CHECKPOINT_LIVE_MISMATCH,
            ConflictKind.BASE_SHA_MISMATCH,
            ConflictKind.HEAD_SHA_MISMATCH,
            ConflictKind.UNEXPLAINED_SHA_CHANGE,
            ConflictKind.REVIEW_HEAD_MISMATCH,
            ConflictKind.CI_HEAD_MISMATCH,
        }
    )
    for conflict in stale_conflicts:
        _increment(
            current,
            LoopHealthKind.STALE_STATE_RECURRENCE,
            durable_identity(conflict.value),
            affected,
            (f"conflict:{conflict.value}",),
        )

    if disposition is RunDisposition.INTERVENTION_REQUIRED:
        fingerprint = durable_identity(
            ",".join(sorted(item.value for item in conflicts)) or "human-authority"
        )
        _increment(
            current,
            LoopHealthKind.MANUAL_INTERVENTION,
            fingerprint,
            affected,
            tuple(durable_identity(f"conflict:{item.value}") for item in conflicts),
            blocked_work_count=1 if affected else 0,
            manual_intervention_required=True,
        )

    return tuple(sorted(current.values(), key=lambda item: (item.kind.value, item.fingerprint)))


def plan_improvements(
    events: tuple[LoopHealthEvent, ...],
    *,
    existing_issues: tuple[ExistingImprovementIssue, ...],
    checkpoint_keys: tuple[str, ...],
    planning_date: date,
    max_candidates: int = 3,
) -> tuple[ImprovementCandidate, ...]:
    """上限付き・決定論的・修復可能な改善候補を生成する。"""
    if max_candidates < 1:
        return ()

    completed_open_keys = {
        item.improvement_key
        for item in existing_issues
        if item.state.lower() == "open" and item.project_configured
    }
    suppressed = completed_open_keys | set(checkpoint_keys)
    candidates: list[ImprovementCandidate] = []

    for event in sorted(events, key=_event_rank):
        if event.occurrence_count < _THRESHOLDS[event.kind]:
            continue
        key = improvement_key(event)
        if key in suppressed:
            continue
        severity = _severity(event)
        target = planning_date + timedelta(days=_TARGET_DAYS[severity])
        candidates.append(
            ImprovementCandidate(
                improvement_key=key,
                kind=event.kind,
                severity=severity,
                title=_TITLES[event.kind],
                problem=_problem(event),
                evidence_refs=tuple(_redacted_reference(item) for item in event.source_refs),
                affected_work_ids=event.affected_work_ids,
                start_date=planning_date.isoformat(),
                target_date=target.isoformat(),
            )
        )
        suppressed.add(key)
        if len(candidates) >= max_candidates:
            break

    return tuple(candidates)


def improvement_key(event: LoopHealthEvent) -> str:
    state = {
        "kind": event.kind.value,
        "fingerprint": event.fingerprint,
        "affected_work_ids": sorted(event.affected_work_ids),
    }
    serialized = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialized).hexdigest()


def marker(key: str) -> str:
    return f"<!-- loop-improvement-key:{key} -->"


def render_issue_body(candidate: ImprovementCandidate, config: LoopEngineConfig) -> str:
    """自己改善公開先のAuthorityに結び付けた日本語の改善Issue本文を生成する。"""
    sink = config.self_improvement
    if not sink.enabled:
        raise ValueError("自己改善公開先が無効です")
    affected = ", ".join(f"#{item}" for item in candidate.affected_work_ids) or "なし"
    evidence = "\n".join(f"- `{item}`" for item in candidate.evidence_refs) or "- なし"
    authority = "\n".join(f"- {item}" for item in sink.authority_refs) or "- GitHubの現在状態"
    return (
        f"{marker(candidate.improvement_key)}\n\n"
        f"Issue level: {sink.issue_level or ''}\n\n"
        "## 正本\n\n"
        f"{authority}\n\n"
        "## 自動生成元\n\n"
        "Loop Engineeringの自己改善系統が、通常実行中の型付き健全性証拠から"
        "生成した改善Workです。\n\n"
        f"- 発火条件: `{candidate.kind.value}`\n"
        f"- 優先度: `{candidate.severity.value}`\n"
        f"- 影響Work: {affected}\n"
        f"- 開始日: `{candidate.start_date}`\n"
        f"- 目標日: `{candidate.target_date}`\n\n"
        "## 問題\n\n"
        f"{candidate.problem}\n\n"
        "## 証拠\n\n"
        f"{evidence}\n\n"
        "## 完了条件\n\n"
        "- 同じ健全性指紋の再発原因を設計・実装で解消する\n"
        "- 失敗・復旧情報を型付きかつ秘密情報を含まない状態で維持する\n"
        "- 自動回復可能な事象を人間介入へ送らない\n"
        "- 対象試験、Ruff、厳格Mypy、全pytest、厳密HEAD CIを実行する\n"
    )


def _increment(
    events: dict[tuple[LoopHealthKind, str], LoopHealthEvent],
    kind: LoopHealthKind,
    fingerprint: str,
    affected_work_ids: tuple[int, ...],
    source_refs: tuple[str, ...],
    *,
    blocked_work_count: int = 0,
    manual_intervention_required: bool = False,
) -> None:
    fingerprint = durable_identity(fingerprint)
    source_refs = tuple(durable_identity(item) for item in source_refs)
    key = (kind, fingerprint)
    prior = events.get(key)
    if prior is None:
        events[key] = LoopHealthEvent(
            kind,
            fingerprint,
            1,
            affected_work_ids,
            source_refs,
            blocked_work_count,
            manual_intervention_required,
        )
        return
    events[key] = LoopHealthEvent(
        kind,
        fingerprint,
        prior.occurrence_count + 1,
        tuple(sorted(set(prior.affected_work_ids) | set(affected_work_ids))),
        tuple(dict.fromkeys(prior.source_refs + source_refs)),
        max(prior.blocked_work_count, blocked_work_count),
        prior.manual_intervention_required or manual_intervention_required,
    )


def _severity(event: LoopHealthEvent) -> ImprovementSeverity:
    if event.manual_intervention_required or event.blocked_work_count > 0:
        return ImprovementSeverity.P0
    if event.kind in {
        LoopHealthKind.REPEATED_FAILURE,
        LoopHealthKind.NO_PROGRESS,
        LoopHealthKind.MANUAL_OPERATION_REPEAT,
        LoopHealthKind.STALE_STATE_RECURRENCE,
        LoopHealthKind.RECOVERY_REPETITION,
    }:
        return ImprovementSeverity.P1
    return ImprovementSeverity.P2


def _event_rank(event: LoopHealthEvent) -> tuple[int, int, str, str]:
    priority = {
        ImprovementSeverity.P0: 0,
        ImprovementSeverity.P1: 1,
        ImprovementSeverity.P2: 2,
    }[_severity(event)]
    return (priority, -event.occurrence_count, event.kind.value, event.fingerprint)


def _problem(event: LoopHealthEvent) -> str:
    return (
        f"`{event.kind.value}` が {event.occurrence_count} 回観測されました。"
        "同じ原因を通常のLoopで自動回復できるようにしてください。"
    )


def _redacted_reference(value: str) -> str:
    return durable_identity(value)
