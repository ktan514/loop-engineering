# Loop Engineering 正本索引

## 中核

- `loop_mission_supervisor.md` — Mission監督、再調整、Work選択、Resume Gate、Task Packet、Write Gate
- `loop_self_improvement.md` — 健全性観測と自己改善Work
- `loop_design_completion_matrix.md` — 設計責務の完了表
- `loop_cross_design_audit.md` — 横断監査

## 実行・復旧

- `loop_autonomous_runner.md` — 継続実行型の自律実行機
- `loop_integration_recovery.md` — 統合、再取得、競合、復旧、実運用の受け入れ条件
- `deterministic_ci.md` — 厳密HEADへ結び付ける機械検査

## レビュー・運用記憶

- `loop_canonical_review_pipeline.md` — 正本レビュー経路
- `trusted_host_reviewer_boundary.md` — レビューワー認証情報と対象作業領域の物理分離
- `loop_operational_store.md` — PostgreSQL運用記憶

## GitHub / Repository運用

- `project_v2_management_spec.md` — 専用GitHub Project管理仕様
- `branch_lifecycle_and_commit_hygiene.md` — branch / merge / 履歴品質
- `commit_message_language_policy.md` — commit messageの日本語運用

## Repository運用文書

- `../../operations/loop_environment_preflight.md`
- `../../operations/loop_mission_goal.md`
- `../../operations/chatgpt_resume_gate.md`
- `../../operations/github_project_management.md`
- `../../operations/loop_engineering_operation_hub.md`

## 移行・履歴

- `../../migration/ai_liver_yura_current_loop_engineering_import.md`
- `../../history/ai_liver_yura_issue_pr_mapping.md`
- `../../history/ai_liver_yura_japanese_and_runtime_repairs.md`

## 文章言語

人間向け文章はRepository rootの`AGENTS.md`に従い日本語を基本とする。英語技術語の原語が必要な場合は自然な日本語の意味を先に示し、必要な場合だけ括弧内へ併記する。機械識別子、status値、command、path、SHA、API/class/function/field名、機械可読値は維持してよい。
