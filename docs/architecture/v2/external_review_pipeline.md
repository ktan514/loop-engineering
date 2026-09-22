# V2 External Review Pipeline詳細設計

Owner: Issue #102
Parent: Issue #81
Design source: Issue #98 / docs/architecture/review_pipeline.md
Status: canonical implementation design

## 1. 目的

LOCAL_PASS後のexact targetに対し、設定された外部Review Levelを順番に自動実行し、required Levelをすべて通過した場合だけEXTERNAL_PASSを成立させる。

特定Productや特定model名をCoreへ固定しない。ProductionごとのHost設定でReview Levelを差し替えられる汎用pipelineとする。

## 2. Review Level policy

各Levelは次を持つ。

```text
ReviewLevelPolicy
- level
- provider
- model
- api_base
- credential_env
- required
- timeout_seconds
- context_policy
- escalation_policy
- passes_required
```

`passes_required`は同一Levelで要求するfresh review回数である。たとえば低レベルAPIを2回、高レベルAPIを2回通す構成は次で表現する。

```ini
[review.level.1]
provider = openai
model = <low-level-model>
required = true
passes_required = 2

[review.level.2]
provider = openai
model = <high-level-model>
required = true
passes_required = 2
```

CoreはLOW/HIGHという名前を知らない。Level数、provider、model、必要回数はProduction policyで決める。

## 3. exact target

External Reviewは次へbindする。

```text
ExternalReviewTarget
- repository_identity
- work_identity
- issue_number
- pr_number
- exact_head_sha
- change_identity
- active_lineage_identity
- canonical_design_identities[]
- acceptance_digest
- scope_paths[]
- local_pass_identity
- acceptance_checks[]
- canonical_context[(reference, content)]
- verification_evidence[]
- non_goals[]
```

HEADまたはchange identityが変わった場合、旧External Review evidenceはcurrent targetへ流用しない。

外部API reviewerはProduct Workspaceへ直接tool accessを持つとは限らないため、Hostはtrusted canonical sourceから解決した設計本文、受入条件、ローカル検証証拠をbounded review contextとして渡す。identity/digestだけを渡して「設計を確認した」と扱わない。

## 4. fresh pass

同一Levelで複数PASSを要求する場合も、各passは別ReviewRequestである。

ReviewRequestKeyへ最低限次を含める。

- exact target
- local_pass_identity
- level
- pass_index
- provider/model policy identity
- canonical design identities
- acceptance digest
- canonical context digest
- normalized acceptance checks
- verification evidence identities/summaries

したがって同一passの重複provider callは抑止する一方、pass 1とpass 2は別callとして実行される。

## 5. result

External Reviewerはversioned structured resultを返す。

```text
ExternalReviewResult
- schema_version
- request_key
- target_head_sha
- target_change_identity
- verdict
- findings[]
- reviewer_identity
- diagnostics[]
```

verdict:

- PASS
- REQUEST_CHANGES
- ESCALATE
- NOT_RUN

自由文やHTTP statusだけをPASS Authorityにしない。

## 6. finding

External findingはLocal findingと同じ安全schemaへ正規化する。

```text
Finding
- finding_identity
- severity
- path
- location
- problem
- basis
- evidence
- impact
- suggested_fix
```

Hostでschema/path/scopeを検証し、valid BLOCKING findingだけをsame lineage REPAIRへ渡す。

## 7. REQUEST_CHANGES

valid blocking findingがある場合:

```text
CURRENT_EXTERNAL_LEVEL
→ REQUEST_CHANGES
→ same lineage REPAIR
→ VERIFY_LOCAL
→ fresh LOCAL_REVIEW
→ LOCAL_PASS
→ External Review Level 1 / pass 1
```

修正でtargetが変わるため、旧targetのLocal/External PASSをすべてstaleとする。途中Levelから再開しない。

## 8. PASS

1つのLevelについて`passes_required`回のfresh PASSを取得してから次Levelへ進む。

全required Levelを満たした場合だけ:

```text
EXTERNAL_PASS
```

を発行する。

optional LevelがNOT_RUNの場合はpolicy上skip可能だが、required LevelのNOT_RUNをPASSへ読み替えない。

## 9. ESCALATE

`escalation_policy`は次を許可する。

- NEXT_LEVEL
- HUMAN
- BLOCK

NEXT_LEVELは明示的にpolicyで許可された場合だけ次Levelへ移る。
HUMANはHuman Verification/Interventionへ返す。
BLOCKは自動継続を停止する。

Implementer自身の自己承認へfallbackしない。

## 10. Provider Adapter

Coreはprovider固有APIへ依存しない。

初期AdapterとしてOpenAI-compatible Chat Completions境界を実装する。Adapterはcredential env名からruntime secretを取得し、request/log/DBへsecret値を保存しない。

provider呼出し前後にexact PR HEADをreadbackする。review中にtargetが変わった場合、結果をPASSへ昇格しない。

## 11. Durable state

PostgreSQLへ次を保存する。

```text
ExternalReviewState
- work_identity
- target_head_sha
- change_identity
- local_pass_identity
- level_index
- pass_index
- completed_evidence[]
- current_request_key?
- status
- diagnostics[]
```

restart時はcurrent targetをreadbackし、一致する場合だけ未完Level/passから再開する。不一致ならLevel 1/pass 1へ再bindする。

## 12. Backward compatibility

既存`[models]`のsingle reviewer設定は、明示的な`[review.level.N]`がない場合にLevel 1 / passes_required=1へ正規化する。

これにより既存設定を壊さず、Productionごとに複数Levelへ拡張できる。

## 13. Hard invariants

- provider/modelをCoreへ固定しない
- required LevelをskipしてEXTERNAL_PASSにしない
- pass_indexを省略してfresh reviewを使い回さない
- target変更後に旧review evidenceを使わない
- REQUEST_CHANGESはsame lineage REPAIRへ戻す
-修正後はLocal Quality LoopとExternal Level 1から取り直す
- API credentialをTaskPacket/DB/logへ保存しない
- NOT_RUNをPASSへ読み替えない
- same ReviewRequestKeyを二重callしない
