# Ports and Adapters Architecture

Owner: Issue #4
現行製造Authority: #81 / #82
Status: canonical architecture / V2 manufacturing

## 1. Purpose

Loop Engineering CoreをGitHub、GitHub Projects、GitHub Actions、Codex、OpenAI、PostgreSQL等の特定providerから分離し、交換可能なPort/Adapter構造を定義する。

V2の完成品は、Goal bootstrap、Planning、Work selection、Implementer、CI、Reviewer、Human Verification、Integration、durable recoveryをPort境界で接続する。

## 2. Dependency rule

依存方向:

```text
Adapters → Application / Core Ports → Domain
```

Core/Domainはprovider SDK、HTTP response型、GitHub固有ID形式、Codex CLI固有option、PostgreSQL driver型をimportしない。

## 3. Read vs Write separation

read capabilityとwrite capabilityを別Portとして扱う。

理由:

- Reviewerはread-onlyでよい
- planning read可能でもwrite不可の環境がある
- Preflightで最小権限を検証しやすい
- accidental mutationを型/DI境界で減らせる
- create系effectもreadbackと分離できる

## 4. Product Registration / Goal Port

Host bootstrap trust anchorをProduct側のPlanning providerから分離する。

```text
ProductRegistrationPort
- load(product_key) -> ProductDevelopmentRegistration
- resolve_goal(registration) -> GoalDefinitionSnapshot
```

ProductDevelopmentRegistration最低項目:

- canonical Workspace path
- target repository identity
- Product Project identity
- trusted Profile source
- trunk branch
- Goal Definition source
- Self-Improvement target

Goal DefinitionはPlanning入力であり、current execution stateのAuthorityではない。

## 5. Planning Ports

### PlanningGeneratorPort

Goalから型付きWork proposalを生成する。

```text
PlanningGeneratorPort
- propose(goal, profile, existing_work_snapshot) -> WorkPlanProposal
```

LLM outputを直接mutation commandとして扱わない。proposalはschema / dependency / scope / duplication検証を通してからPlanningWriterへ渡す。

### PlanningReaderPort

- WorkItem discovery
- dependency/status/priority/date read
- project/milestone/queue read
- acceptance criteria / machine field read
- planning schema/field mapping resolution

### PlanningWriterPort

- Work Issue作成
- Work status transition
- priority/date/field mutation
- add/remove Work from queue/project
- dependency relation mutation
- machine field mutation

Planning providerがSCM Issueと同一providerでもPortは論理分離する。

create系mutationもEffect Intent / readback契約を通し、logical Work identityで重複作成を抑止する。

## 6. Source Control Ports

### SourceControlReaderPort

責務:

- repository identity resolve
- ref/head/base read
- branch/PR/change-set read
- commit/blob/file read
- merge/integration state read
- current permission/capability observation

Coreへ返すのはnormalized snapshot。

### SourceControlWriterPort

責務:

- create/update branch/ref
- create commit/change-set
- open/update PR equivalent
- request reviewer where SCM owns it
- merge/integrate
- comment/report mutation where SCM owns it

全mutationは`WritePrecondition`を受け取り、可能ならprovider conditional mutationを使う。

## 7. Effect Execution Port

V2ではPlanning / SCMのwrite操作を、直接Port callの成功だけで確定しない。

```text
EffectExecutorPort
- execute(EffectAttempt) -> EffectDispatchResult

EffectReadbackPort
- readback(EffectAttempt) -> EffectReadbackResult
```

順序:

```text
DB intent確定
→ target限定readback
→ Write Gate
→ EffectExecutor
→ target限定readback
→ DB outcome確定
```

create系effectは作成前にprovider IDを持てないため、deterministic logical identityとreadback locatorをTaskPacketに保存する。

## 8. CI Port

```text
CIPort
- resolve_required_checks(target, profile)
- dispatch(check_request)
- read_result(check_identity)
- normalize_evidence(result)
```

Requirements:

- exact target binding
- duplicate dispatch policy
- pending/runningのwait semantics
- stale target rejection
- logs/artifactsはuntrusted evidence dataとして扱う

GitHub Actionsは初期Adapter。

## 9. Implementer Port

```text
ImplementerPort
- inspect_capabilities()
- execute(TaskPacket) -> ChangeProposal | RemoteEffectReport | BlockedResult
```

Implementerの本質はTaskPacketに対する設計・変更提案・実装結果生成であり、Git push権限を持つことをCore requirementにしない。

### Preferred mode: proposal mode

```text
TaskPacket
→ Implementer
→ ChangeProposal
→ Host validation
→ Workspace / SourceControl effect
→ readback
```

### Compatibility mode: remote-effects mode

Implementer自身がcommit/pushする場合:

- capabilityとして明示
- allowed remote effect scopeをTaskPacketで制限
- child終了後にfresh readback必須
- expected transition外のmutationをconflict扱い
- Reviewer credential / Operational Store credentialを渡さない

DESIGN / IMPLEMENT / REPAIRを別transitionとして扱い、設計が必要なWorkでDESIGN evidenceなしにIMPLEMENTへ進ませない。

## 10. Reviewer Port

```text
ReviewerPort
- health()
- request(ReviewRequest)
- read_result(ReviewRequestKey)
```

ReviewRequest最低項目:

- target repository identity
- exact head/target identity
- trusted canonical generation
- bounded diff/change evidence
- Work acceptance criteria
- verification evidence refs

verdict例:

- `PASS`
- `REQUEST_CHANGES`
- `ESCALATE`
- `NOT_RUN`

ReviewerはImplementerから独立し、source-control write capabilityを持たない。

## 11. Human Verification Port

Human Verificationの必要性と結果をtyped evidenceとしてControl Loopへ返す。

```text
HumanVerificationPort
- required(work, profile) -> bool
- request(verification_request) -> VerificationRequestIdentity
- read_result(identity) -> HumanVerificationEvidence
```

自動テストで代替できないUI、音声、映像、実機、操作感等だけを対象とする。

結果はexact HEADへbindする。

## 12. Workspace Port

```text
WorkspacePort
- register/resolve workspace
- inspect checkout/ref/head
- inspect dirty state
- prepare isolated work area
- apply validated proposal
- read local diff
- cleanup staging area
```

Workspace操作はrepository identityとWorkspaceIdentityを常に照合する。

## 13. Operational State Port

V2の停止復元に必要なtransactional durable stateを扱う。

```text
OperationalStatePort
- synchronize_work_definition(...)
- select_work(...)
- issue_task_packet(...)
- start_transition(...)
- record_effect_intent(...)
- record_effect_outcome(...)
- checkpoint(...)
- enqueue_report(...)
- recover(...)
```

保存対象:

- Work execution state
- TaskPacket / generation
- Checkpoint
- lease
- idempotency
- effect attempt / pending effect
- report outbox
- sanitized execution evidence identity

ProductのIssue / Project定義やGitHub live effectをDBだけで上書きしない。

### Current production adapter

現行V2ではPostgreSQL adapterを停止復元の必須production基盤とする。

Filesystem RuntimeStoreは歴史上の初期adapterであり、V2 productionのtransaction / lease / effect atomicity要件を満たす代替として扱わない。将来別adapterを追加する場合も、PostgreSQLと同等のdurability / transaction / concurrency / recovery契約を満たす必要がある。

## 14. Execution Lease Port

```text
ExecutionLeasePort
- acquire(scope, target, transition)
- renew(lease)
- inspect(scope)
- release(lease)
- reconcile_stale(lease, evidence)
```

V2 productionではOperational State transactionと整合するlease実装を使用する。

## 15. Credential Provider

secret取得はProject Profileとは別Port/Host serviceで扱う。

```text
CredentialProviderPort
- capability_available(name) -> bool/metadata
- issue_ephemeral_handle(name, consumer_scope)
```

Core snapshot/logへsecret値を返さない。

Reviewer credentialはReviewer host boundaryの内側へ閉じ込める。Operational Store credentialをImplementerへ渡さない。

## 16. Policy Ports

Product固有mapping/判断をCoreへハードコードしない。

候補:

- WorkMappingPolicy
- SchedulingPolicy
- CanonicalDiscoveryPolicy
- VerificationPolicy
- HumanVerificationPolicy
- MutationPolicy
- CommandPolicy

Mandatory Host Safety PolicyはこれらProduct policyより上位。

## 17. Event model

Adapter固有eventをDomain eventへnormalizeする。

例:

```text
GitHub workflow completed
→ VerificationEvidenceUpdated

PR head changed
→ ExecutionTargetAdvanced

Review result arrived
→ ReviewEvidenceUpdated
```

Platformはwebhook常駐前提でなくてもよい。明示Run時のfresh Observeで同じ状態を再構成可能にする。

## 18. Adapter failure classification

Adapterはraw exceptionを直接Control Loop semanticsにしない。

最低限:

- `UNAVAILABLE`
- `UNAUTHORIZED`
- `FORBIDDEN`
- `NOT_FOUND`
- `STALE_PRECONDITION`
- `RATE_LIMITED`
- `INVALID_RESPONSE`
- `AMBIGUOUS_EFFECT`

Coreはerror kind + scope + retry semanticsからRun dispositionを判断する。

## 19. Current V2 adapter set and manufacturing gaps

現行V2でADOPT / EXTENDする主要adapter:

- GitHub Work Definition adapter
- GitHub effect readback adapter
- GitHub Issue report publisher
- PostgreSQL Work / Execution State adapter
- GitHub V2 Effect Executor
- Codex / Workspace系既存adapterの有用部分

製造#83〜#88で完成させるgap:

- Goal Planning generator
- Planning create/update writer
- create系effect / readback
- V2 Scheduler / Supervisor
- V2 Codex Implementer composition
- CI adapter V2 evidence connection
- Reviewer V2 evidence connection
- Human Verification boundary
- V2 Autonomous Runner

## 20. Hard invariants

- Coreはprovider SDK型へ依存しない
- Read/Write capabilityを分離
- Planning LLM outputを直接commandとして実行しない
- ImplementerのGitHub write権限をCore必須条件にしない
- Reviewerへsource-control write権限を渡さない
- secretをProject Profile/TaskPacket/Checkpointへ含めない
- provider response successだけでeffect truthを確定しない
- exact target identityをCI/Review/Human Verification/Integrationで維持
- V2 production recoveryはtransactional durable Operational Stateを必須とする
- create effectを再起動時に盲目的再送しない
