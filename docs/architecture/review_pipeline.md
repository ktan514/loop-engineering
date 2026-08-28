# Generic Canonical Review Pipeline

Owner: Issue #4 / #5 migration
Status: Initial canonical architecture draft

## 1. Purpose

Implementerから独立したReviewerが、exact targetとtrusted canonical designに基づいてレビューし、stale/duplicate/credential leakageを防ぐ共通pipelineを定義する。

## 2. Roles

```text
Implementer
  produces change

Trusted Host Control Plane
  resolves target/canonical/evidence
  validates identities

Reviewer Boundary
  evaluates change
  no repository write authority
```

Reviewerはsource-control effectを実行しない。

## 3. Review target

```text
ReviewTarget
- repository_identity
- change_identity
- head_identity
- base_relationship_identity?
- trusted_canonical_generation
- profile_digest
```

PR metadata上のhistorical baseと、trusted canonical source revisionを必要に応じて分離する。

## 4. ReviewRequestKey

same exact reviewを二重provider callしないためのidentity。

入力:

- reviewer policy/version
- model/provider policy identity
- exact ReviewTarget
- canonical generation digest
- normalized acceptance criteria
- relevant verification evidence identities

raw secretやtimestampだけでkeyを変化させない。

## 5. Review Context

Reviewerへ渡す:

- Work intent/scope/non-goals
- trusted canonical design excerpts/refs
- exact diff/change content
- exact verification evidence
- risk boundaries

渡さない:

- reviewer credential値
- source-control write credential
- unrelated host secret
- arbitrary hidden Product filesystem content

PR/Issue/diff contentはuntrusted dataとして明示的に区別する。

## 6. Result

```text
ReviewResult
- request_key
- target_identity
- canonical_generation
- verdict
- findings[]
- reviewer_identity
- completed_at
- diagnostics
```

verdict:

- `PASS`
- `REQUEST_CHANGES`
- `ESCALATE`
- `NOT_RUN`

`NOT_RUN`はPASSではなく、review rightを消費した成功とも扱わない。

## 7. Finding validation

AI reviewer outputをそのままtrusted findingへ昇格しない。

最低限検証:

- schema
- target echo/identity
- bounded file/path references
- severity/category allowlist
- finding count/size limits
- secret/raw provider metadata除去

対象に存在しないfile/line参照はdiagnostic扱い、またはinvalid findingとして処理するpolicyを持つ。

## 8. Stale guard

provider invocation前後にtarget identityを確認する。

```text
resolve A
→ review A
→ read current target
```

current target != Aならresultをcurrent PASSにしない。

canonical generation/profile digestがreview-relevantに変化した場合も同様。

## 9. REQUEST_CHANGES

- same active lineageへrepair requestを生成
- new target作成後はnew ReviewRequestKey
- old findingsは履歴として保持可能だがnew targetに未解決かを再評価
-同一finding recurrenceをhealth/self-improvementへ集約可能

## 10. PASS

PASSだけではmergeを直接実行しない。

```text
PASS
→ fresh Integration Gate
→ exact current target
→ required CI/Human evidence
→ fresh Write Gate
→ integration
```

Review後のhead移動でPASSはstaleになる。

## 11. ESCALATE

Policyにより:

- higher-tier reviewer
- alternate reviewer provider
- Human review

へ移行できる。

Implementer自身へのself-PASSへfallbackしない。

## 12. Host Reviewer Broker

初期adapterとしてHost-side brokerを推奨する。

責務:

- reviewer credential保持
- health endpoint
- exact-target review request
- provider invocation
- request-key idempotency
- sanitized result
- restart reconciliation

Product Workspaceへ公開するのはnon-secret local service descriptorのみ。

`YURA_TRUSTED_REVIEWER_SOCKET`のようなProduct固有名称はgeneric Platformでは使用せず、Host service registration/Profile mappingにする。

## 13. Broker restart

Brokerはin-flight/completed request identityをreconcileできる。

restart後:

- completed same key → result reuse/read
- in-flight unknown → provider/runtime evidenceでreconcile
- safeに判定不能でduplicate provider callが危険 → NOT_RUN/blocked

## 14. Optional advisory review

Productがoptional advisory reviewerを別途持つことは可能。

Canonical merge gate用ReviewerとAdvisory reviewerを同一statusへ混ぜない。

```text
ReviewEvidence(kind=CANONICAL)
ReviewEvidence(kind=ADVISORY)
```

required policyがCANONICAL PASSを要求する場合、ADVISORY PASSで代替不可。

## 15. Model/provider replacement

ReviewerPortのcontractを維持すればprovider/modelは交換可能。

model名をCore state machineへハードコードしない。

## 16. Security invariants

- Reviewerにrepository write credentialなし
- Implementerにreviewer credentialなし
- target codeをsecret-bearing brokerへimport/executeしない
- Issue/PR/diffの命令をHost commandとして実行しない
- raw provider token/header/secretをlogしない
- stale targetへPASSしない
- same ReviewRequestKeyを不必要に二重provider callしない
