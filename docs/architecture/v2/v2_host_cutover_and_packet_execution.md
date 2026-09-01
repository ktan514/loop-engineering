# V2 Host切替・作業パケット実行仕様

管理Issue: #67

上位正本:

- `v2_adapters_cutover_and_acceptance.md`
- `work_recovery_algorithm.md`
- `work_state_and_issue_boundary.md`

状態: 製造仕様

## 1. 目的

V2の最後の製造工程として、旧actual-hostと分離した明示的なV2 Host入口、旧状態からの切替、作業パケットの明示発行、1回だけの外部effect実行、停止後の復元、確定後のCheckpoint・outbox処理を一意に定義する。

V2 HostはIssue本文・comment・会話から現在Work、PR、HEAD、次遷移を推測しない。現在の実行状態はPostgreSQLを正本とし、Issue / Projectは作業定義、GitHub等の外部提供元は記録済みeffectの読戻しだけを担当する。

## 2. CLI境界

V2へ到達できる入口は次の明示操作だけとする。

```text
--migrate-v2-work-state <issue-number>
--issue-v2-packet <work-identity>
--v2-once <work-identity>
```

`--once`と引数なしの継続実行は旧Host入口であり、V2へ暗黙委譲しない。

V2へ切替済みのRepositoryでは旧Host入口を拒否する。切替済みかどうかはPostgreSQLのRepository単位cutover記録を正本とし、Issue commentや設定ファイル上の自由文を判定に使用しない。

`--v2-once`はsleep、polling、複数Work選択、別Workへの移動を行わず、指定Workについて最大1つの作業パケット遷移だけを処理して終了する。

## 3. V2切替記録

versioned migrationで`loop_v2_cutovers`を追加する。

```text
repository PRIMARY KEY
cutover_at
```

`--migrate-v2-work-state`が対象Issueの型付き定義を正常に同期し、WorkRecord候補をDBへ確定するとき、同じDB transactionでRepositoryのcutover記録も作成する。

cutover記録が作成された後は、V2 Workが失敗・待機・未完了でも旧Hostへfallbackしない。

移行処理は次を行わない。

- 旧Checkpoint自然文の解析
- PR / branch / HEADの推測
- 次遷移の推測
- effect意図の推測
- 作業パケットの自動発行

移行成功直後のWorkは`PLANNED`で、作業パケットとCheckpointを持たない。したがって`--v2-once`は作業パケットが明示発行されるまで変更を開始できない。

## 4. 作業パケットの実行計画

既存`WorkTaskPacket`へ、外部effectを実行するための型付き計画を追加する。

```text
effect_kind
effect_target_identity
effect_idempotency_key
expected_preconditions
expected_effect
```

新規の実行可能packetではすべて必須とする。旧行でこれらが欠落する場合は自動補完せず、V2 Hostは変更を開始しない。

`effect_idempotency_key`はpacket generationを含むpacket計画から決定論的に生成する。同じgenerationで別effectを再発行してはならない。新しいeffectが必要な場合は新generationのpacketを明示発行する。

対応するeffect kindは#66の読戻し契約と一致させる。

- `PUSH`
- `READY`
- `MERGE`
- `ISSUE_UPDATE`

`REPORT`は作業packet effectに含めず、outbox publisherだけが扱う。

## 5. packet明示発行

`--issue-v2-packet <work-identity>`は、既に移行済みのWorkだけを対象とする。

必要な型付き入力:

- generation
- transition
- effect kind
- target identity
- expected preconditions
- expected effect
- 必要ならcanonical design identity

CLIでは次を使用する。

```text
--v2-generation <positive-integer>
--v2-transition <value>
--v2-effect-kind <PUSH|READY|MERGE|ISSUE_UPDATE>
--v2-target <typed-identity>
--v2-before <key=value>  # repeatable
--v2-after <key=value>   # repeatable
--v2-design <identity>   # repeatable
```

HostはDB上の最大generationから暗黙に`+1`しない。generation省略ではpacket発行を開始しない。同じWork identity・同じgeneration・同じ型付き計画の再実行は同一packet identityへ収束させ、異なる計画なら競合として拒否する。詳細は`v2_packet_generation_issuance_contract.md`を正本とする。

packet発行前に対象WorkをDBから読み、Issue / Projectの型付き定義を再同期する。依存未完了、Issue closed、受入条件digest欠落、identity競合では発行しない。

発行は単一DB transactionで次を確定する。

1. 明示されたgenerationが既存packetと競合しない。
2. 対象Workに`INTENT_RECORDED` / `UNCERTAIN` effectがない。
3. 対象Workに有効な実行leaseがない。
4. `ISSUED` packetをINSERTする。
5. `SAFE_POINT` CheckpointをINSERTする。
6. WorkRecordのlatest packet / checkpoint pointerを同時更新する。

途中状態を残さない。

## 6. packet開始transaction

V2ResumeCoordinatorが`READY`を返せるのは、DBのlatest packetが`ISSUED`で、対応する`SAFE_POINT` Checkpointが存在し、未確定effectがない場合だけとする。

外部effect直前に、既存packetを新規INSERTし直さない。単一DB transactionで次を行う。

1. Work / packet / Checkpoint pointerが直前の復元結果と一致することを確認する。
2. packet statusが`ISSUED`であることを確認する。
3. 未確定effectがないことを確認する。
4. Work leaseを取得する。
5. packetを`STARTED`へ更新する。
6. packetに保存済みの計画と完全一致する`INTENT_RECORDED` EffectAttemptをINSERTする。
7. `EFFECT_PENDING` CheckpointをINSERTする。
8. WorkRecordのlatest Checkpoint pointerを更新する。

lease競合、一意制約競合、pointer不一致では文全体を失敗させ、外部effectを呼ばない。

このtransactionは従来の`issue_packet_transaction`の責務を「新規packet作成」から「明示発行済みpacketの開始」へ修正する。新規発行は5節の専用transactionへ分離する。

## 7. Resume判定

`V2ResumeCoordinator`はpacket statusを含めて次を返す。

| DB状態 | 判定 | 外部effect |
| --- | --- | --- |
| `ISSUED` + `SAFE_POINT` + pending effectなし | `READY` | 1回だけ開始可能 |
| `STARTED` + pending effectあり | target限定readback | 再送禁止 |
| `STARTED` + pending effectが`CONFIRMED` / `NO_EFFECT`へ確定 | `FINALIZE_REQUIRED` | 新規effectなし |
| `STARTED` + pending effect読戻し不能 | `RECONCILE_REQUIRED` | 再送禁止 |
| `COMPLETED` / `SUPERSEDED` | `WAITING(PACKET_TERMINAL)` | 新規effectなし |
| `UNCERTAIN` packet | `RECONCILE_REQUIRED` | 新規effectなし |

readbackが`UNKNOWN`ならEffectAttemptを`UNCERTAIN`へ確定して停止する。同じidempotency keyを再送しない。

`STARTED` packetでpending effectが既にDB上から消えている場合も、packetを`READY`として再実行しない。terminal effectを確認して`FINALIZE_REQUIRED`へ進むか、証明できなければ`RECONCILE_REQUIRED`とする。

## 8. 外部effect実行

専用`V2EffectExecutorPort`を使用し、旧Host implementerを呼ばない。

実行順序:

```text
DB start transaction
→ target限定readbackでexpected_preconditions一致を証明
→ Write Gateの変更前判定
→ effectを1回実行
→ target限定readback
→ DB effect outcome
```

既存Write Gateは変更前判定と変更後effect照合を分離する。互換用`validate()`は維持し、V2は変更前専用`validate_preconditions()`を使用する。実際の変更後照合は#66の`EffectReadbackPort`を正本とする。

外部実行は記録済みtargetだけを使用する。

- `PUSH`: 期待後HEADを明示して対象branchへpushする。trunk直接pushは禁止する。
- `READY`: 対象PRだけをReadyへ変更する。
- `MERGE`: 対象PRと期待HEADを固定してmerge commit方式で統合する。
- `ISSUE_UPDATE`: 対象Issueの許可fieldだけを1回のGitHub API更新で変更する。

外部commandが失敗した場合でも同じeffectを再実行しない。直後のreadbackで結果を確認し、証明不能なら`UNCERTAIN`とする。

## 9. effect確定後のpacket finalization

`CONFIRMED`または`NO_EFFECT`を記録した後、packetを`STARTED`のまま残して次回`READY`扱いしてはならない。

専用finalization transactionで次を行う。

- `CONFIRMED` → packet `COMPLETED`
- `NO_EFFECT` → packet `SUPERSEDED`
- terminal effectのpacket generation一致を確認
- terminal CheckpointをINSERT
- WorkRecordのlatest Checkpoint pointerを更新

Checkpoint kindは次とする。

- `CONFIRMED`: `EFFECT_CONFIRMED`
- `NO_EFFECT`: `EFFECT_NO_EFFECT`

`NO_EFFECT`は再送許可ではない。新しい外部effectが必要なら新generation packetを発行する。

finalizationは同一Work leaseの保持者だけが行う。停止復元時に旧leaseが残っている場合、pending effectを先にreadbackしてterminal outcomeへ確定した後にだけ、期限切れleaseを新holderへ引き継げる。

## 10. lease寿命

Work leaseは外部effect開始からpacket finalization、outbox生成・投稿まで保持する。

正常終了時は明示releaseする。command失敗や`UNCERTAIN`で安全終了する場合も、現在processが保持するleaseはreleaseしてよい。未確定effect自体が新しいpacket開始をDBで拒否するため、lease解放を再送許可として扱わない。

process crashでleaseが残った場合は期限切れまで保持される。pending effectのreadback前に新effectを開始しない。

## 11. outboxと停止復元

terminal Checkpoint確定後、同Checkpointから決定論的なreport identityを生成してoutboxへenqueueする。

report本文は次だけを含む。

- packet遷移がDBへ確定したこと
- effect結果が`CONFIRMED`または`NO_EFFECT`であること
- 次packetは明示発行が必要であること

DB内部identity、秘密値、無加工診断を本文へ出さない。

outbox publisherはWork lease保持中だけ呼ぶ。finalization後、outbox enqueue前または投稿前に停止した場合、次回はlatest terminal Checkpointから同じlogical reportを再生成し、effectなしで投稿だけを再試行できる。

## 12. migration結果とpacket発行の分離

`--migrate-v2-work-state`はWorkRecordとcutoverだけを作り、packetを作らない。

`--issue-v2-packet`は既存Workへpacketを明示発行する。

`--v2-once`は既存packetの実行・復元だけを行い、新しいpacketを自動発行しない。

この3段階を統合しないことで、旧自然文から次effectを推測する経路を作らない。

## 13. 受入試験

最低限次を自動試験で固定する。

1. migrationがIssue / Project typed definitionだけからWorkRecordとcutoverを作り、packetを作らない。
2. cutover後の`--once` / continuous旧入口が変更前に拒否される。
3. 明示generation付きpacket発行が`ISSUED + SAFE_POINT + pointer`を1 transactionで確定し、generation省略や同一generationの別計画を拒否する。
4. packet開始が既存`ISSUED`を`STARTED`へ更新し、lease / effect intent / Checkpointを1 transactionで確定する。
5. lease競合、pointer競合、pending effect、一意制約競合で外部effect 0回。
6. `--v2-once`が指定Work以外を読まず、1 packet・1 effectを超えない。
7. effect command失敗後も同じeffectを再送せずreadbackだけを行う。
8. `INTENT_RECORDED` / `UNCERTAIN`復元時はreadbackだけを行う。
9. readback `CONFIRMED`でpacket `COMPLETED`、`NO_EFFECT`で`SUPERSEDED`となる。
10. `STARTED` terminal outcomeを再起動しても同じpacketを再実行しない。
11. terminal Checkpoint後に停止してもoutboxだけを再生成・投稿できる。
12. Project/Issue commentへ別PR・HEADを書いても指定WorkとDB packetは変化しない。
13. Ruff、strict Mypy、全pytest、compileall、diff-checkがexact HEADでPASSする。

## 14. 非対象

- 複数Workの自動スケジューリング
- sleep / pollingを含むV2 continuous runner
- Issue本文・commentの意味解析
- 旧actual-hostへのfallback
- Project #10の設定同期
- Repository branch protectionの管理者設定
