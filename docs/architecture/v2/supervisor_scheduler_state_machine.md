# V2 Supervisor / Scheduler 状態機械

管理Issue: #84
親Issue: #81
依存: #83
上位正本: `autonomous_development_completion_contract.md`

状態: 製造仕様

## 1. 目的

Goal bootstrapでGitHubへ確立したWork群と、PostgreSQLのV2実行状態を入力に、current Workと次の安全なtransitionを自然文解析なしで決定する。

## 2. Authority

- Work目的・受入条件・依存・Project状態: GitHub Issue / Project
- current selected Work・packet・checkpoint・pending effect: PostgreSQL
- branch / PR / HEAD: GitHub live（#86で接続）
- CI / review / Human Verification evidence: exact target evidence（#87で接続）

bootstrap markerはGoal配下Workのplanning discoveryにだけ使用し、current Workやnext transitionの復元には使わない。

## 3. Work observation

Supervisorへ渡すprovider非依存snapshotを`V2WorkObservation`とする。

最低限:

- work identity / issue number / revision
- issue open/closed
- project status / priority
- dependency states
- acceptance digest
- canonical design identities
- active lineage identity
- exact head
- verification state
- review state
- Human Verification requirement/state
- merged state
- unresolved conflict

#84ではGitHub planning discoveryとDB current stateを接続し、branch/CI/review等の値は後続adapterが追加できる型として先に固定する。

## 4. 導出transition

優先順:

1. unresolved conflict → `BLOCKED`
2. dependency未完了 → wait-only
3. Workがterminal → `COMPLETE_WORK`
4. canonical designなし → `DESIGN`
5. implementation headなし → `IMPLEMENT`
6. verification failure → `REPAIR`
7. verification未実行 → `VERIFY`
8. verification pending → wait-only
9. review changes/failure → `REPAIR`
10. review未実行 → `REVIEW`
11. review pending → wait-only
12. Human Verification必要かつ未要求 → `HUMAN_VERIFY`
13. Human Verification pending → wait-only
14. Human Verification failure → `REPAIR`
15. required evidence PASS → `INTEGRATE`

後続#86/#87はこの状態機械を迂回せずevidenceを追加する。

## 5. Selection

1. PostgreSQL current Workがsafe/actionableなら継続。
2. current Workがwait-onlyなら、dependency-readyな別actionable Workを選ぶ。
3. priorityはP0→P1→P2→P3→未指定。
4.同順位はProject Statusの作業中を優先し、最後にissue numberで安定tie-breakする。
5. competing lineage / conflict Workはcandidateから除外し、他に進めるWorkが無ければ`INTERVENTION_REQUIRED`。

## 6. duplicate suppression

`V2ScheduleKey`は次をhashする。

- Goal revision
- Work identity / Issue revision
- Project status / priority
- dependency evidence
- canonical design identities
- active lineage / head
- verification/review/HV evidence identity
- DB checkpoint / packet identity
- next transition

同じkeyが既にin-flight/confirmedなら同一dispatchを再発行しない。

## 7. WorkRecord同期

bootstrap後のWork Issueを初めて観測したとき、typed Issue/Project snapshotから`PLANNED` WorkRecordを作成しV2へmigrateする。

Issue commentやCheckpoint文からrevision・PR・HEAD・次transitionを復元しない。

GitHub WorkがclosedなのにDBが未完了等の矛盾は静かに上書きせずtyped conflictとする。

## 8. Goal completion候補

`actionable Workなし`だけではGoal完了にしない。

- 全Work terminal
- pending/uncertain effectなし
- active lineageなし
- required Human Verification pendingなし
- Goal acceptance evidence complete

をfresh evidenceで満たす場合だけ`COMPLETE_GOAL`候補を返す。

## 9. 実装

- `v2_supervisor.py`: pure state derivation / selection / ScheduleKey
- `v2_work_queue.py`: GitHub planning discovery / typed snapshot / WorkRecord migration
- `v2_work_definition.py`: typed snapshot readをpublic adapter contractへ昇格
- tests: continuity / wait中別Work / dependency / conflict / duplicate / restart / goal completion

## 10. 完了条件

- current WorkをIssue commentから解析しない。
- dependency-ready/actionableを決定論的に選択する。
- wait-only Workが独立Workを止めない。
- 同じexact state/transitionを重複dispatchしない。
- bootstrap Workをtyped snapshotからV2 WorkRecordへ同期できる。
- conflictでfail-closedする。
- Work完了後に次WorkまたはGoal completion候補へ進む。
