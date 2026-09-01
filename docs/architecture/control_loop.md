# Generic Control Loop State Machine

Owner: Issue #3
現行製造Authority: #81 / #82
Status: canonical architecture / V2 manufacturing

## 1. Purpose

Loop Engineering Platformの中心となる状態機械を、GitHub Issue / PR / Codex / OpenAI等の固有providerから分離して定義する。

Loopは「AIを常時回し続けるdaemon」ではなく、初期GoalをWork構造へ展開し、fresh observationとdurable execution stateに基づいて**次の安全なtransitionを決定・実行・readbackし、Goal完了まで継続するControl Plane**である。

安全な1 transitionの実行基盤だけではLoop Engineering全体の完成を意味しない。Goal bootstrap、Planning、Work selection、Implementer、Verification、Review、Integration、next Work、Goal completionまでが接続されて完成する。

## 2. Top-level lifecycle

新規Productの標準開始:

```text
BOOTSTRAP
→ PREFLIGHT
→ PLAN
→ OBSERVE
→ RECONCILE
→ RESUME_GATE
→ SELECT
→ EXECUTE
→ READBACK
→ VERIFY
→ REVIEW
→ HUMAN_VERIFY?
→ INTEGRATE
→ CHECKPOINT
→ CONTINUE | YIELD_EXTERNAL | INTERVENTION_REQUIRED | COMPLETE
```

既にWork graphが存在するRunやrestartではBOOTSTRAP / PLANをskipできる。

各stateは毎回通る固定serial pipelineではない。Workの現在状態に応じて不要stateをskipできるが、mutation前後の安全invariantは省略しない。

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
- goal_definition_identity
- goal_revision
- kind
- authority_refs[]
- acceptance_criteria[]
- completion_policy
- scope
```

新規ProductではHost Product RegistrationがGoal Definitionのtrust anchorを持つ。Goal本文はPlanning入力であり、current execution stateのAuthorityではない。

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
- logical_key
- source_identity
- type
- status
- priority
- dependencies[]
- acceptance_criteria
- canonical_design_refs[]
- verification_policy
- lineage_refs[]
```

GitHub IssueはWorkItem planning adapterの1実装であり、Core contractではない。

## 4. State meanings

### 4.1 BOOTSTRAP

Host Product RegistrationとGoal Definitionを読み、対象Productを安全に開始できるか検証する。

確認:

- canonical Workspace path
- target repository identity
- planning Project identity
- trusted Profile / policy
- Goal identity / revision / acceptance criteria
- Self-Improvement公開先

Product Workがまだ存在しないこと自体はblockerではない。新規GoalならPLANへ進む。

BOOTSTRAPは既存Issue番号、PR番号、branch、TaskPacketを人間へ事前要求しない。

### 4.2 PREFLIGHT

必要capabilityが存在するかをmutation前に確認する。

確認例:

- source-control read/write capability
- planning read/write capability
- local workspace capability
- implementer availability
- reviewer availability
- required PostgreSQL operational state capability
- credential presence without exposing secret value
- isolation/sandbox capability

Preflightは「全providerが常に利用可能」を要求しない。現在transitionに必要なcapabilityをtypedに評価する。

### 4.3 PLAN

Goalから型付きWork graph proposalを生成・検証し、Planning providerへ投影する。

Planning outputは直接commandではない。

最低限:

```text
WorkPlanProposal
- proposal_identity
- goal_revision
- works[]
- dependencies[]
- acceptance_criteria
- completion_conditions
```

schema、cycle、scope、logical key、Goal対応、既存Work競合を検証後にだけIssue / Project create effectへ変換する。

同じGoal revision / logical keyの再bootstrapは同一Workへ収束させ、create retryで重複Workを作らない。

### 4.4 OBSERVE

current-state Authorityをfresh取得し、1 `ObservationEpoch` を構築する。

異なるepochの値を暗黙に混合しない。

観測元は責務ごとに分離する。

- Issue / Project: Work定義、依存、優先度、完了判断
- PostgreSQL: selected Work、TaskPacket、Checkpoint、lease、pending effect
- GitHub live: branch / PR / HEAD / merge
- CI / Reviewer / Human Verification: exact target evidence

### 4.5 RECONCILE

external live state、PostgreSQL execution state、planning definition、canonical design identityの不一致を決定論的に分類する。

出力:

- reconciled snapshot
- conflicts[]
- required reconciliation actions[]

不一致を推測で補正しない。

Issue comment自然文からcurrent Work / PR / HEADを復元しない。

### 4.6 RESUME_GATE

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

### 4.7 SELECT

dependency-readyかつactionableなWorkを選択する。

Selectionはprovider固有Status名へ依存しない。

### 4.8 EXECUTE

DESIGN / IMPLEMENT / REPAIR / RECONCILE等のbounded actionを実行する。

Implementerへ渡す契約は型付きTaskPacketとする。

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

DESIGNが必要なWorkはcanonical design evidenceが確定するまでIMPLEMENTへ進めない。

### 4.9 READBACK

local/remote mutation後、実際に何が起きたかをAuthority sourceから読み返す。

Command/process exit codeだけをeffect truthにしない。

### 4.10 VERIFY

deterministic tests / static checks / integration checks / CI等のmachine verificationをexact targetへbindする。

old-head evidenceをcurrent targetへ流用しない。

### 4.11 REVIEW

Independent Reviewerによるcanonical reviewをexact targetへbindする。

REQUEST_CHANGESはsame lineage REPAIRへ戻す。review pendingだけをHuman Interventionにしない。

### 4.12 HUMAN_VERIFY

GUI、音声、映像、実機、操作感等、自動検証だけで完了判定できないWorkについて明示的なHuman Verification evidenceを待つ。

必要なWorkだけがこのstateを通る。

Human Verification pendingはwait-onlyであり、独立actionable Workがあればそちらへ進める。

HEAD変更後はpolicyに従い旧Human Verificationを無効化する。

### 4.13 INTEGRATE

merge / promotion / canonicalization等を行う。

IntegrationもWrite Gate → Effect → Readbackを通す。

mergeはexpected exact HEADを固定する。

### 4.14 CHECKPOINT

再起動可能なdurable execution stateをPostgreSQLへ記録する。

GitHub等の外部effectの真偽は各provider live stateがAuthorityであり、DB記録だけで成功と判定しない。

Issue commentはCheckpointの人間向け投影であって再開入力ではない。

## 5. Dependency-ready vs Actionable

### Dependency-ready

- required dependenciesが満了
- canonical designが解決可能、またはDESIGN transitionがactionable
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

- RunGoal identity / revision
- Work identity / revision
- trusted profile digest
- dependency evidence identities
- canonical design identities
- active lineage identity
- expected base/head
- current CI/review/verification identity
- latest safe checkpoint identity
- next transition

同じScheduleKeyが既にsuccessful/in-flight effectへ対応する場合、duplicate executionを禁止またはreconcileする。

PostgreSQLのTaskPacket generation / idempotencyと統合し、process再起動後も抑止を維持する。

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

create系effectでもlogical identityとreadback方法を先に確定する。

## 9. Effect model

mutationを直接「成功した事実」とみなさない。

```text
EffectIntentをDB確定
→ EffectExecutor
→ provider/local response
→ fresh target-limited readback
→ EffectReceiptをDB確定
```

status例:

- `CONFIRMED`
- `NO_EFFECT`
- `STALE_TARGET`
- `UNCERTAIN`
- `FAILED`

`UNCERTAIN`を同じidempotency keyで自動再送しない。

## 10. Run disposition

### CONTINUE

次の安全なactionable transitionが存在する。

### YIELD_EXTERNAL

Goalは未完了だが、現在は外部結果待ちのみで有用なactionがない。

busy pollingを前提にしない。Runを安全に終了し、後続triggerでfresh observeできる。

### INTERVENTION_REQUIRED

Human judgmentや外部操作が本当に必要。

例:

- competing canonical lineagesを自動決定不能
- required credential未設定で代替Workもない
- destructive decisionにHuman approval required
- source-of-truth conflictを安全に解消不能

### COMPLETE

RunGoalのcompletion policyをfresh evidenceで満たした場合のみ。

個別Work完了や「actionable Workなし」をGoal完了と誤認しない。

## 11. Review/Fix loop

```text
IMPLEMENT
→ READBACK(new target)
→ VERIFY
→ REVIEW(exact target)
   ├─ PASS → HUMAN_VERIFY? → INTEGRATE
   ├─ REQUEST_CHANGES → same lineage REPAIR
   ├─ ESCALATE → higher policy / Human
   └─ NOT_RUN/PENDING → YIELD or other Work
```

Repairはsame lineageで行う。理由なくnew competing lineageを作らない。

## 12. Work / Goal completion

Work完了はintegration effectのfresh readbackとWork acceptance evidenceを確認した後にだけ成立する。

Goal完了は次をすべてfresh確認した場合だけ成立する。

- Goal配下のplanned Workがすべてterminal completion
- open dependency未完了なし
- active implementation lineageなし
- pending / uncertain effectなし
- required Human Verification pendingなし
- Goal acceptance criteriaに対応するcompletion evidenceあり

## 13. Crash/restart invariant

停止後の最初の実行状態入力はPostgreSQLの安全Checkpointである。

```text
DB recovery
→ Product Registration / Goal revision照合
→ typed planning definition sync
→ pending effect target限定readback
→ necessary live source readback
→ RECONCILE
→ RESUME_GATE
```

mutation後checkpoint前crashでも、effect identityをreadbackして二重送信しない。

## 14. Hard invariants

- initial GoalだけからWork graphをbootstrap可能
- one Run != one Goal
- wait-only != mission stop
- design-before-code
- mutation前Write Gate
- mutation後effect readback
- pending / UNCERTAIN effect再送禁止
- same exact state duplicate dispatch抑止
- stale CI/review/Human Verificationをcurrent evidenceへ昇格しない
- implementation findingはsame lineage repairを優先
- conflict未解消で推測継続しない
- Issue comment自然文をmachine current-state Authorityにしない
- Product固有Issue/Project identityをCoreへ埋め込まない
- controlled Goal-to-completion E2E未PASSでPlatform完成としない
