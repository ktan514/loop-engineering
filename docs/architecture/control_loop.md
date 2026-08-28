# Generic Control Loop State Machine

Owner: Issue #3
Status: Initial canonical architecture draft

## 1. Purpose

Loop Engineering Platformの中心となる状態機械を、GitHub Issue / PR / Codex / OpenAI等の固有providerから分離して定義する。

Loopは「AIを常時回し続けるdaemon」ではなく、fresh observationに基づき**安全な1 transitionを決定・実行・readbackするControl Plane**である。

## 2. Top-level lifecycle

```text
PREFLIGHT
→ OBSERVE
→ RECONCILE
→ RESUME_GATE
→ SELECT
→ EXECUTE
→ READBACK
→ VERIFY
→ REVIEW
→ INTEGRATE
→ CHECKPOINT
→ CONTINUE | YIELD_EXTERNAL | INTERVENTION_REQUIRED | COMPLETE
```

各stateは必ず毎回通る固定serial pipelineではない。Workの現在状態に応じて不要stateをskipできるが、mutation前後の安全invariantは省略しない。

例:

```text
OBSERVE
→ RECONCILE
→ RESUME_GATE
→ SELECT
→ REVIEW
→ CHECKPOINT
→ YIELD_EXTERNAL
```

## 3. Run vs Goal vs Work

### Run

1回のPlatform process/execution session。

- restart可能
- crash可能
- Run終了はGoal終了を意味しない

### RunGoal

Loopが継続的に完了させようとする論理目標。

```text
RunGoal
- goal_id
- kind
- authority_refs[]
- completion_policy
- scope
```

kind例:

- `SINGLE_WORK`
- `PROJECT_QUEUE`
- `MILESTONE`
- `MIGRATION`
- `MISSION`

### WorkItem

独立してselection/transition可能な作業単位。

```text
WorkItem
- work_id
- source_identity
- type
- status
- priority
- dependencies[]
- canonical_design_refs[]
- verification_policy
- lineage_refs[]
```

GitHub IssueはWorkItem adapterの1実装であり、Core contractではない。

## 4. State meanings

### 4.1 PREFLIGHT

必要capabilityが存在するかをmutation前に確認する。

確認例:

- source-control read/write capability
- planning read/write capability
- local workspace capability
- implementer availability
- reviewer availability
- required runtime store capability
- credential presence without exposing secret value
- isolation/sandbox capability

Preflightは「全providerが常に利用可能」を要求しない。Work selectionに必要なcapabilityをtypedに評価する。

### 4.2 OBSERVE

current-state Authorityをfresh取得し、1 `ObservationEpoch` を構築する。

異なるepochの値を暗黙に混合しない。

### 4.3 RECONCILE

external live state、checkpoint、runtime state、canonical design identityの不一致を決定論的に分類する。

出力:

- reconciled snapshot
- conflicts[]
- required reconciliation actions[]

不一致を推測で補正しない。

### 4.4 RESUME_GATE

特定Work/Lineage/Transitionを安全に開始できるか評価する。

出力:

```text
ResumeCertificate
- gate: PASS | STOP
- run_goal
- target_work
- execution_target
- active_lineage
- canonical_design_refs[]
- current_status
- last_verified_evidence[]
- next_transition
- conflicts[]
- observation_id
- source_freshness
```

`PASS` はWork完了や品質保証を意味しない。「このexact stateから指定transitionを開始してよい」のみを意味する。

### 4.5 SELECT

dependency-readyかつactionableなWorkを選択する。

Selectionはprovider固有Status名へ依存しない。

### 4.6 EXECUTE

Design/Implement/Repair/Reconcile等のbounded actionを実行する。

Implementerへ渡す契約は `TaskPacket`。

```text
TaskPacket
- packet_id
- observation_id
- schedule_key
- target_work
- execution_target
- authority_refs[]
- scope[]
- non_goals[]
- acceptance_checks[]
- risk_boundaries[]
- active_lineage
- expected_transition
- allowed_effect_kinds[]
```

### 4.7 READBACK

local/remote mutation後、実際に何が起きたかをAuthority sourceから読み返す。

Command/process exit codeだけをeffect truthにしない。

### 4.8 VERIFY

deterministic tests / static checks / integration checks等のmachine verificationをexact targetへbindする。

### 4.9 REVIEW

Independent Reviewerによるcanonical reviewをexact targetへbindする。

### 4.10 INTEGRATE

merge / promotion / canonicalization等を行う。

IntegrationもWrite Gate → Effect → Readbackを通す。

### 4.11 CHECKPOINT

外部Authorityとreconcile可能なdurable stateを記録する。

Checkpoint自体をcurrent-state Authorityへ昇格しない。

## 5. Dependency-ready vs Actionable

この2つを明確に分離する。

### Dependency-ready

- required dependenciesが満了
- canonical designが解決可能
- fatal conflictなし
- planning policyが開始を禁止していない

### Actionable

現在local/remote actionを起こせる状態。

例:

- design required
- implementation required
- CI failure repair
- review finding repair
- reconciliation mutation
- merge ready

wait-only例:

- CI running
- review pending
- Human Verification pending
- external provider temporarily unavailable

wait-only Workが存在しても、独立actionable Workを止めない。

## 6. Selection policy

generic default:

1. current Workがsafe/actionableならcontinuityを優先
2. current Workがwait-onlyなら別dependency-ready actionable Workを列挙
3. unresolved conflictのWorkはimplementation candidateから除外
4. planning priority/statusをnormalized valueへmappingしてrank
5. stable Work identityでtie-break

Project-specific scheduling policyはPolicy Adapterで差し替え可能だが、安全invariantを緩和不可。

## 7. ScheduleKey / duplicate suppression

same exact state / same next transitionのduplicate dispatchを防ぐ。

`ScheduleKey`のcanonical input例:

- RunGoal identity
- Work identity
- trusted profile digest
- dependency evidence identities
- canonical design identities
- active lineage identity
- expected base/head
- current CI/review/verification identity
- relevant checkpoint identity
- next transition

同じScheduleKeyが既にsuccessful/in-flight effectへ対応する場合、duplicate executionを禁止またはreconcileする。

## 8. Write Gate

すべてのmutation直前にfresh preconditionを確認する。

```text
WriteGate
- expected authority revision
- expected target identity
- expected lineage
- expected base/head
- lease ownership
- effect policy
- conflict absence
```

Write Gateの観測からeffect実行までに対象が変化した場合、providerがconditional writeを提供するなら利用し、そうでなければreadbackでstale mutationを検出してreconcileする。

## 9. Effect model

mutationを直接「成功した事実」とみなさない。

```text
EffectIntent
→ EffectExecutor
→ provider/local response
→ fresh readback
→ EffectReceipt
```

`EffectReceipt`:

```text
EffectReceipt
- effect_id
- intended_target
- observed_target
- expected_before_identity
- observed_after_identity
- status
- evidence_refs[]
```

status例:

- `CONFIRMED`
- `NO_EFFECT`
- `STALE_TARGET`
- `AMBIGUOUS`
- `FAILED`

## 10. Run disposition

### CONTINUE

次の安全なactionable transitionが存在する。

### YIELD_EXTERNAL

Goalは未完了だが、現在は外部結果待ちのみで有用なactionがない。

busy polling / sleep loopを行わずRunを終了できる。

### INTERVENTION_REQUIRED

Human judgmentや外部操作が本当に必要。

例:

- competing canonical lineagesを自動決定不能
- required credential未設定で代替Workもない
- destructive decisionにHuman approval required
- source-of-truth conflictを安全に解消不能

### COMPLETE

RunGoalのcompletion policyをfresh evidenceで満たした場合のみ。

個別Work完了をMission/Project Goal完了と誤認しない。

## 11. Review/Fix loop

```text
IMPLEMENT
→ READBACK(new target)
→ VERIFY
→ REVIEW(exact target)
   ├─ PASS → INTEGRATE
   ├─ REQUEST_CHANGES → same lineage REPAIR
   ├─ ESCALATE → higher policy / Human
   └─ NOT_RUN/PENDING → YIELD or other Work
```

Repairは原則same lineageで行う。理由なくnew competing lineageを作らない。

## 12. Crash/restart invariant

Crash地点ごとに「effectが起きたか不明」を許容し、restart時にfresh readbackで判定する。

危険な設計:

```text
mutation success response
→ local checkpoint
```

だけに依存すると、mutation後checkpoint前crashでduplicate effectが起きる。

正規設計:

```text
expected effect identity
→ mutation
→ crash possible
→ restart
→ fresh readback
→ effect already existsなら再実行しない
```

## 13. Hard invariants

- one Run != one Goal
- wait-only != mission stop
- mutation前Write Gate
- mutation後effect readback
- same exact state duplicate dispatch抑止
- stale CI/reviewをcurrent evidenceへ昇格しない
- implementation findingはsame lineage repairを優先
- conflict未解消で推測継続しない
- busy pollしない
- Product固有Issue/Project identityをCoreへ埋め込まない
