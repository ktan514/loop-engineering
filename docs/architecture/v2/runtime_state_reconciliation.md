# Runtime Operational State / Restart Reconciliation

管理Issue: #44
関連: #27 / #46
状態: canonical architecture

## 1. 目的

PostgreSQL Operational Storeを、Loop EngineeringのHost run、遷移履歴、実行排他、再起動復旧、重複副作用防止のために使用する。

この文書はGitHub current-state Authorityを変更しない。Issue、PR、Project、branch、exact HEAD、CI、review、Mission Checkpointの現在状態はfresh GitHub readを正とする。PostgreSQLは過去の運用効果と未確定状態を保持するだけで、DBだけからcurrent Workやmerge可否を決定しない。

## 2. 1 Host遷移の永続境界

各Host遷移は1つのdurable runとして扱う。

開始順序:

1. 前回の未完了runをPostgreSQLからreadbackする。
2. GitHubからcurrent targetをfresh readする。
3. 前回未完了runがある場合は、そのrunが記録した観測Checkpointとfresh targetを比較してreconcileする。
4. 現在runの`run_id`を生成し`loop_runs`へ`RUNNING`として記録する。
5. current targetの最小snapshotを`loop_checkpoints`へ記録する。
6. project単位execution leaseを取得する。
7. Host controllerを1遷移だけ実行する。
8. 結果を`loop_transitions`へ記録し、必要に応じてblocker / external waitを更新する。
9. runをterminal statusへ更新しleaseを解放する。

未完了runを安全にreconcileできない場合は新しいrun自体を開始しない。通常終了経路ではrunとleaseをterminalへする。プロセス異常終了では`RUNNING` / `ACTIVE`が残り、次回起動時のreconcile対象になる。

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

### 3.3 terminal transitionだけ記録済みの場合

`loop_transitions`にはterminal結果があるが`loop_runs`が`RUNNING`のままなら、外部副作用の結果は既に型付きで永続化済みである。runをそのterminal statusへ閉じ直し、残存leaseを解放してfresh GitHub stateから続行する。

## 4. Execution lease

project単位に1つのlease identityを使用する。

```text
host-transition:<project-key>
```

leaseは現在runの`run_id`をholderとする。

- `ACTIVE` leaseが無い場合だけ取得できる。
- 正常終了時に`RELEASED`へ更新する。
- 異常終了で残ったleaseは、restart reconcileで前回runを安全にstaleまたはterminalと判定できた場合だけ解放する。
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

`INTERVENTION_REQUIRED`はblocker、`YIELD_EXTERNAL`はexternal waitとして現在runへ関連付けて記録する。

後続runで新しいterminal operational stateを確立できた場合、そのProject / Repositoryに属する過去runの`OPEN` blocker / external waitを`RESOLVED`へ更新した後、現在runが必要とする新しい`OPEN`状態だけを記録する。これにより履歴を削除せず、現在有効な待機・blockerを判別できる。

## 6. Secret safety

DB操作は#46で導入したPostgreSQL command adapterを通す。

- DSN passwordをargvへ含めない。
- token、API key、Authorization、provider raw payload、prompt、diffを保存しない。
- SQLへ埋め込む値は上限付きの内部identity / status / detail / GitHub numeric identity / exact HEADに限定し、SQL literal escapingを必須とする。

## 7. required / optional policy

`required = true`では、run開始後のOperational State read/write/lease失敗もfail-closedする。DBへ永続化できない状態で副作用を開始しない。

外部副作用後のDB commitが確定できない場合は`OPERATIONAL_STORE_COMMIT_UNCERTAIN`とし、安全側へ停止する。

`required = false`ではDB unavailableをtyped degraded pathとして扱える。ただしDB-backed leaseや未確定副作用判定が必要な箇所では再送よりyield/interventionを優先する。

Yuraローカル実運転は`required = true`を使用する。

## 8. Product非変更の実機確認

Runtimeを実際にYuraへdispatchする前に、次を実行できる。

```bash
pipenv run python -m loop_engineering --operational-state-check
```

この確認はGitHub API mutation、Codex、Product Workspace変更を行わず、`loop_runs`へsynthetic runを書込み、`RUNNING` readback、`COMPLETED`更新、terminal readbackを確認する。

synthetic runの`project_key`は`health:<project-key>` namespaceへ分離する。確認途中で異常終了して`RUNNING`が残っても本番Projectのrestart reconcile対象にはならない。

期待結果:

```text
OPERATIONAL_STATE_CHECK=PASS detail=ROUND_TRIP_PASS
```

## 9. 完了検証

#44後半の完了条件:

1. Product非変更のround-trip checkでPostgreSQL write/readbackがPASSする。
2. Host遷移開始・結果がPostgreSQLへ実記録される。
3. execution leaseが取得・解放される。
4. `YIELD_EXTERNAL` / `INTERVENTION_REQUIRED`がdurable stateへ反映される。
5. 異常終了runを次回起動でreadbackできる。
6. fresh GitHub targetが前進済みならstale DB stateをreconcileして続行できる。
7. 同一Checkpointで副作用成否不明なら再送せず`OPERATIONAL_STATE_UNCERTAIN`になる。
8. DB read/write failureは`required = true`でCodex/GitHub mutation開始前にfail-closedする。
9. pytest / Ruff / strict Mypy / compileall / diff-check / exact-head CIがPASSする。
