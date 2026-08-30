"""実ホストのReady・review・merge境界を安全に制御する。"""

from __future__ import annotations

from collections.abc import Mapping

from .ci_gate import CIGateStatus
from .config import LoopEngineConfig
from .host_entrypoint import (
    PilotAwareMissionPort,
    PilotPlanningImplementer,
    ReconciliationAwareHostLoopController,
    StrictGhMissionPort,
)
from .host_runtime import (
    HostTarget,
    HostTransitionResult,
    HostTransitionStatus,
    LocalRunner,
)


class SafeActualHostMissionPort(StrictGhMissionPort):
    """Draftを同一遷移で統合せず、日本語の通常mergeだけを実行する。"""

    def __init__(
        self,
        config: LoopEngineConfig,
        runner: LocalRunner,
        environment: Mapping[str, str],
    ) -> None:
        super().__init__(config, runner, environment)

    def make_ready_for_review(self, target: HostTarget) -> bool:
        """expected HEADを維持したままReady化だけを行う。"""
        if target.pr_number is None or target.head_sha is None:
            return False
        fresh = self.current_target()
        if (
            fresh is None
            or fresh.pr_number != target.pr_number
            or fresh.head_sha != target.head_sha
            or fresh.stale_checkpoint
            or fresh.merged
        ):
            return False
        if not fresh.draft:
            return True
        ready = self._run_gh(
            ("pr", "ready", str(target.pr_number), "--repo", self.config.repository)
        )
        if not ready.succeeded:
            return False
        readback = self.current_target()
        return bool(
            readback is not None
            and readback.pr_number == target.pr_number
            and readback.head_sha == target.head_sha
            and not readback.stale_checkpoint
            and not readback.draft
            and not readback.merged
        )

    def has_current_head_review(self, target: HostTarget) -> bool:
        """current exact HEADへ提出済みのreview evidenceがあるか確認する。"""
        if target.pr_number is None or target.head_sha is None:
            return False
        raw = self._api_value(
            f"repos/{self.config.repository}/pulls/{target.pr_number}/reviews?per_page=100"
        )
        if not isinstance(raw, list):
            raise RuntimeError("GitHubレビュー応答が一覧ではありません")
        for item in raw:
            if not isinstance(item, dict):
                continue
            if item.get("commit_id") != target.head_sha:
                continue
            state = item.get("state")
            if state in {"DISMISSED", "PENDING"}:
                continue
            return True
        return False

    def merge_current(self, target: HostTarget) -> bool:
        """Ready済みexpected HEADだけを日本語messageで通常mergeする。"""
        self._merge_conflict_target = None
        if target.pr_number is None or target.head_sha is None:
            return False
        if self._pull_requires_reconciliation(target):
            self._remember_merge_conflict(target)
            return False

        fresh = self.current_target()
        if (
            fresh is None
            or fresh.pr_number != target.pr_number
            or fresh.head_sha != target.head_sha
            or fresh.stale_checkpoint
        ):
            return False
        if fresh.merged:
            return True
        if fresh.draft:
            return False

        pull = self._api_json(f"repos/{self.config.repository}/pulls/{target.pr_number}")
        if pull.get("state") != "open":
            return False
        head = pull.get("head")
        base = pull.get("base")
        if not isinstance(head, dict) or head.get("sha") != target.head_sha:
            return False
        if not isinstance(base, dict) or not isinstance(base.get("ref"), str):
            return False
        base_ref = base["ref"]

        merged = self._run_gh(
            (
                "pr",
                "merge",
                str(target.pr_number),
                "--repo",
                self.config.repository,
                "--merge",
                "--match-head-commit",
                target.head_sha,
                "--subject",
                f"PR #{target.pr_number} を {base_ref} へ統合する",
                "--body",
                f"Work #{target.work_issue} の変更を通常マージで統合する。",
            ),
            timeout_seconds=180,
        )
        if not merged.succeeded:
            if self._pull_requires_reconciliation(target):
                self._remember_merge_conflict(target)
            return False
        readback = self._api_json(f"repos/{self.config.repository}/pulls/{target.pr_number}")
        return bool(readback.get("merged"))


class ReviewAwareHostLoopController(ReconciliationAwareHostLoopController):
    """Ready化とcurrent-head review到着をmergeとは別遷移にする。"""

    def __init__(
        self,
        config: LoopEngineConfig,
        mission: PilotAwareMissionPort,
        implementer: PilotPlanningImplementer,
        review_port: SafeActualHostMissionPort,
    ) -> None:
        super().__init__(config, mission, implementer)
        self._review_port = review_port

    def run_once(self) -> HostTransitionResult:
        try:
            target = self._reconciliation_mission.current_target()
        except RuntimeError:
            return super().run_once()

        if (
            target is not None
            and target.issue_open
            and not target.merged
            and not target.stale_checkpoint
            and target.pr_number is not None
            and target.head_sha is not None
        ):
            try:
                ci = self._reconciliation_mission.ci_status(target)
            except RuntimeError:
                return HostTransitionResult(
                    HostTransitionStatus.INTERVENTION_REQUIRED,
                    "CI_OBSERVE_FAILED",
                    target.work_issue,
                    target.pr_number,
                    target.head_sha,
                )
            if ci is CIGateStatus.PASS:
                if target.draft:
                    if not self._review_port.make_ready_for_review(target):
                        return HostTransitionResult(
                            HostTransitionStatus.INTERVENTION_REQUIRED,
                            "READY_TRANSITION_FAILED",
                            target.work_issue,
                            target.pr_number,
                            target.head_sha,
                        )
                    return HostTransitionResult(
                        HostTransitionStatus.YIELD_EXTERNAL,
                        "REVIEW_PENDING",
                        target.work_issue,
                        target.pr_number,
                        target.head_sha,
                    )
                try:
                    reviewed = self._review_port.has_current_head_review(target)
                except RuntimeError:
                    return HostTransitionResult(
                        HostTransitionStatus.YIELD_EXTERNAL,
                        "REVIEW_PENDING",
                        target.work_issue,
                        target.pr_number,
                        target.head_sha,
                    )
                if not reviewed:
                    return HostTransitionResult(
                        HostTransitionStatus.YIELD_EXTERNAL,
                        "REVIEW_PENDING",
                        target.work_issue,
                        target.pr_number,
                        target.head_sha,
                    )

        return super().run_once()
