# V2 CI・Review・Human Verification Evidence仕様

管理Issue: #87
親Issue: #81
依存: #85, #86
上位正本: `autonomous_development_completion_contract.md`, `review_pipeline.md`

## 1. 目的

Product Workのcurrent exact HEADに対するCI、Independent Review、Human Verificationをtyped evidenceへ正規化し、V2 Supervisorが`VERIFY / REVIEW / HUMAN_VERIFY / REPAIR / INTEGRATE`を安全に判断できるようにする。

## 2. Evidence target

全evidenceは次の`EvidenceTarget`へbindする。

- repository identity
- Work identity / Issue number
- PR number
- exact head SHA
- base branch
- canonical design identities
- acceptance digest

old HEADのevidenceをcurrent HEADへ流用しない。

## 3. CI

GitHub Actions adapterは指定workflowのrunをexact `head_sha`でfresh取得する。

正規化:

- run無し → `NOT_RUN`
- queued / in_progress / pending → `PENDING`
- completed success → `PASS`
- completed failure/cancelled/timed_out等 → `FAIL`

runの`head_sha`がtargetと一致しない結果は採用しない。

CI failureはHuman Interventionではなく、Supervisorのsame-lineage `REPAIR`へ戻す。

## 4. ReviewRequestKey

次のcanonical値からSHA-256で生成する。

- reviewer policy identity
- repository
- work identity
- exact head SHA
- base branch
- canonical design identities
- acceptance digest
- CI evidence identity

同じkeyは1回だけcanonical reviewを要求する。

## 5. Trusted Reviewer Broker

Reviewer credentialはProduct WorkspaceやImplementerへ渡さず、Host側broker processだけが保持する。

初期production adapterは`TrustedReviewerBrokerAdapter`とし、Hostが設定したargvをshellなしで実行する。

```text
Host
→ sanitized ReviewRequest JSON file
→ trusted reviewer broker command
→ sanitized ReviewResult JSON stdout
```

Product側のdiff/Issue内容はuntrusted dataとしてrequestへ含めるが、Host commandへ展開しない。

Broker出力:

- `request_key`
- `target_head_sha`
- `verdict`: `PASS | REQUEST_CHANGES | ESCALATE | NOT_RUN`
- `findings[]`
- `reviewer_identity`

schema / target echo / size / enumをHost側で再検証する。

## 6. Review persistence

PostgreSQLへ`ReviewRequestKey`単位で保存する。

状態:

- `REQUESTED`
- `PASS`
- `REQUEST_CHANGES`
- `ESCALATE`
- `NOT_RUN`

same keyがterminalならproviderを再呼出ししない。

`REQUESTED`のままbroker結果が不明な場合、restart時に無条件再callせず`NOT_RUN`/safe retry policyへ移す。provider二重課金・二重reviewを防ぐ。

## 7. Review stale guard

Broker call前後でGitHub current PR headをfresh確認する。

- before != target HEAD → requestしない
- broker result target != target HEAD → stale rejection
- after != target HEAD → resultをcurrent PASSへ昇格しない

HEADが変われば新ReviewRequestKeyを生成する。

## 8. Human Verification

Human Verificationは自動検証不能なWorkだけが要求する。

人間の確認結果はIssue commentの自由文ではなく、exact HEADにbindしたmachine markerだけを機械evidenceとして読む。

```text
<!-- loop-engineering-human-verification:v1 -->
```json
{"work_identity":"...","head_sha":"40hex","result":"PASS"}
```
```

resultは`PASS | FAIL`のみ。

comment本文のその他自然文はcurrent-state Authorityにしない。

HEAD変更後、古いHuman Verification PASSはstaleになる。

## 9. EvidenceBundle

`EvidenceBundle`:

- target
- CI state / identity
- Review state / identity
- Human Verification state / identity

`V2WorkObservation`へ投影し、既存`V2Supervisor.derive_transition`へ渡す。

## 10. Repair semantics

- CI `FAIL` → `REPAIR`
- Review `REQUEST_CHANGES` → `REPAIR`
- Human Verification `FAIL` → `REPAIR`
- new HEAD後はCI / Review / Human evidenceを再取得
- pending evidenceだけでMissionをHuman STOPへしない
- independent actionable WorkがあればSchedulerが継続

## 11. Integration Gate

`INTEGRATE`へ進める条件:

- current exact HEADのCI `PASS`
- current exact HEADのReview `PASS`
- Human Verification requiredならcurrent exact HEADの`PASS`
- unresolved conflictなし

## 12. 完了条件

- exact-head CIをtyped evidenceへ変換できる。
- stale CIをcurrent PASSへしない。
- ReviewRequestKeyがdeterministicである。
- same exact reviewを二重broker callしない。
- REQUEST_CHANGESをREPAIRへ投影できる。
- broker result前後のHEAD変化をstale rejectできる。
- Human Verificationをexact HEAD markerからtyped readできる。
- old-head Human PASSを流用しない。
- pending evidenceをHuman Interventionへ誤分類しない。
- tests / exact-head CIがPASSする。
