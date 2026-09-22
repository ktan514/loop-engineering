from pathlib import Path

import pytest

from loop_engineering.config import (
    LoopEngineConfig,
    LoopEngineeringSettings,
    ModelConfig,
    ReviewLevelConfig,
    SecretReferenceConfig,
)
from loop_engineering.controlled_e2e_audit import audit_controlled_e2e


class FakeDatabase:
    def __init__(self, *, evidence_count: int = 4) -> None:
        self.evidence_count = evidence_count

    def query_json_rows(self, sql: str) -> list[dict[str, object]] | None:
        if "FROM loop_autonomous_runtimes" in sql:
            return [{
                "runtime_identity": "runtime:1",
                "goal_revision": "goal-rev-1",
                "status": "COMPLETED",
                "current_work_identity": None,
            }]
        if "FROM loop_goal_plans" in sql:
            return [{
                "projection_json": {
                    "works": [{"issue_number": 2}],
                }
            }]
        if "FROM loop_work_records" in sql:
            return [{"lifecycle": "COMPLETED"}]
        if "FROM loop_local_quality_state" in sql:
            return [{
                "stage": "LOCAL_PASS",
                "local_pass_identity": "local-pass:1",
            }]
        if "FROM loop_external_review_state" in sql:
            return [{
                "status": "PASS",
                "completed_evidence": [
                    f"review:{index}" for index in range(self.evidence_count)
                ],
            }]
        if "FROM loop_autonomous_dispatches" in sql:
            return []
        if "FROM loop_effect_attempts" in sql:
            return []
        if "FROM loop_bootstrap_effects" in sql:
            return []
        raise AssertionError(sql)


def settings(tmp_path: Path) -> LoopEngineeringSettings:
    return LoopEngineeringSettings(
        config_path=tmp_path / "config.ini",
        project_key="e2e",
        workspace_path=tmp_path,
        engine=LoopEngineConfig(
            repository="owner/product",
            owner="owner",
            project_number=10,
            mission_issue=None,
        ),
        models=ModelConfig(
            "local-llm-coder",
            "local",
            "openai",
            "high",
            "https://api.example.test/v1",
            "local-main",
        ),
        secrets=SecretReferenceConfig(
            "GH_TOKEN",
            "OPENAI_API_KEY",
            "LOOP_POSTGRES_DSN",
            "LOOP_TRUSTED_REVIEWER_SOCKET",
        ),
        review_levels=(
            ReviewLevelConfig(
                1,
                "openai",
                "low",
                "https://api.example.test/v1",
                "OPENAI_API_KEY",
                passes_required=2,
            ),
            ReviewLevelConfig(
                2,
                "openai",
                "high",
                "https://api.example.test/v1",
                "OPENAI_API_KEY",
                passes_required=2,
            ),
        ),
    )


def test_audit_requires_all_four_external_fresh_passes(tmp_path: Path) -> None:
    result = audit_controlled_e2e(
        settings=settings(tmp_path),
        environment={},
        database=FakeDatabase(),
    )

    assert result["status"] == "PASS"
    assert result["required_external_fresh_passes_per_work"] == 4


def test_audit_rejects_missing_external_fresh_pass(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="FRESH_PASS_COUNT"):
        audit_controlled_e2e(
            settings=settings(tmp_path),
            environment={},
            database=FakeDatabase(evidence_count=3),
        )
