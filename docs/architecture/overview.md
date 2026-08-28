# Loop Engineering Platform Architecture Overview

Owner: Issue #1
Status: Initial canonical architecture draft

## 1. Purpose

`loop-engineering` は、特定のProduct repositoryや特定のAI実装担当に依存せず、ソフトウェア開発の反復を安全に継続するための外部Control Planeである。

対象Productのruntimeへ組み込むものではなく、開発時に次の循環を統括する。

```text
PREFLIGHT
→ OBSERVE
→ RECONCILE
→ RESUME GATE
→ SELECT
→ EXECUTE
→ READBACK
→ VERIFY
→ REVIEW
→ INTEGRATE
→ CHECKPOINT
→ CONTINUE / YIELD / INTERVENTION / COMPLETE
```

## 2. Three physical boundaries

Platformは次の3領域を分離する。

```text
Loop Engineering Platform Repository
        │
        │ trusted control logic
        ▼
Target Product Workspace

Host Runtime State
(blockers / leases / sessions / logs / operational state)
```

### 2.1 Platform Repository

所有するもの:

- generic control-loop domain
- Resume / Write / Review / Integration gates
- provider-independent ports
- provider adapters
- host-side execution policy
- generic security policy
- schemas / templates / migration tooling
- Platform自身のcanonical design

所有しないもの:

- Product source code
- Product固有architecture
- Product固有Issue番号、Project番号、branch名
- Productのsecret
- Product runtime state

### 2.2 Target Product Workspace

所有するもの:

- Product code
- Product tests
- Product canonical design
- Product固有の開発ルール
- optional `.loop-engineering.yml`

Product WorkspaceはControl PlaneのAuthorityではない。特にfeature/PR branch上の設定やコードを、secret-bearing host control planeが無条件にimport/executeしてはならない。

### 2.3 Host Runtime State

既定のrootを `LOOP_ENGINEERING_HOME` と呼ぶ。

既定値は実装段階でOSごとに決定するが、Product Workspaceの外に配置する。

保存対象候補:

- runtime blockers
- execution leases / locks
- run/session metadata
- local checkpoints
- sanitized logs
- provider operational metadata
- optional cache

Host Runtime StateはGitHub等のlive Authorityの代替ではない。再起動補助・重複抑止・運用観測のためのoperational stateである。

## 3. Core abstractions

Platform CoreはGitHub、Codex、OpenAI、GitHub Actions等の固有名をdomain modelへ埋め込まない。

主要抽象:

- `RunGoal`: 何を完了まで進めるか
- `WorkItem`: 独立して選択・実行・検証できる作業単位
- `ExecutionTarget`: repository/workspace/ref/head等のexact target
- `ObservationEpoch`: 1回の判断に使うfresh source集合
- `AuthoritySnapshot`: current-state truthの観測値
- `Lineage`: 1 Workに対する連続した実装系統
- `ResumeCertificate`: next transition開始可否
- `TaskPacket`: Implementer等へ渡すbounded instruction contract
- `EffectIntent`: Hostが実行しようとするmutation
- `EffectReceipt`: mutation後readbackで確認した事実
- `Checkpoint`: 再開可能性のためのdurable summary
- `Blocker`: scopeを持つtyped interlock

## 4. Provider boundary

CoreはPortを介して外部systemを利用する。

初期Port候補:

- SourceControlReaderPort
- SourceControlWriterPort
- PlanningReaderPort
- PlanningWriterPort
- CIPort
- ImplementerPort
- ReviewerPort
- WorkspacePort
- RuntimeStorePort
- ExecutionLeasePort
- ClockPort

初期Adapter候補:

- GitHub
- GitHub Projects
- GitHub Actions
- Codex
- Trusted Reviewer Broker
- Filesystem Runtime Store

Provider交換はCore state machineの変更理由にしない。

## 5. Authority principle

Platformは「記憶している状態」ではなく、可能な限りfreshなexternal/live stateから現在状態を再構成する。

原則:

1. live provider stateをcurrent-state Authorityとして読む
2. Product canonical designのtrusted revisionを設計Authorityとして読む
3. runtime store / checkpoint / cacheはreconciliation補助に使う
4. summary / memoryは候補発見にのみ使い、current state確定に使わない
5. mutation直前にfresh Write Gateを行う
6. mutation後はeffect readbackで事実確認する

Authority conflictを暗黙補正しない。安全に一意化できない場合はfail-closedする。

## 6. Review independence

Implementerとfinal Reviewer Authorityを分離する。

- Implementerは自分の変更へfinal PASSを発行しない
- Reviewerは原則としてtarget repositoryを書き換えない
- reviewはexact target identityへbindする
- stale targetのreview結果はcurrent PASSとして扱わない
- reviewer credentialをImplementer / Product Workspaceへ渡さない

## 7. Generic completion model

`RunGoal` はGitHub Issueに限定しない。

例:

- single work item completion
- repository migration completion
- project queue completion
- milestone completion
- mission/root completion

GitHub Issue / Project / PR等へのmappingはAdapter/Profile側が担当する。

## 8. Architecture Completion Gate

Issue #1がPASSするまで、既存 `ai-liver-yura` のLoop Engineering責務と競合するPlatform実装を開始しない。

設計順序:

1. #2 Workspace / Project Profile boundary
2. #3 Control Loop / Authority / State Model
3. #4 Ports / Adapters / Security Boundary
4. #5 ai-liver-yura migration reconciliation
5. #6 Generic E2E / Recovery
6. cross-design audit
7. Human architecture confirmation

実装開始はこのGate通過後とする。
