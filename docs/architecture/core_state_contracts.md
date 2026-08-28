# Core State Contracts

Owner: Issue #10
Status: Implementation design
Canonical parents:
- `docs/architecture/core_foundation_implementation.md`
- `docs/architecture/authority_and_state.md`

## 1. Purpose

Generic Core Foundationの最下層として、provider非依存・immutable・deterministically serializableなState / Identity / Evidence契約を具体化する。

本書の型はprovider adapterからnormalized snapshotを受け取るDomain contractであり、GitHub/Codex/OpenAI/filesystem等のclient objectを保持しない。

## 2. Module ownership

```text
src/loop_engineering/core/
├─ serialization.py  # canonical JSON / digest only
├─ identities.py     # SourceIdentity / ExecutionTarget / CanonicalGeneration
├─ conflicts.py      # Conflict taxonomy and immutable Conflict
├─ evidence.py       # exact-target Evidence
├─ models.py         # RunGoal / WorkItem / Lineage / Checkpoint / ObservationEpoch
└─ __init__.py       # stable public exports for Phase I
```

`serialization.py`はDomain型をimportせず、dataclass / Enum / datetime / tuple / mapping等のgeneric valueだけを正規化する。これにより循環依存を避ける。

## 3. Common invariants

- public DTOは原則 `@dataclass(frozen=True, slots=True)`
- identityを構成する文字列は空文字・空白のみを禁止
- timestampはtimezone-aware `datetime`のみ許可
- collectionはimmutable tupleを基本とする
- secret / token / credential / provider clientをfieldに持たない
- provider固有numeric ID型やSDK objectをCoreへ持ち込まない
- unknown provider/work/evidence kindをCore enumへ無制限追加しない箇所はnormalized stringを使う

## 4. SourceIdentity

```text
SourceIdentity
- provider_kind: str
- object_kind: str
- stable_id: str
- revision_id: str
- observed_at: datetime
```

意味:
- `stable_id`: revisionが変わっても同一objectを指すprovider-neutral string
- `revision_id`: current stateを区別するimmutable revision string
- `observed_at`: observation evidence timestampでありstalenessの唯一の根拠ではない

Equalityは全field一致。provider objectを内包しない。

## 5. ExecutionTarget

```text
ExecutionTarget
- repository_identity: SourceIdentity
- workspace_identity: SourceIdentity | None
- ref_identity: SourceIdentity | None
- base_identity: SourceIdentity | None
- head_identity: SourceIdentity | None
- canonical_generation_digest: str | None
```

Review/CI/mergeでhead必須かどうかのtransition-specific判定は後続Decision/Effect Workの責務。本型はtarget identityの器だけを提供する。

## 6. CanonicalGeneration

```text
CanonicalGeneration
- refs: tuple[SourceIdentity, ...]
- normalized_digest: str
```

`from_refs()` はrefsをidentity keyでstable sortし、順序差だけでdigestが変化しないようにする。空generationは禁止。

Digestはcanonical JSONのSHA-256 lowercase hexとする。

## 7. Conflict

Taxonomy:

```text
ConflictKind
- AUTHORITY_UNAVAILABLE
- AUTHORITY_CONTRADICTION
- PROFILE_UNRESOLVED
- CANONICAL_UNRESOLVED
- CANONICAL_MISMATCH
- MULTIPLE_ACTIVE_LINEAGES
- UNKNOWN_LINEAGE
- BASE_IDENTITY_MISMATCH
- HEAD_IDENTITY_MISMATCH
- UNEXPLAINED_TARGET_ADVANCE
- CHECKPOINT_LIVE_MISMATCH
- CI_TARGET_MISMATCH
- REVIEW_TARGET_MISMATCH
- VERIFICATION_MISMATCH
- LEASE_CONFLICT
- FORBIDDEN_CAPABILITY
- RUNTIME_STATE_UNAVAILABLE
```

```text
ConflictScope
- GLOBAL
- PROJECT
- WORK
- LINEAGE
- TRANSITION
- WORKSPACE
- SECURITY
```

```text
ConflictSeverity
- INFO
- WARNING
- BLOCKING
```

```text
Conflict
- kind
- scope
- severity
- subject_identity: SourceIdentity | None
- evidence_refs: tuple[SourceIdentity, ...]
- resolution_policy: str
```

## 8. Evidence

```text
Evidence
- evidence_id: str
- kind: str
- target_identity: ExecutionTarget
- source_identity: SourceIdentity
- result: str
- observed_at: datetime
```

`kind/result`はprovider-neutral normalized string。PASS等の意味付け・current evidence判定は後続Decision Coreが行う。

Evidenceを別HEADへ自動継承するlogicは本Workに含めない。

## 9. RunGoal / WorkItem

```text
RunGoalKind
- SINGLE_WORK
- PROJECT_QUEUE
- MILESTONE
- MIGRATION
- MISSION
```

```text
RunGoal
- goal_id: str
- kind: RunGoalKind
- authority_refs: tuple[SourceIdentity, ...]
- completion_policy: str
- scope: str
```

```text
WorkItem
- work_id: str
- source_identity: SourceIdentity
- work_type: str
- status: str
- priority: str | None
- dependencies: tuple[str, ...]
- canonical_design_refs: tuple[SourceIdentity, ...]
- verification_policy: str | None
- lineage_refs: tuple[str, ...]
```

Dependency-ready/actionable等のderived decisionは#11で実装し、本型へprovider-specific status logicを入れない。

## 10. Lineage

```text
LineageClassification
- ACTIVE
- SUPERSEDED
- VALIDATION_ONLY
- CI_ONLY
- ABANDONED
- MERGED
- UNKNOWN
```

```text
Lineage
- lineage_id: str
- work_id: str
- classification
- repository_identity: SourceIdentity
- branch_identity: SourceIdentity | None
- pr_identity: SourceIdentity | None
- base_identity: SourceIdentity | None
- head_identity: SourceIdentity | None
- created_from: SourceIdentity | None
- supersession: str | None
```

「1 Work = 1 active lineage」の判定は#11 reconciliation responsibilityであり、本Workはclassification付きsnapshotのみ提供する。

## 11. Checkpoint

```text
Checkpoint
- checkpoint_id: str
- goal_id: str
- work_id: str | None
- lineage_id: str | None
- last_observation_id: str
- expected_target_identity: ExecutionTarget | None
- last_confirmed_effects: tuple[str, ...]
- last_verification: tuple[Evidence, ...]
- pending_external: tuple[SourceIdentity, ...]
- next_expected_transition: str | None
- created_at: datetime
```

Checkpointはcurrent-state Authorityではない。本型にprovider mutation methodを持たせない。

## 12. ObservationEpoch

```text
ObservationEpoch
- observation_id: str
- observed_at: datetime
- run_goal_snapshot: RunGoal
- project_profile_snapshot: SourceIdentity | None
- source_control_snapshots: tuple[SourceIdentity, ...]
- planning_snapshots: tuple[SourceIdentity, ...]
- work_snapshots: tuple[WorkItem, ...]
- lineage_snapshots: tuple[Lineage, ...]
- ci_snapshots: tuple[Evidence, ...]
- review_snapshots: tuple[Evidence, ...]
- verification_snapshots: tuple[Evidence, ...]
- canonical_design_snapshots: tuple[SourceIdentity, ...]
- runtime_snapshots: tuple[SourceIdentity, ...]
- diagnostics: tuple[str, ...]
```

`project_profile_snapshot`はPhase IIでProjectProfileSnapshot型が確立するまでSourceIdentityでprofile revisionだけをbindする暫定generic boundaryとする。Phase IIで型を置換する場合は設計更新を先行する。

## 13. Canonical serialization

公開helper:

```text
canonical_json(value) -> str
canonical_digest(value) -> str
```

Rules:
- UTF-8 JSON
- key sort
- compact separator
- Enumは`.value`
- timezone-aware datetimeはUTCへ正規化してISO 8601 `Z`
- dataclassはfield名mapへ再帰変換
- tuple/listはarray
- mapping keyはstrのみ
- set / arbitrary object / naive datetimeはTypeErrorまたはValueErrorで拒否
- digestはcanonical JSON UTF-8 bytesのSHA-256 lowercase hex

Serializationはcurrent-state Authorityを作るものではなく、equality/idempotency/snapshot identityを補助するdeterministic representationである。

## 14. Validation tests

最低限:
- blank identity拒否
- naive datetime拒否
- frozen mutation拒否
- equal objectのcanonical JSON/digest一致
- revision差でdigest変化
- CanonicalGeneration refs入力順が違ってもdigest一致
- unsupported arbitrary object/set拒否
- ObservationEpochを含むnested serialization決定論性
- provider SDK/importがCoreに存在しない

## 15. Non-goals

- dependency-ready / actionable判定
- reconciliation / scheduler
- ResumeCertificate / TaskPacket
- Write Gate / Effect Receipt判定
- RuntimeStore persistence
- provider adapter conversion
- Pydantic等external schema framework導入
