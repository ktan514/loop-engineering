# Ports and Adapters Architecture

Owner: Issue #4
Status: Initial canonical architecture draft

## 1. Purpose

Loop Engineering CoreをGitHub、GitHub Projects、GitHub Actions、Codex、OpenAI等の特定providerから分離し、交換可能なPort/Adapter構造を定義する。

## 2. Dependency rule

依存方向:

```text
Adapters → Application / Core Ports → Domain
```

Core/Domainはprovider SDK、HTTP response型、GitHub固有ID形式、Codex CLI固有optionをimportしない。

## 3. Read vs Write separation

read capabilityとwrite capabilityを別Portとして扱う。

理由:

- Reviewerはread-onlyでよい
- planning read可能でもwrite不可の環境がある
- Preflightで最小権限を検証しやすい
- accidental mutationを型/DI境界で減らせる

## 4. Source Control Ports

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
- merge/integrate
- comment/checkpoint mutation where SCM owns it

全mutationは`WritePrecondition`を受け取り、可能ならprovider conditional mutationを使う。

## 5. Planning Ports

### PlanningReaderPort

- WorkItem discovery
- dependency/status/priority/date read
- project/milestone/queue read
- planning schema/field mapping resolution

### PlanningWriterPort

- Work status transition
- priority/date/field mutation
- add/remove Work from queue/project
- planning checkpoint metadata mutation

Planning providerがSCM Issueと同一providerでもPortは論理分離する。

## 6. CI Port

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

GitHub Actionsは初期Adapter候補。

## 7. Implementer Port

```text
ImplementerPort
- inspect_capabilities()
- execute(TaskPacket) -> ChangeProposal | RemoteEffectReport | BlockedResult
```

Implementerの本質は**TaskPacketに対する変更提案/実装結果生成**であり、Git push権限を持つことをCore requirementにしない。

### Preferred mode: proposal mode

```text
TaskPacket
→ Implementer
→ ChangeProposal
→ Host validation
→ SourceControlWriterPort
→ commit/push
```

### Compatibility mode: remote-effects mode

既存Codex運用のようにImplementer自身がcommit/pushする場合:

- capabilityとして明示
- allowed remote effect scopeをTaskPacketで制限
- child終了後にfresh readback必須
- expected transition外のmutationをconflict扱い
- Reviewer credentialを渡さない

初期Codex Adapterは両modeのうち実環境で安全に成立するものから実装可能。

## 8. Reviewer Port

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

Reviewerはfinal quality Authorityを担えるが、source-control write capabilityを持たない。

## 9. Workspace Port

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

## 10. Runtime Store Port

`workspace_boundary.md` / `runtime_layout.md`のoperational stateを扱う。

- blockers
- sessions
- idempotency keys
- sanitized events
- local checkpoints

external current-state Authorityを置き換えない。

## 11. Execution Lease Port

```text
ExecutionLeasePort
- acquire(scope, target, transition)
- renew(lease)
- inspect(scope)
- release(lease)
- reconcile_stale(lease, evidence)
```

process-local mutexだけに限定しない。将来cross-process/cross-host adapterへ拡張可能。

## 12. Credential Provider

secret取得はProject Profileとは別Port/Host serviceで扱う。

```text
CredentialProviderPort
- capability_available(name) -> bool/metadata
- issue_ephemeral_handle(name, consumer_scope)
```

Core snapshot/logへsecret値を返さないことを原則とする。

Reviewer credentialはReviewer host boundaryの内側へ閉じ込める。

## 13. Policy Ports

Product固有mapping/判断をCoreへハードコードしない。

候補:

- WorkMappingPolicy
- SchedulingPolicy
- CanonicalDiscoveryPolicy
- VerificationPolicy
- MutationPolicy
- CommandPolicy

Mandatory Host Safety PolicyはこれらProduct policyより上位。

## 14. Event model

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

## 15. Adapter failure classification

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

## 16. Initial adapter set

初期実装候補:

- GitHubSourceControlAdapter
- GitHubPlanningAdapter
- GitHubActionsCIAdapter
- CodexImplementerAdapter
- TrustedReviewerBrokerAdapter
- FilesystemRuntimeStoreAdapter
- LocalWorkspaceAdapter
- FilesystemExecutionLeaseAdapter

PostgreSQL operational store等は後続adapterとして追加可能。

## 17. Hard invariants

- Coreはprovider SDK型へ依存しない
- Read/Write capabilityを分離
- ImplementerのGitHub write権限をCore必須条件にしない
- Reviewerへsource-control write権限を渡さない
- secretをProject Profile/TaskPacket/Checkpointへ含めない
- provider response successだけでeffect truthを確定しない
- exact target identityをCI/Review/Integrationで維持
