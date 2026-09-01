# Loop Engineering GitHub Project運用規則

## 1. 対象

- Owner: `ktan514`
- Repository: `ktan514/loop-engineering`
- GitHub Project: `loop-engineering`
- 管理対象: Loop Engineering本体の設計、実装、検証、統合、管理、自己改善

この文書は`ai-liver-yura`のProject #7運用、Issue #207、Issue #384、既存Repository規約からLoop Engineering専用Repositoryへ引き継いだ運用規則を定義する。

## 2. 現在状態の正本

現在状態は次を優先する。

1. GitHub live Issue / PR / branch / commit SHA / Actions
2. 対象Issueの最新Resume Checkpoint
3. GitHub Project `loop-engineering` のlive field
4. Repository正本文書
5. chat transcript
6. summary / memory

summary / memoryは候補発見だけに使用し、現在Issue、PR、branch、SHA、Status、次作業を確定する正本にしない。

## 3. Issue階層

- `Parent`: 複数Work / Integrationを束ねる完成目標
- `Work`: 独立した設計・実装責務
- `Integration`: E2E・実環境結合確認
- `Management`: 運用、移行、監査、Repository管理

1つのWork Issueには原則として1本の能動実装作業系列だけを持つ。

## 4. 必須Project項目

Yura Project #7と同じ考え方で最低限次を管理する。

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

field ID、option ID、Project item IDを保存値から推測して変更しない。変更直前にliveで解決し、変更後に効果を再取得する。

## 5. Status

- `Backlog`: 候補。着手条件未成立を含む
- `Ready`: 仕様、責任範囲、依存関係が整理され着手可能
- `In progress`: 設計、実装、調査、自動試験等を進行中
- `Review`: PR、設計、実装結果等を確認中
- `Verification`: 実環境・人間確認待ち
- `Blocked`: 依存、判断、権限、外部環境待ち
- `Done`: 受け入れ条件、検証、必要な確認、統合まで完了

実動作確認が必要なWorkを`Review`から直接`Done`へ進めない。

## 6. 日程

`Ready`または`In progress`へ進むIssueには`Start date`と`Target date`を設定する。

日程は計画情報であり、品質Gateを緩める根拠にしない。GitHub Project `loop-engineering` の`Start date` / `Target date`を計画日程の正本とし、Issue本文へ同じ予定日を重複記載しない。

既存Issue本文に残る予定日は履歴・移行証拠としてのみ扱い、現在計画のAuthorityに使用しない。

## 7. Branch / PR

- 通常開発を`main`へ直接commitしない。
- 設計: `design/<topic>`
- 実装: `feature/<topic>` / `fix/<topic>`
- 管理: `management/<topic>`
- 同期・移行: `sync/<topic>`
- 検証専用: `test/<topic>`
- force push / rebaseによる共有作業系列の履歴破壊を行わない。
- 通常統合はmerge commit方式を使用する。
- merge前にcurrent PR HEADを再取得し、期待HEADを固定する。
- merge後はsource branchを再利用しない。

詳細は`docs/architecture/v2/branch_lifecycle_and_commit_hygiene.md`に従う。

## 8. 設計先行

コードまたは運用挙動を変更するWorkは、実装前に対象のRepository正本設計を更新する。

単純な翻訳では仕様・挙動を変更しない。翻訳中に機能不具合を発見した場合は、意味のある機能修正として明示する。

## 9. 厳密HEAD

CI、レビュー、統合証拠はcurrent exact HEADへ結び付ける。

HEAD変更後に旧CI・旧reviewを現在のPASSとして流用しない。Ready化・merge直前にもPR / base / head / mergeabilityを再取得する。

## 10. Review / Verification待ち

review待ちだけをMission STOPにしない。同一exact HEADへの同一レビュー要求を重複実行せず、独立して依存関係を満たしたWorkがあればfresh Resume Gate後に進める。

人間確認が必要なWorkは`Verification`で待機し、他の独立Workを止めない。

## 11. Resume Gate

新しいチャット、長時間中断、作業系列変更、base/head説明不能な変化の後は、コード・branch・PR変更前に次を照合する。

- 運用ハブIssue
- 対象Issue
- Repository正本設計
- GitHub live Issue / PR / branch / base / head
- 最新Resume Checkpoint
- competing lineage

競合がある場合は実装せず再調整する。PASS時だけResume Certificateを提示して作業を再開する。

## 12. 文章言語

`docs/GITHUB_OPERATION_RULES.md`、`docs/REPOSITORY_RULES.md`、`docs/architecture/v2/commit_message_language_policy.md`をRepository文章言語の正本とする。

人間向け文章は日本語を基本言語とし、必要な英語技術語は自然な日本語の意味を先に表し、必要な場合だけ原語を括弧内へ併記する。機械識別子、status値、command、path、branch、SHA、class/function/field名、machine-readable値は維持してよい。

コミットメッセージも日本語を主要言語とする。

## 13. Project変更の安全境界

- Project identityをlive確認する。
- field / option / item identityを変更直前にlive取得する。
- 重複itemを確認する。
- 変更後にlive readbackする。
- Project整備だけを理由にIssue本文、PR、Assignees、branch、source codeを勝手に変更しない。
- 接続手段から確認できないView / Workflow設定を確認済み扱いしない。

## 14. 完了

commit、push、Draft PR作成、自動試験の一部PASSだけではDoneにしない。対象Workの受け入れ条件、必要な静的・動的検証、Review / Verification、merge、main readbackを満たした場合だけ完了とする。
