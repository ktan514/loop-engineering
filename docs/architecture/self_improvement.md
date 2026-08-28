# Generic Self-Improvement Lane

Owner: Issue #3 / #5 migration
Status: Initial canonical architecture draft

## 1. Purpose

Loop Engineering自身の失敗・手動介入・無進捗・重複実行等を観測し、Platform改善候補へ昇格するSelf-Improvement Laneをgenericに定義する。

Self-ImprovementはProduct Workと同じControl Planeを壊さず、改善Issueのstormや自己再帰を防ぐ。

## 2. Separation of concerns

```text
Operational events
→ Health detection
→ Improvement candidate
→ Planning publication
→ normal Work selection
```

Self-ImprovementはPlatform codeをその場で自己書換えする機能ではない。

改善候補を通常のWorkItemへ昇格し、通常のDesign / Implement / Verify / Review / Integrate Gateを通す。

## 3. Generic health events

初期kind:

- `REPEATED_FAILURE`
- `NO_PROGRESS`
- `MANUAL_INTERVENTION`
- `MANUAL_OPERATION_REPEAT`
- `STALE_STATE_RECURRENCE`
- `DUPLICATE_SCHEDULING`
- `RECOVERY_REPETITION`
- `AMBIGUOUS_EFFECT_RECURRENCE`
- `PROVIDER_FLAKINESS`
- `POLICY_GAP`

Product固有のfailure taxonomyは追加metadataへ載せ、Core enumへ無制限に埋め込まない。

## 4. HealthEvent

```text
HealthEvent
- kind
- fingerprint
- occurrence_count
- scope
- affected_work_ids[]
- evidence_refs[]
- severity_hint?
- first_seen
- last_seen
```

fingerprintはsecret/raw payloadを含まないstable normalizationから生成する。

## 5. Improvement candidate

```text
ImprovementCandidate
- improvement_key
- title
- problem
- severity
- scope
- evidence_refs[]
- affected_subjects[]
- suggested_owner
- deduplication_window
```

`improvement_key`はsame underlying problemのduplicate publicationを抑止する。

## 6. Publication boundary

改善候補をどこへIssue/Task化するかはPlanningWriter adapter/Profileが決める。

例:

- Platform defect → `ktan514/loop-engineering`
- Yura固有policy defect → `ktan514/ai-liver-yura`

Coreへ特定repository/Project番号をハードコードしない。

## 7. Ownership routing

候補は少なくとも次へ分類する。

- `PLATFORM_CORE`
- `PLATFORM_ADAPTER`
- `HOST_ENVIRONMENT`
- `PRODUCT_PROFILE`
- `PRODUCT_WORKFLOW`
- `UNKNOWN`

UNKNOWNを自動でPlatform/Productどちらかへ断定しない。必要ならHuman/Management reconciliationへ送る。

## 8. Storm guard

禁止:

- 同じfailureごとに新Issueを無制限生成
- improvement Issue作成失敗を原因にさらにimprovement Issueを再帰生成
- same keyのclosed Issueを理由なく即再作成

必要:

- durable improvement key
- occurrence aggregation
- open/existing Work discovery
- cooldown/reopen policy
- retry budget

## 9. Self-recursion guard

Self-Improvement Lane自身が失敗した場合、同Laneを無限自己起動しない。

例:

```text
Planning publication failure
→ health diagnostic
→ bounded retry/reconciliation
→ external wait/intervention
```

同failureから新しいpublication taskを連鎖生成しない。

## 10. Evidence authority

Health eventはdiagnostic/operational evidenceであり、Product current-state Authorityではない。

改善候補生成時もexternal target stateをfreshに確認する。

例:

「duplicate scheduling recurrence」を検出しても、現在のWorkが実際に重複lineageを持つかはfresh SourceControl/Planning snapshotで再確認する。

## 11. Scheduling

改善Workは通常Workと同じschedulerへ投入できる。

Priorityはpolicyにより決定する。

例:

- safety invariant破壊 → P0
- loop全体をblockする基盤障害 → high
- manual operation reduction → medium
- observability improvement → lower

Self-ImprovementがProduct completion Workを常に奪う固定priorityにしない。

## 12. Human intervention reduction

手動操作が繰り返される場合、改善候補に昇格できる。

ただしHuman approval自体が安全要件の場合、そのapprovalを「自動化すべき無駄」と誤分類しない。

`MANUAL_OPERATION_REPEAT`と`MANDATORY_HUMAN_GATE`を区別する。

## 13. Yura migration

Yura PR #466のSelf-Improvement実装から保持する知見:

- durable improvement key
- health event accumulation
- issue storm guard
- restart-safe health checkpoint
- existing improvement issue reconciliation

汎用化時に除去するもの:

- fixed Yura repository
- fixed Project #7
- Project #6 hard-coded identity
- Yura issue-number based affected work types
- fixed area/status/issue-level strings

これらはProfile/Planning adapter mappingへ移す。

## 14. Hard invariants

- Self-Improvementは自己コードを直接書換えない
- 改善も通常Work Gateを通す
- improvement publication先をCoreへ固定しない
- duplicate/storm/self-recursionを抑止する
- mandatory Human Gateを誤って自動化対象にしない
- health stateをProduct current-state Authorityにしない
