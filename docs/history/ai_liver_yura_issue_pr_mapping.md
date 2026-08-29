# AI Liver ゆら Loop Engineering Issue / PR対応表

抽出元snapshot: `ktan514/ai-liver-yura@1ef8e5de344e854865187e0dd8c465eca650f333`

## 中核責務

| source Issue | 責務 | 主なsource PR / 履歴 | snapshot状態 |
| --- | --- | --- | --- |
| #462 | Loop Engineering親Mission | #466, #473-#484等 | open |
| #463 | 環境・利用能力の事前確認 | 初期Preflight / host reviewer bootstrap | closed |
| #465 | Mission監督 / Work選択 / 自己改善 | #466 | closed |
| #468 | 全体設計完了Gate / 横断監査 | #473 | closed |
| #469 | Preflight / Host Launcherの`tools/loop_engine`移設 | #474 | closed |
| #470 | PostgreSQL運用記憶 | #475 | closed |
| #467 | 自律実行機 / 制御Loop | #476 | closed |
| #472 | 信頼済み正本レビューワー境界 | host broker運用 | closed |
| #471 | E2E Integration / 実製品試験 | #477-#486 | open |

## 実運用修正

| source PR | 内容 |
| --- | --- |
| #479 | #471自身を実製品試験候補へ再選択する循環を防止 |
| #480 | Codex CLI 0.150系の起動契約へ修正 |
| #481 | runtime進捗と診断ログを可視化 |
| #482 | `dirty` PRをCodex再調整へ戻す |
| #483 | console簡潔化とCodex生存通知 |
| #484 | 既定を継続実行へ変更、`--once`を診断用に維持 |
| #485 / #486 | Mission Checkpointの`current Work`等の固定項目契約 |
| #489 | Codex終了だけで成功扱いしない、Git操作を信頼済みホストへ分離、日本語規則を`AGENTS.md`へ正本化 |
| #490 | Loop文書・ログ・Mission Goal等の日本語化 |
| #491 | Loop制御コード、Codex指示、Checkpoint等の日本語化 |
| #492 | Mission監督正本、V2索引、Preflight説明の日本語化 |

## レビュー系の関連履歴

#369〜#373は、実装担当と独立レビューワーの責務分離、厳密HEAD、古い結果拒否、修正循環、安全境界の知見を提供した履歴である。

現在のLoop正本レビューは`docs/architecture/v2/loop_canonical_review_pipeline.md`と`trusted_host_reviewer_boundary.md`を優先する。旧PR・旧branchを現在作業系列として再利用しない。

## 日本語・Git履歴管理

source Issue #384を、Repository文章言語、commit message、branch lifecycle、履歴整理の管理Authorityとして参照する。

専用Repositoryでは次へ引き継ぐ。

- root `AGENTS.md`
- `docs/architecture/v2/commit_message_language_policy.md`
- `docs/architecture/v2/branch_lifecycle_and_commit_hygiene.md`
- `docs/history/ai_liver_yura_japanese_and_runtime_repairs.md`
- 専用RepositoryのGitHub運用ハブIssue

## destination

- 現行一括同期Work: destination Issue #24
- 現行同期PR: destination PR #25
- 旧中間取り込み #20 / PR #22: superseded / unmerged
- 旧Project補助script案 #21 / PR #23: superseded / unmerged
