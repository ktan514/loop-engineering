# Integration and Recovery Architecture

Owner: Issue #6
Status: Initial canonical architecture draft

## 1. Purpose

Loop Engineering Platformがpartial failure、process crash、provider outage、stale evidence、duplicate dispatch、複数Productのwait状態を安全に扱い、再起動後も二重effectなしで継続できる横断設計を定義する。

## 2. Recovery principle

Recoveryの基本は「前回どこまで実行したと記憶しているか」ではなく、**前回意図したeffectが現在のAuthority上で実際に存在するかをfreshに読み直す**ことである。

```text
checkpoint / runtime event
+ fresh live authority
→ reconcile
→ effect present? / absent? / ambiguous?
→ Resume Gate
```

## 3. Transition transaction pattern

全mutation transitionは論理的に次の形を取る。

```text
1. Observe target
2. Acquire/confirm execution lease
3. Fresh Write Gate
4. Persist idempotency/effect intent where required
5. Execute effect
6. Fresh effect readback
7. Validate observed effect
8. Persist receipt/checkpoint
9. Release/advance lease
```

単一DB transactionでremote providerまでatomicにできるとは仮定しない。

したがってcrash recoveryはeffect identity + readbackで行う。

## 4. Crash windows

### A. Before effect

```text
Write Gate
→ crash
```

再起動時にfresh observationし、preconditionがまだ成立すれば再dispatch可能。

### B. During/after effect, before response

```text
provider request
→ effect may have happened
→ network/process crash
```

最も危険なwindow。

再起動時に同じeffectを即再送しない。expected effect identityでproviderを検索/readbackし、`CONFIRMED / NO_EFFECT / AMBIGUOUS`へ分類する。

### C. After response, before readback

provider response成功でもeffect truth未確定。

fresh readback必須。

### D. After readback, before checkpoint

external Authorityが進んでいるため、古いcheckpointだけを根拠に再dispatchしない。

fresh stateがcheckpointより進んでいる理由をeffect identityで説明できればadvanceとして採用する。

## 5. Idempotency identities

主要effectごとにstable idempotency identityを持つ。

例:

- Task dispatch: `ScheduleKey`
- Review request: `ReviewRequestKey`
- CI dispatch: `CheckRequestKey`
- Integration: expected PR/change-set + expected head
- Checkpoint publish: checkpoint generation key
- Improvement publication: improvement key

Providerがnative idempotencyを持たない場合、RuntimeStore + external readbackで重複抑止する。

## 6. CI recovery

```text
expected target HEAD
→ dispatch
→ run identity
→ pending/running: YIELD_EXTERNAL
→ restart/future run
→ fresh read run/result
```

Hard rules:

- result target != current expected target → stale
- old PASSをnew targetへ継承しない
- dispatch responseが不明ならsame targetのexisting runを検索してから再dispatch
- CI failureは同一lineage repair candidate

## 7. Review recovery

```text
ReviewRequestKey
= reviewer policy generation
+ target identity
+ canonical generation
+ bounded context generation
```

Rules:

- same keyのduplicate provider callを避ける
- reviewer service restart後もcompleted/in-flight identityをreconcile
- review中にhead/canonical generationが変化 → resultをcurrent PASSにしない
- `NOT_RUN`/transport failureをPASS/REQUEST_CHANGESへ推測変換しない
- `REQUEST_CHANGES`はsame lineage repairへ戻す

## 8. Integration recovery

merge/integration前:

- exact current target read
- required CI evidence exact target PASS
- required Review evidence exact target PASS
- unresolved blocker/conflictなし
- Human Verification policyがpre-mergeなら満了
- fresh Write Gate

merge request後:

- responseに依存せずcanonical trunk/targetをreadback
- integrated commit/change-set identityを確認
- source branch/head relationを確認

crash後に既にmerge済みなら再mergeせずcheckpointへ進む。

## 9. Human Verification

Human VerificationはAI reviewとは別evidence class。

```text
Machine Verification
Independent Review
Human Verification
```

Product Profileが必要とする場合のみGateへ組み込む。

Human Verification pendingのWorkがあっても、依存しないWork/Productは継続可能。

Humanの承認を別targetへ自動移送しない。必要に応じてrelease candidate / build / commit identityへbindする。

## 10. External provider outage

Provider failureをscope付きで扱う。

例:

```text
Reviewer unavailable
→ REVIEW transition blocked
→ unrelated DESIGN/IMPLEMENT Workは継続可能
```

```text
SourceControl Authority unavailable
→ current stateを確定できない
→ mutation全般fail-closed
```

```text
Optional analytics DB unavailable
→ operational metrics degraded
→ required idempotencyが他storeにあるなら継続可
```

## 11. RuntimeStore outage

Store capabilityを分類する。

- required-for-safety
- optional-for-observability

required idempotency/effect intentを確認できず、duplicate remote effectの危険があるtransitionは停止。

read-only observation等、安全に実行できる処理は継続可能。

## 12. Concurrency

### Same Work

原則1 active implementation lineage + exclusive mutation lease。

### Different Work / same Workspace

ファイル/branch/worktree競合の可能性があるため、Workspace mutation policyで排他する。

### Different Workspace / same Repository

remote branch/PR targetが独立していても、canonical trunk mutation時にfresh Write Gateが必要。

### Different Product

独立して安全ならparallel/alternate Runsを許容。

初期実装はsingle-process sequential schedulerでもよいが、domain modelはcross-product concurrencyを禁止しない。

## 13. No-progress control

無限修正/再試行を避ける。

追跡例:

- same finding recurrence
- same ScheduleKey repeated
- same error fingerprint
- repeated ambiguous effect
- repeated stale target
- repair cycle count

Policy threshold到達時:

- alternate Workがあれば切替
- high-tier reviewerへescalate
- Human interventionへ移行
- improvement candidate生成

単なる回数上限だけでGoal COMPLETEにはしない。

## 14. Graceful shutdown / SIGINT

shutdown時:

- new mutationを開始しない
- child process termination policyに従う
- in-flight effect identityを可能な範囲で記録
- leaseを安全にrelease、またはstale recovery可能な状態にする
- secretをshutdown logへdumpしない

強制終了でもrestart時readbackでreconcile可能であることを優先する。

## 15. Source-of-truth matrix

| Concern | Authority | Runtime support | Recovery action |
|---|---|---|---|
| branch/head | SourceControl | cached identity | fresh read |
| Work status | Planning provider | checkpoint ref | fresh read/reconcile |
| CI | CI provider | request key/run id | fresh result read |
| Review | Reviewer boundary/provider | request key | result reconcile |
| merge/integration | SourceControl | effect intent | trunk readback |
| Human verification | Product-defined authority | evidence ref | target-bound read |
| blocker | RuntimeStore + resolution evidence | durable blocker | reevaluate condition |
| lease | ExecutionLeasePort | durable lease | stale lease reconcile |

## 16. Multi-product scheduling

Example:

```text
Product A / Work 10: reviewer pending
Product A / Work 11: dependency blocked
Product B / Work 3: actionable

→ Product B / Work 3 may run
```

RunGoal scopeがsingle Productに限定されている場合はscope外へ越えない。

Cross-product schedulerはexplicit registered Projectのみ対象とし、filesystem自動全探索しない。

## 17. Completion semantics

`COMPLETE`にはfresh completion evidenceが必要。

禁止:

- actionable Workが見つからない → COMPLETE
- reviewer unavailable → COMPLETE
- all visible Work wait-only → COMPLETE
- current Work merged → Mission COMPLETE

これらは通常 `YIELD_EXTERNAL` または別Work selection。

## 18. Hard invariants

- crash後はfresh readbackから再開
- unknown effectを盲目的に再送しない
- remote response success != effect truth
- checkpoint lagでduplicate effectを起こさない
- CI/review/Human evidenceはexact targetへbind
- one wait-only Workでglobal stopしない
- Authority unavailable時にcacheからmutation truthを捏造しない
- graceful/forced restartの両方でreconcile可能なidentityを持つ
