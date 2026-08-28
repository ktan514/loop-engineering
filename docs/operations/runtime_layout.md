# Host Runtime State Layout

Owner: Issue #2
Status: Initial canonical operations draft

## 1. Purpose

Loop Engineeringの実行状態をProduct Workspaceから分離し、再起動・重複抑止・blocker管理・監査を安全に行うためのHost Runtime Stateの論理配置を定義する。

## 2. Root

論理root名:

```text
LOOP_ENGINEERING_HOME
```

実装時にOS別defaultを決定する。明示overrideは可能とするが、Product Workspace配下を既定にしない。

## 3. Logical layout

```text
$LOOP_ENGINEERING_HOME/
├─ identity/
│  └─ host.json
├─ projects/
│  └─ <project-key>/
│     ├─ project.json
│     ├─ blockers/
│     └─ workspaces/
│        └─ <workspace-id>/
│           ├─ workspace.json
│           ├─ sessions/
│           ├─ local-state/
│           └─ blockers/
├─ leases/
├─ services/
├─ cache/
└─ logs/
```

物理file名はadapter実装により変更可能。Coreはこのdirectory構造へ依存しない。

## 4. Data classes

### Durable operational state

再起動後も必要になり得るもの:

- project/workspace registration
- unresolved runtime blockers
- execution lease recovery metadata
- dispatch/review/integration idempotency identity
- sanitized checkpoints
- service endpoint registration

### Ephemeral state

Run終了後に破棄可能なもの:

- temporary staging paths
- process-local scratch
- transient stdout/stderr capture
- derived cache

### External Authority references

runtime stateへ保存してよいのはreference/digestであり、current truthのコピーをAuthority化しない。

例:

- repository id
- PR number
- expected head SHA
- canonical blob SHA
- workflow run id
- review request key

再開時は可能な限りexternal providerからfresh readbackする。

## 5. Blocker persistence

blockerは次を満たす。

- stable blocker id
- scope / subject identity
- reason code
- evidence reference
- creation time
- resolution condition
- lifecycle status

blocker fileを手動削除しただけで安全条件が満たされたとみなさない。

再開時:

```text
persisted blocker
→ fresh external/local evidence
→ resolution condition evaluation
→ resolved / remains blocked / conflict
```

stale blockerはreconciliation可能とする。

## 6. Execution leases

同一Workspace/Work/Transitionに複数Executorがmutationを行わないため、Execution Leaseを設ける。

```text
ExecutionLease
- lease_id
- scope
- holder_identity
- acquired_at
- heartbeat_or_expiry_policy
- target_identity
- transition_identity
```

単純なPID fileだけをAuthorityにしない。process死後のstale leaseを安全にreconcileできる情報を持つ。

lease取得だけではGitHub等のremote concurrencyを完全保証しないため、mutation前のfresh Write Gateを別途必須とする。

## 7. Logs

ログはdiagnostic用途でありAuthorityではない。

禁止:

- access token
- API key
- Authorization header
- raw `.env`
- reviewer secret
- private key
- provider raw responseにsecretが含まれる場合の無加工保存

推奨:

- run id
- project/workspace id
- transition
- source/effect identity
- sanitized status
- timing
- error class

## 8. Cache

cache missは機能停止理由にしない。

cache hitをlive truthとして扱わず、Authorityが必要なtransitionではfresh provider readを行う。

cache corruption/unknown schemaは破棄可能であることを原則とする。

## 9. Services

Trusted Reviewer Broker等のhost service discoveryに使うmetadataはsecretを含めない。

例:

- logical service name
- local socket path
- protocol version
- health metadata

credential本体はservice process側のみが保持する。

## 10. Permissions

Runtime rootは原則として単一ユーザー所有・最小権限とする。

Product process/target codeへRuntime root全体のread権限を与えない。

特にReviewer credentialやhost policyがProduct Workspaceから参照可能な構成にしない。

## 11. Backup / retention

必須AuthorityはGitHub等のexternal sourceに残すため、Runtime Store backupを唯一の復旧経路にしない。

一方、idempotency/auditに必要なoperational eventはretention policyを定義する。

Retentionは#4/#6でsecurity/recovery要件と合わせて確定する。

## 12. Failure semantics

Runtime Storeが利用不能な場合:

- read-only observationまで可能なケースは許容できる
- duplicate effect防止に必要なstateが確認不能ならmutationをfail-closed
- external Authorityをruntime cacheで代替しない
- Product側フォルダへfallbackしてstateを書かない

## 13. Hard invariants

- Product Workspace外にruntime stateを置く
- Product codeからruntime rootをAuthorityとして操作させない
- blocker削除 = blocker解消、とはしない
- log/cacheをcurrent-state Authorityにしない
- secretsを通常state/logへ保存しない
- stale lease/blockerをfresh evidenceでreconcile可能にする
