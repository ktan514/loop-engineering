# V2製造作業計画書

管理Issue: #62

状態: 製造順序の正本

## 1. 前提

本書はV2製造作業の順序、開始日、終了日、依存関係の正本である。製造者は本書にない順序で外部effectを伴う実装を開始しない。開始日・終了日はGitHub Project #9の同名fieldへ転記する。

## 2. 作業順序

| 順序 | 作業 | 開始日 | 終了日 | 依存 | 完了条件 |
| --- | --- | --- | --- | --- | --- |
| 1 | DB transaction・lease・packet generation | 2026-08-31 | 2026-09-01 | #62 | intent、Checkpoint、leaseを同一transactionで確定し、競合leaseと停止を試験する |
| 2 | Issue / Project定義同期adapter | 2026-09-02 | 2026-09-03 | 1 | 指定IssueとProject fieldだけをrevision付きで同期し、自然文を解析しない |
| 3 | effect読戻し・outbox投稿adapter | 2026-09-04 | 2026-09-05 | 1, 2 | target限定readback、`UNCERTAIN`再送禁止、報告重複抑止を試験する |
| 4 | `--v2-once` Host合成・移行・受入 | 2026-09-06 | 2026-09-06 | 1, 2, 3 | 明示Workの1 packet・1遷移、旧入口拒否、停止復元の結合試験を通す |

## 3. 詳細設計への対応

| 作業 | 詳細設計の節 |
| --- | --- |
| 1 | `work_recovery_algorithm.md` 3、4、5 / `v2_adapters_cutover_and_acceptance.md` 4、6 |
| 2 | `v2_adapters_cutover_and_acceptance.md` 2、3 |
| 3 | `v2_adapters_cutover_and_acceptance.md` 4、5 |
| 4 | `v2_adapters_cutover_and_acceptance.md` 6、7、8 |

## 4. 共通完了条件

- `v2`ラベルを持つIssueとPRだけで作業する。
- 各WorkはProject #9へ追加し、本書の開始日・終了日を設定する。
- 新しい外部変更はDB intent、lease、Write Gate、readbackを通過する。
- 全Work完了後にだけ、#62の受入試験を実機で行う。
