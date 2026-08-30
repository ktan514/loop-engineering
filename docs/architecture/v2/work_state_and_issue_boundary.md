# 作業状態DBとIssueの責務境界

管理Issue: #62

状態: 再設計正本

## 1. 結論

Loop Engineeringは、GitHub IssueとPostgreSQLに同じ作業状態を正本として持たせない。

- Issueは課題と作業の元締めである。何を解決するか、受入条件、優先度、依存関係、設計判断、利用者へ知らせる状況報告を所有する。
- PostgreSQLは実行中の作業状態の永続正本である。作業環境が停止した後に、どの作業をどこまで実行し、何を確認してから再開するかを復元する。
- GitHubのPR、branch、CI、reviewなどの外部効果は、それぞれの提供元が正本である。DBはそれらを再実行するための対象identityと確認結果を保存するが、外部効果の値を推測してはならない。

従来の「最新Issue Checkpointを再開の入力にする」方式は廃止する。Issueの報告文は人間向けの出力であり、機械が再開対象を解決する入力ではない。

## 2. 所有権

| 情報 | 所有者 | 用途 |
| --- | --- | --- |
| 課題の目的、背景、受入条件、優先度、依存関係、完了判断 | Issue / Project | 作業の起票・選択・統括 |
| 設計判断と変更理由 | IssueとRepository正本文書 | 人間への情報展開と設計意図 |
| 進捗、停止理由、次の連絡事項 | Issue comment | 人間への状況報告 |
| 作業の選択結果、作業パケット、実行段階、再開地点 | PostgreSQL | 停止後の復元 |
| run、遷移、lease、dispatch、idempotency、blocker、外部待機 | PostgreSQL | 重複防止と安全な継続 |
| PR、branch、HEAD、CI、reviewの実効果 | 各外部提供元 | 変更前後の対象確認 |

Issueのopen/closed、Projectの優先度・依存関係はDBが上書きしない。反対に、DBの実行段階、lease、未確定副作用、作業パケットをIssue commentから復元・上書きしない。

## 3. DBの復元モデル

DBにはIssueごとの実行可能な作業記録を保持する。少なくとも次をversioned migrationで導入する。

```text
work_records
- work_key                         # repository identity + Issue identity
- issue_identity / issue_revision  # 起票元への参照と最後に確認した版
- lifecycle                        # PLANNED / SELECTED / RUNNING / WAITING / BLOCKED / COMPLETED
- selected_transition
- active_lineage_identity?
- latest_task_packet_identity?
- last_safe_checkpoint_identity?
- revision

task_packets
- identity / work_key / generation
- transition / preconditions / expected_effects
- canonical_design_identities
- external_target_identities
- status                            # ISSUED / STARTED / COMPLETED / SUPERSEDED / UNCERTAIN

work_checkpoints
- identity / work_key / run_identity
- checkpoint_kind                   # SAFE_POINT / EFFECT_PENDING / EFFECT_CONFIRMED / WAITING
- resumable_state
- next_action
- task_packet_identity
- external_target_identities
- evidence_identities

effect_attempts
- idempotency_key / work_key / kind
- target_identity / status          # INTENT_RECORDED / CONFIRMED / NO_EFFECT / UNCERTAIN
- request_identity? / confirmed_at?
```

本文、秘密情報、無加工のプロンプト、Issue/PR本文、差分、認証情報は保存しない。必要なのは参照identity、版、列挙値、上限付きの安全な説明コードだけである。

`work_records.revision`と各Checkpointのgenerationにより、同じ作業パケットを停止後に二重実行しない。DB更新と外部効果の間に異常終了した場合は、`UNCERTAIN`として残し、再送ではなく対象外部効果の照合へ進む。

## 4. 起動・再開手順

```text
Issue / Projectを観測
  → DBへ作業記録を作成または同期
  → DBから未完了の作業記録と最後の安全Checkpointを復元
  → 対象外部効果だけを再照合
  → 再調整
  → lease取得
  → 1遷移を実行
  → DBへCheckpointを確定
  → Issueへ人間向け報告を投影
```

再起動の最初の入力はDBである。Issue commentを探索して作業、PR、HEAD、次遷移を組み立ててはならない。

ただし、DBだけで外部変更を開始してはならない。次の場合だけ、DBが記録した対象identityを使って必要最小限の外部再照合を行う。

- PR操作・push・mergeの前: branch、base、HEAD、PR状態
- CI/reviewに依存する遷移の前: 記録した厳密HEADへの証拠
- Issue/Project変更の前: 対象Issueと変更予定field
- `EFFECT_PENDING`または`UNCERTAIN`: 当該effect identityの有無

外部照合が不能なら、DBの安全Checkpointを保持したまま`WAITING`または`BLOCKED`にし、同じ副作用を再送しない。

## 5. Issueとの同期

同期は一方向の役割を持つ。

1. IssueからDBへ、作業の定義と計画上の変更を取り込む。
2. DBからIssueへ、理解可能な進捗報告を投稿する。

報告には作業状態、実行済み段階、確認済み外部効果、待機理由、次の人間への連絡事項を含める。作業パケット全体、lease、DB内部ID、再実行鍵、機密性のある診断は投稿しない。

DBへの保存に失敗した遷移は、外部変更を開始しない。外部変更後にDB確定へ失敗した場合は`UNCERTAIN`を優先して安全側に停止し、復旧後に対象だけを照合する。Issueへの報告失敗はDBの実行状態を巻き戻さない。報告は再送可能なoutboxとしてDBに保持し、同一報告を重複投稿しない。

## 6. 移行

1. `loop_runs`、`loop_transitions`、`loop_checkpoints`を削除せず、既存記録を履歴として保持する。
2. 新しい作業記録、作業パケット、Checkpoint、effect試行、Issue報告outboxを追加する。
3. 現行の最新Issue Checkpointを一度だけ読取り、対応するDB作業記録の初期化候補として扱う。曖昧な対象は自動移行せず`BLOCKED`にする。
4. Host入口をDB復元→限定再照合へ切り替える。
5. Issue Checkpointを再開入力から外し、DB確定後の人間向け報告へ置き換える。
6. 停止後の復元、effect未確定、Issue投稿失敗、GitHub読取不能、DB障害、二重起動を結合試験で証明する。

## 7. 不変条件

- 作業環境の再開対象はDBの安全Checkpointから復元する。
- 課題の目的・受入条件・完了判断はIssueが所有する。
- Issue commentは再開の機械入力ではない。
- 外部効果はDBの記録だけで成功と判定しない。
- `UNCERTAIN`のeffectを自動再送しない。
- DB確定前に外部変更を開始しない。
- DBとIssueの不一致は片方で静かに上書きせず、同期競合として記録する。
- Issueへの状況報告はDB確定済み状態だけから作成する。
