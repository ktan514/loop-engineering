# Runtime Operational State / Restart Reconciliation

管理Issue: #44
関連: #27 / #46
状態: canonical architecture

## 1. 目的

PostgreSQL Operational Storeを、Loop EngineeringのHost run、遷移履歴、実行排他、再起動復旧、重複副作用防止のために使用する。

この文書はGitHub current-state Authorityを変更しない。Issue、PR、Project、branch、exact HEAD、CI、review、Mission Checkpointの現在状態はfresh GitHub readを正とする。PostgreSQLは過去の運用効果と未確定状態を保持するだけで、DBだけからcurrent Workやmerge可否を決定しない。

## 2. 1 Host遷移の永続境界

各`run_actual_host_transition()`は1つのdurable runとして扱う。

開始順序:

1. 前回の未完了runをPostgreSQLからreadbackする。
2. 新しい`run_id`を生成し`loop_runs`へ`RUNNING`として記録する。
3. GitHubからcurrent targetをfresh readする。
4. 前回未完了runがある場合は、そのrunが記録した観測Checkpointとfresh targetを比較してreconcileする。
5. current targetの最小snapshotを`loop_checkpoints`へ記録する。
6. project単位execution leaseを取得する。
7. Host controllerを1遷移だけ実行する。
8. 結果を`loop_transitions`へ記録し、必要に応じてblocker / external waitを更新する。
9. runをterminal statusへ更新しleaseを解放する。

通常終了経路ではrunとleaseを必ずterminalへする。プロセス異常終了では`RUNNING` / `ACTIVE`が残り、次回起動時のreconcile対象になる。

## 3. Restart reconcile

前回runが`RUNNING`のまま残っている場合、Loop Engineeringは副作用が発生しなかったと推定しない。

前回runに記録されたCheckpoint snapshotとfresh GitHub targetを比較する。

### 3.1 GitHub側が前進している場合

次のいずれかが変化していれば、DB上の前回状態はstale operational stateとしてreconcileできる。

- Mission Checkpoint comment identity
- current Work
- current PR
- exact HEAD

fresh GitHub stateを採用し、前回runを`RECONCILED`として閉じ、古いleaseを解放して現在runを継続する。

### 3.2 同一Checkpointのままの場合

前回runが`RUNNING`で、fresh GitHub targetが前回snapshotと同一である場合、副作用の成否は確定できない。

この場合はCodex実行、GitHub write、merge等を再送しない。`OPERATIONAL_STATE_UNCERTAIN`として型付きInterventionへ遷移し、blockerを永続化する。

前回runに観測Checkpoint自体が無い場合も同様に未確定として扱う。

## 4. Execution lease

project単位に1つのlease identityを使用する。

```text
host-transition:<project-key>
```

leaseは現在runの`run_id`をholderとする。

- `ACTIVE` leaseが無い場合だけ取得できる。
- 正常終了時に`RELEASED`へ更新する。
- 異常終了で残ったleaseは、restart reconcileで前回runを安全にstaleと判定できた場合だけ解放する。
- 同一Checkpointで未確定の場合は自動解放して再実行しない。

leaseはGitHub Authorityの代替ではなく、同一Host/DBを共有する実行の重複抑止である。

## 5. 保存する状態

### loop_runs

- run identity
- project key / repository
- `RUNNING` / `COMPLETED` / `YIELD_EXTERNAL` / `INTERVENTION_REQUIRED` / `RECONCILED`
- secret-safe metadata

### loop_checkpoints

fresh GitHub観測から次だけ保存する。

- run identity
- project key
- Mission Checkpoint comment identity
- work issue
- PR number
- exact HEAD

Issue/PR本文やMission Goal本文そのものは保存しない。

### loop_transitions

Host transition結果の型付き値だけ保存する。

- status
- detail code
- work issue
- PR number
- exact HEAD

### loop_blockers / loop_external_waits

`INTERVENTION_REQUIRED`はblocker、`YIELD_EXTERNAL`はexternal waitとして記録する。後続runで同一targetが解消した場合はresolvedへ更新する。

## 6. Secret safety

DB操作は#46で導入したPostgreSQL command adapterを通す。

- DSN passwordをargvへ含めない。
- token、API key、Authorization、provider raw payload、prompt、diffを保存しない。
- SQLへ埋め込む値は上限付きの内部identity / status / detail / GitHub numeric identity / exact HEADに限定し、SQL literal escapingを必須とする。

## 7. required / optional policy

`required = true`では、run開始後のOperational State read/write/lease失敗もfail-closedする。DBへ永続化できない状態で副作用を開始しない。

`required = false`ではDB unavailableをtyped degraded pathとして扱える。ただしDB-backed leaseや未確定副作用判定が必要な箇所では再送よりyield/interventionを優先する。

Yuraローカル実運転は`required = true`を使用する。

## 8. 完了検証

#44後半の完了条件:

1. Host遷移開始・結果がPostgreSQLへ実記録される。
2. execution leaseが取得・解放される。
3. `YIELD_EXTERNAL` / `INTERVENTION_REQUIRED`がdurable stateへ反映される。
4. 異常終了runを次回起動でreadbackできる。
5. fresh GitHub targetが前進済みならstale DB stateをreconcileして続行できる。
6. 同一Checkpointで副作用成否不明なら再送せず`OPERATIONAL_STATE_UNCERTAIN`になる。
7. DB read/write failureは`required = true`でCodex/GitHub mutation開始前にfail-closedする。
8. pytest / Ruff / strict Mypy / compileall / diff-check / exact-head CIがPASSする。
