# Loop Engineering Project仕様・運用ルール

状態: 正本
適用日: 2026-08-29
Project owner: `ktan514`
Repository: `ktan514/loop-engineering`

## 1. 目的

GitHub Project `loop-engineering` は、Loop Engineering専用Repositoryの設計・実装・調査・検証・統合・管理を一元管理する。

運用思想は`ai-liver-yura` Project #7を引き継ぐが、AI Liver製品側の`v2` label、Project #6 / #7番号、製品固有Areaをそのまま専用側の正本にしない。

## 2. 管理対象

- Loop Engineeringの設計
- 制御系・ホスト実行系の実装
- GitHub / CI / Reviewer / PostgreSQL等の接続境界
- E2E・復旧・実環境確認
- 自己改善
- Repository運用・移行・監査

## 3. Issue階層

- `Parent`
- `Work`
- `Integration`
- `Management`

1 Work = 原則1能動実装作業系列とする。

## 4. Status

- `Backlog`: 作業候補。着手条件未成立を含む
- `Ready`: 仕様・責任範囲・依存関係が整理され着手可能
- `In progress`: 設計・実装・調査・自動試験等を進行中
- `Review`: PR / Design / Implementation result等を確認中
- `Verification`: 実環境・人間確認待ち
- `Blocked`: 依存作業・判断・権限・外部サービス・環境待ち
- `Done`: 受け入れ条件、自動検査、必要な実動作確認、Review、mergeまで完了

Verification FAIL時は`In progress`へ戻し、修正後に必要なら再び`Verification`へ送る。

## 5. Priority

- `P0`: Loop Engineering成立・主要安全境界に不可欠
- `P1`: 主要機能・品質に必要
- `P2`: 比較的低優先の改善
- `P3`: 任意・後回し可能な改善

## 6. 必須Project field

- Status
- Priority
- Area
- Issue level
- Start date
- Target date

利用可能なら次も使用する。

- 作業種別
- 工程
- Iteration
- Quarter
- 担当ロール

field ID / option ID / item IDを保存値から推測しない。変更前にlive取得し、変更後に効果を再取得する。

## 7. 日程

`Ready` / `In progress`へ進むIssueには`Start date` / `Target date`を設定する。

日程は計画情報であり、品質Gateを緩める理由にしない。GitHub Project `loop-engineering` の`Start date` / `Target date`を計画日程の正本とし、Issue本文へ同じ予定日を重複記載しない。

既存Issue本文に残る予定日は履歴・移行証拠としてのみ扱い、現在計画のAuthorityに使用しない。

## 8. Verification

実環境確認が必要な作業を、自動試験成功だけでDoneにしない。

例:

- GitHub実権限
- Projects v2実操作
- Codex CLI
- Reviewer broker
- PostgreSQL
- OS / filesystem / process境界
- 実Repository pilot

必要な場合は`Verification`を経る。

## 9. 安全なProject操作

1. Project owner / identityをlive確認する。
2. field ID / option IDを変更前にlive再取得する。
3. existing item duplicateを確認する。
4. mutation後にlive readbackする。
5. Project都合だけでIssue本文、Assignees、Milestone、branch、PR stateを変更しない。
6. source code / merge stateをProject整備のついでに変更しない。
7. APIや接続手段で確認できないView / Workflow設定を確認済み扱いしない。

## 10. GitHub / ChatGPT / Codex作業報告

最低限次を記録する。

- 対象Issue / PR
- Current Status
- active lineage
- base / exact HEAD
- 実施変更
- 検証結果
- 人間実動作確認の要否
- 残作業 / 判断

## 11. Branch / merge

`docs/architecture/v2/branch_lifecycle_and_commit_hygiene.md`を正本とする。

- 通常開発を`main`へ直接pushしない。
- 通常mergeはmerge commit方式。
- squash / rebase mergeを通常運用にしない。
- merge前に期待HEADを固定する。
- merge後のsource branchを再利用しない。
- force push / rebaseによる共有履歴破壊を原則禁止する。

## 12. 文章言語

`docs/GITHUB_OPERATION_RULES.md`、`docs/REPOSITORY_RULES.md`、`docs/architecture/v2/commit_message_language_policy.md`に従う。

人間向け文章とcommit messageは日本語を基本とし、機械識別子・固定値は維持する。
