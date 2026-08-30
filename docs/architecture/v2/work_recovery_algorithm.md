# V2作業復元アルゴリズム

管理Issue: #62

状態: 再設計正本

## 1. 目的

V2は、Issue comment、会話、古い外部観測から現在作業を推測しない。停止後の再開はDBの安全Checkpointを唯一の起点とし、Issueは課題定義、外部提供元は効果の確認だけを担う。

この文書は`work_state_and_issue_boundary.md`の責務境界を、実行可能な決定アルゴリズムへ具体化する。

接続層、Host合成、切替、受入試験は`v2_adapters_cutover_and_acceptance.md`を正本とする。

## 2. 入力と出力

1回の再開判定の入力は次だけである。

| 入力 | 用途 |
| --- | --- |
| DB作業記録 | 選択済みWork、作業パケット、最後の安全Checkpoint、lease、effect試行、報告outbox |
| Issue / Project定義 | 目的、受入条件、依存関係、優先度、完了判断の同期 |
| effect対象の外部読戻し | DBに`INTENT_RECORDED`または`UNCERTAIN`として残る対象だけの確認 |

出力は`ResumeDecision`である。

```text
READY(packet)
RECONCILE_REQUIRED(effect identities)
WAITING(reason)
BLOCKED(reason)
COMPLETED
```

`READY`以外は新しい外部変更を開始しない。`COMPLETED`はIssue closedかつDB Workが`COMPLETED`であり、未確定effectの読戻し後にだけ返す終端状態である。

## 3. データモデル

`WorkRecord`はIssue identityと最後に同期したIssue revision、作業ライフサイクル、現在の作業パケット、最後の安全Checkpointを持つ。`TaskPacket`はgeneration、遷移、設計identity、外部対象identity、事前条件を持つ。`WorkCheckpoint`は作業パケットに対応する安全な再開地点だけを持つ。

effectは次の状態機械を必須とする。

```text
INTENT_RECORDED
  ├─ 外部効果の読戻し成功 → CONFIRMED
  ├─ 外部効果なしの読戻し成功 → NO_EFFECT
  └─ 読戻し不能または曖昧 → UNCERTAIN
```

`UNCERTAIN`から`INTENT_RECORDED`へ戻す遷移は禁止する。新しいeffectは、異なるidempotency keyを持つ新しい作業パケットだけが要求できる。

## 4. 再開アルゴリズム

```text
1. DBからWorkRecordと最新安全Checkpointを復元する
2. DB migration世代とCheckpoint generationを検証する
3. Work単位leaseを原子的に取得する
4. Issue / Projectの作業定義を同期する
5. 定義identity、revision、依存関係、完了判断を照合する
6. 未確定effectだけを外部提供元から読戻す
7. ResumeDecisionを確定する
8. READYの場合だけ作業パケットを1回実行する
9. DBへ新しい安全Checkpointを確定する
10. 確定済みCheckpointからIssue報告outboxを生成する
```

### 4.1 DB復元

DBにWorkRecordまたは安全Checkpointが無い場合、過去Issue commentへ戻らない。`BLOCKED(WORK_RECOVERY_MISSING)`として、Issueから新規Workを明示選択する処理だけを許可する。

`READY`を返すには、`WorkRecord.latest_task_packet_identity`が指す作業パケットと`WorkRecord.latest_checkpoint_identity`が指す安全Checkpointの両方が存在し、同じWorkを参照し、Checkpointの`task_packet_identity`がその作業パケットを指していなければならない。時刻上もっとも新しい別Checkpointをpointerの代わりに採用してはならない。pointer欠落、参照先欠落、Work不一致、作業パケット不一致のいずれかがある場合は`BLOCKED(WORK_RECOVERY_MISSING)`とし、新しい外部変更を開始しない。

Checkpointが旧schemaの場合、対応migrationを適用した明示移行処理へ渡す。任意の列欠落を空値で補うことは禁止する。

### 4.2 定義同期

Issue / Projectから同期するのは、Issue identity、open/closed、受入条件revision、依存関係、優先度、Project計画値だけである。本文・commentの自然文から`current Work`、PR、HEAD、次遷移を抽出しない。

DBの未完了WorkとIssueの定義が競合する場合は、DBまたはIssueのどちらかを暗黙上書きしない。`BLOCKED(WORK_DEFINITION_CONFLICT)`として、明示的な再調整決定を要求する。

### 4.3 effect読戻し

読戻し対象はDBのeffect試行に記録された`kind`と`target_identity`に限定する。例としてmergeはPR番号・base・期待HEAD、pushはbranch・期待HEAD、Issue更新はIssue番号・期待revisionを読む。

対象外のPR、branch、Issue本文、過去作業系列を探索して現在対象を推測してはならない。

### 4.4 作業パケット実行

`READY`の作業パケットは1回だけ実行する。外部変更前に必ず次をDBへ同一transactionで確定する。

```text
packet status = STARTED
effect status = INTENT_RECORDED
effect idempotency key
期待する外部対象identity
```

外部効果後はreadbackが`CONFIRMED`になるまで、後続の依存effectや完了遷移へ進まない。プロセス停止時は`INTENT_RECORDED`または`UNCERTAIN`が残り、次回は第6段階から再開する。

## 5. crash matrix

| 停止地点 | DB状態 | 次回の扱い |
| --- | --- | --- |
| effect前 | 安全Checkpoint | 同じpacketを実行可能 |
| effect意図の確定後、effect前 | `INTENT_RECORDED` | 対象を読戻し、再送しない |
| effect後、読戻し前 | `INTENT_RECORDED` | 対象を読戻し、確認不能なら`UNCERTAIN` |
| 読戻し成功後、Checkpoint前 | `CONFIRMED` | DBから次のCheckpointを再構成 |
| Checkpoint後、Issue報告前 | outbox `PENDING` | effectを再実行せず報告だけを再送 |

## 6. Issue報告

Issue報告はDBの確定済みCheckpointからだけ生成する。outbox identityは`work identity + checkpoint identity + report kind`とし、同じ報告を重複投稿しない。

報告の失敗は`PENDING`のまま保持する。DBのWork、effect、Checkpointを巻き戻さず、次回は報告だけを再試行できる。

## 7. 不変条件

- Issue commentは機械的な再開入力ではない。
- DBに記録されないeffectを実行しない。
- `UNCERTAIN` effectを再送しない。
- DBの作業状態をIssue自然文で上書きしない。
- Issueの目的・受入条件・完了判断をDBの過去値で上書きしない。
- leaseを持たない実行者はeffectを開始しない。
- 1作業パケットは1つの安全Checkpoint generationへだけ進む。
