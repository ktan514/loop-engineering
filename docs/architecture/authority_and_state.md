# Authority and State Model

Owner: Issue #3
Status: Initial canonical architecture draft

## 1. Purpose

Loop Engineeringが扱う「現在状態」「設計Authority」「実行記憶」を分離し、stale state・competing lineage・provider不整合を安全に扱う。

## 2. Authority classes

### 2.1 Current-state Authority

「今どうなっているか」を決める外部/live source。

例:

- source-control repository / branch / commit / pull request
- planning systemのlive Work state
- CI run/check state
- canonical review state
- Human Verification state

Coreは特定providerを前提にせず、Adapterからnormalized snapshotを受け取る。

### 2.2 Design Authority

「どうあるべきか」を決めるtrusted canonical design / ADR / policy。

必ずexact revision identityを持つ。

### 2.3 Operational State

再起動・重複抑止・監査のためのruntime memory。

例:

- blocker
- lease
- dispatch key
- review request key
- event log
- local checkpoint

Operational Stateをexternal current-state Authorityへ昇格しない。

### 2.4 Conversational Context

chat transcript / summary / memory等。

候補発見・説明補助には使えるが、current PR/head/status/next transition確定には使用しない。

## 3. Authority order

Generic default:

### Current facts

1. fresh live provider state
2. provider-bound latest durable checkpoint
3. normalized planning state
4. trusted local operational evidence
5. conversational context

ただし「順番で上書きする」だけではなく、不一致はtyped conflictとして残す。

### Design intent

1. target Workが参照するtrusted canonical design revision
2. parent/root architecture / mandatory platform policy
3. trusted decision record
4. conversational context

canonical candidateが複数ありsupersede関係を一意化できない場合はSTOP。

## 4. Observation Epoch

1回の判断に使用したsource setをimmutableに束ねる。

```text
ObservationEpoch
- observation_id
- observed_at
- run_goal_snapshot
- project_profile_snapshot
- source_control_snapshots[]
- planning_snapshots[]
- work_snapshots[]
- lineage_snapshots[]
- ci_snapshots[]
- review_snapshots[]
- verification_snapshots[]
- canonical_design_snapshots[]
- runtime_snapshots[]
- diagnostics[]
```

Epochをまたいだ値の混在は明示的なre-observationとして扱う。

## 5. SourceIdentity

全snapshotは可能な範囲でstable identity + revisionを持つ。

```text
SourceIdentity
- provider_kind
- object_kind
- stable_id
- revision_id
- observed_at
```

例:

- repository: stable repository id
- branch: repository id + ref + head SHA
- PR: stable PR id + current head SHA + updated revision
- canonical file: repository id + trusted commit + path + blob SHA
- CI: run/check id + tested target identity
- review: review request/result id + reviewed target identity

## 6. ExecutionTarget

「何に対してeffect/evidenceをbindするか」を共通表現する。

```text
ExecutionTarget
- repository_identity
- workspace_identity?
- ref_identity?
- base_identity?
- head_identity?
- canonical_generation_digest?
```

transitionごとに必要fieldを明示する。

Review/CI/mergeではhead identityを必須とする。

## 7. Lineage model

1 WorkItemの連続した実装履歴をLineageとする。

```text
Lineage
- lineage_id
- work_id
- classification
- repository_identity
- branch_identity?
- pr_identity?
- base_identity?
- head_identity?
- created_from
- supersession?
```

classification:

- `ACTIVE`
- `SUPERSEDED`
- `VALIDATION_ONLY`
- `CI_ONLY`
- `ABANDONED`
- `MERGED`
- `UNKNOWN`

原則:

```text
1 WorkItem = 1 active implementation lineage
```

stacked PRは明示的に1 lineageとして表現可能。

同一WorkにACTIVE候補が複数、またはUNKNOWN lineageがcurrent mutationへ影響する場合はconflict。

## 8. Conflict model

最低限:

- `AUTHORITY_UNAVAILABLE`
- `AUTHORITY_CONTRADICTION`
- `PROFILE_UNRESOLVED`
- `CANONICAL_UNRESOLVED`
- `CANONICAL_MISMATCH`
- `MULTIPLE_ACTIVE_LINEAGES`
- `UNKNOWN_LINEAGE`
- `BASE_IDENTITY_MISMATCH`
- `HEAD_IDENTITY_MISMATCH`
- `UNEXPLAINED_TARGET_ADVANCE`
- `CHECKPOINT_LIVE_MISMATCH`
- `CI_TARGET_MISMATCH`
- `REVIEW_TARGET_MISMATCH`
- `VERIFICATION_MISMATCH`
- `LEASE_CONFLICT`
- `FORBIDDEN_CAPABILITY`
- `RUNTIME_STATE_UNAVAILABLE`

Conflictにはscopeを持たせる。

```text
Conflict
- kind
- scope
- subject_identity
- evidence_refs[]
- severity
- resolution_policy
```

1 Work固有conflictでProject全体を停止しない。ただしGLOBAL/SECURITY/Authority conflictは広いscopeをblockできる。

## 9. Explainable state advance

Checkpointよりlive stateが新しいこと自体は異常ではない。

説明可能例:

- same lineage normal push
- exact-target CI/review result arrival
- merge completion
- explicit supersede/abandon
- newer trusted checkpoint

説明できないadvanceは`UNEXPLAINED_TARGET_ADVANCE`。

## 10. Evidence binding

Evidenceは対象identityと結び付ける。

```text
Evidence
- evidence_id
- kind
- target_identity
- source_identity
- result
- observed_at
```

例:

- CI PASS for HEAD A
- Review PASS for HEAD A
- Human Verification PASS for release candidate B

HEAD AのevidenceをHEAD Bへ自動継承しない。

## 11. Canonical generation

複数canonical fileを使う場合、対象Workの設計世代をdigest化できる。

```text
CanonicalGeneration
- refs[]
- blob_identities[]
- normalized_digest
```

PRが長期化してtrusted canonical branchが進んだ場合、Review/Resume時にcurrent trusted generationと比較する。

古いbase metadataだけをcanonical Authorityにしない。

## 12. Checkpoint model

Checkpointは再開補助でありcurrent truthではない。

```text
Checkpoint
- checkpoint_id
- goal_id
- work_id?
- lineage_id?
- last_observation_id
- expected_target_identity
- last_confirmed_effects[]
- last_verification[]
- pending_external[]
- next_expected_transition
- created_at
```

restart時:

```text
Checkpoint
+ fresh live state
→ reconcile
→ Resume Gate
```

Checkpointだけから作業を再開しない。

## 13. State ownership matrix

| State | Authority owner | Runtime cache allowed | Fresh read required before mutation |
|---|---|---:|---:|
| repository head | SourceControl | yes | yes |
| planning status | Planning provider | yes | yes when relevant |
| canonical design | trusted source ref | yes | yes when generation-sensitive |
| CI result | CI provider | yes | yes before merge |
| review verdict | Reviewer provider/broker | yes | yes before merge |
| runtime blocker | RuntimeStore + resolution evidence | n/a | yes |
| execution lease | ExecutionLeasePort | n/a | yes |
| chat memory | none/current state non-authoritative | yes | never sufficient |

## 14. Staleness policy

stale判定は「時間が古い」だけではなくidentity mismatchを中心にする。

Hard stale例:

- reviewed head != current head
- tested commit != integration target
- profile blob changed
- canonical generation changed in a way relevant to Work
- dependency completion reverted

Time-based freshness windowはprovider/sessionの補助policyとして使用可能。

## 15. Fail-closed vs degrade

### Fail-closed

- mutation target identity不明
- competing active lineage
- canonical Authority unresolved
- secret/security policy conflict
- duplicate effect status不明で再実行すると危険

### Degrade possible

- optional cache unavailable
- advisory metadata unavailable
- non-required analytics store unavailable
- independent Workが別に存在する場合の1 provider wait

## 16. Hard invariants

- operational state != current-state Authority
- checkpoint != current-state Authority
- chat summary/memory != current-state Authority
- evidenceはexact targetへbind
- canonical designはtrusted exact revisionへbind
- 1 Workのcompeting active lineageを許容しない
- unknown conflictを暗黙に正常化しない
- restartはfresh observationからreconcileする
