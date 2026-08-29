# Core状態契約

管理Issue: #10
状態: 実装設計
上位正本:
- `docs/architecture/core_foundation_implementation.md`
- `docs/architecture/authority_and_state.md`

## 1. 目的

Generic Core Foundationの最下層として、提供元非依存・不変・決定論的に直列化できる状態（State）・識別子（Identity）・証拠（Evidence）契約を具体化する。

本書の型は提供元アダプター（provider adapter）から正規化済みsnapshotを受け取るDomain契約であり、GitHub/Codex/OpenAI/filesystem等のclient objectを保持しない。

## 2. モジュール責務

```text
src/loop_engineering/core/
├─ serialization.py  # 正規JSON / digestのみ
├─ identities.py     # SourceIdentity / ExecutionTarget / CanonicalGeneration
├─ conflicts.py      # Conflict taxonomyと不変Conflict
├─ evidence.py       # 厳密対象Evidence
├─ models.py         # RunGoal / WorkItem / Lineage / Checkpoint / ObservationEpoch
└─ __init__.py       # Generic Coreの安定公開API
```

`serialization.py`はDomain型をimportせず、dataclass / Enum / datetime / tuple / mapping等の汎用値だけを正規化する。これにより循環依存を避ける。

## 3. 共通不変条件

- 公開DTOは原則 `@dataclass(frozen=True, slots=True)`
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

Identity equality/hashは `provider_kind / object_kind / stable_id / revision_id` で判定する。`observed_at` は観測metadataであり比較・hash対象外とする。同じHEADやblobを後から再観測してもexact identityは変化しない。

Canonical serializationでは監査用snapshotとして`observed_at`も出力するが、identity-only digestが必要な箇所では`identity_payload()`相当の4 identity fieldだけを用いる。

provider objectを内包しない。

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

Review/CI/mergeでhead必須かどうかの遷移固有判定は後続Decision/Effect Workの責務。本型はtarget identityの器だけを提供する。

ExecutionTargetのexact-target比較も各SourceIdentityのidentity fieldを基準とし、再観測時刻だけの差をtarget movementとみなさない。

## 6. CanonicalGeneration

```text
CanonicalGeneration
- refs: tuple[SourceIdentity, ...]
- normalized_digest: str
```

`from_refs()` はrefsをidentity keyでstable sortし、順序差だけでdigestが変化しないようにする。空generationは禁止。

Digest inputは各refの `provider_kind / object_kind / stable_id / revision_id` だけとし、`observed_at` を除外する。同じcanonical revisionsを別時刻に再観測してもgeneration digestは変化しない。

Digestはidentity projectionのcanonical JSONをSHA-256し、lowercase hexとする。

## 7. Conflict

分類（taxonomy）:

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

Dependency-ready/actionable等の派生判断はDecision Coreで行い、本型へprovider-specific status logicを入れない。

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

「1 Work = 1 active lineage」の判定はreconciliation責務であり、本契約はclassification付きsnapshotのみ提供する。

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

`project_profile_snapshot`はProjectProfileSnapshot型が確立するまでSourceIdentityでprofile revisionだけをbindする暫定generic boundaryとする。型を置換する場合は設計更新を先行する。

## 13. 正規直列化

公開helper:

```text
canonical_json(value) -> str
canonical_digest(value) -> str
```

規則:
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

Snapshot全体のcanonical JSONには観測timestampを含めてよい。一方、SourceIdentity equality、ExecutionTarget identity比較、CanonicalGeneration digest等の**identity-only operation**では観測timestampを除外したidentity projectionを使用する。

## 14. 検証項目

最低限:
- blank identity拒否
- naive datetime拒否
- frozen mutation拒否
- 同一revisionを異なる`observed_at`で再観測してもSourceIdentity equality/hash一致
- equal identity projectionのdigest一致
- revision差でidentity digest変化
- CanonicalGeneration refs入力順・観測時刻が違ってもdigest一致
- unsupported arbitrary object/set拒否
- ObservationEpochを含むnested serialization決定論性
- provider SDK/importがCoreに存在しない

## 15. 対象外

- dependency-ready / actionable判定
- reconciliation / scheduler
- ResumeCertificate / TaskPacket
- Write Gate / Effect Receipt判定
- RuntimeStore persistence
- provider adapter conversion
- Pydantic等external schema framework導入
