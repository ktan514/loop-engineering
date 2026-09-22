# V2 Local Quality Loop詳細設計

Owner: Issue #101
Parent: Issue #81
Design source: Issue #98 / `docs/architecture/local_llm_coder_integration.md`
Status: canonical implementation design

## 1. 目的

IMPLEMENT / REPAIR後のexact targetに対し、決定論的なローカル検証、fresh Self Review、finding検証、同一lineage REPAIRをLoop Engineeringが自動反復し、成立条件を満たしたtargetだけへ`LOCAL_PASS` evidenceを発行する。

本設計はLocal Quality Loopだけを対象とする。External Review LevelはIssue #102、Runner全体compositionはIssue #88で接続する。

## 2. Quality target

Local Quality LoopのtargetはHEAD SHAだけではなく、Workspaceの変更状態を含む。

```text
LocalQualityTarget
- repository_identity
- work_identity
- exact_head_sha
- change_identity
- active_lineage_identity
- canonical_design_identities[]
- acceptance_digest
- scope_paths[]
```

`change_identity`はstaged / unstaged / untrackedを含むWorkspace identityである。同じHEADでもchange identityが変化した場合、旧Local Verification / Local Review / LOCAL_PASS evidenceはstaleとする。

## 3. Durable state

PostgreSQLへWorkごとのcurrent quality stageを保存する。

```text
LocalQualityState
- work_identity
- target_head_sha
- change_identity
- stage
- review_cycle
- no_progress_count
- last_progress_fingerprint
- verification_identity?
- review_request_key?
- review_identity?
- local_pass_identity?
```

stage:

- `VERIFY_LOCAL`
- `LOCAL_REVIEW`
- `REPAIR`
- `LOCAL_PASS`
- `BLOCKED`

target変更時は同じWorkのstateを新targetへ再bindし、verification/review/local-pass identityをクリアして`VERIFY_LOCAL`へ戻す。旧evidenceは履歴として保持してもcurrent PASSへ流用しない。

## 4. Deterministic verification

Product固有commandはtrusted Profile / Host policyから`VerificationCommandDescriptor`として渡す。

```text
VerificationCommandDescriptor
- identity
- argv[]
- working_directory
- timeout_seconds
- required
```

任意shell文字列を直接実行しない。command開始前後にtarget identityをreadbackし、検証中にtargetが変化した場合はPASSへ昇格しない。

required commandが全てPASSした場合だけLocal Verification PASS evidenceを作る。required FAIL / timeout / readback不一致はLOCAL_PASSを成立させない。

## 5. Local Self Reviewer

Self Reviewerは`local-llm-coder` Worker v1を次の固定契約で呼ぶ。

```text
role = SELF_REVIEWER
transition = REVIEW
effect_requirement = MUST_NOT_CHANGE
input_target_identity = exact_head_sha
expected_change_identity = change_identity
```

Reviewerはfresh sessionで起動し、Product write authorityを持たない。Worker process終了コードや自然文をAuthorityにせず、versioned structured resultとHost readbackを使用する。

Completion Contract未充足、malformed result、target mismatch、mutation検出はPASSではない。`INCOMPLETE`は`LOCAL_REVIEW`のunfinished stateとして同じreview request identityへ再開可能とする。

## 6. Finding validation

Worker findingを無条件にFixerへ渡さない。Hostは各findingを次へ分類する。

- `APPROVED`
- `REJECTED`
- `DUPLICATE`

検証項目:

- schemaが完全
- finding identityが一意
- target head / change identityがcurrent targetと一致
- pathが安全なrepository relative path
- pathがWork scope内
- severityが`BLOCKING` / `NON_BLOCKING`
- basis / evidence / impact / suggested_fixが空でない
- 同一target上の同一内容findingをduplicateとして識別

`APPROVED BLOCKING`だけをFixerの`approved_findings`へ渡す。`REJECTED` / `DUPLICATE`を修正対象へ昇格しない。`REJECTED`が残るreviewはLOCAL_PASSにせず、同一targetで再reviewする。反復して進展しない場合はno-progress guardでBLOCKする。

NON_BLOCKING findingは履歴へ保存するが、APPROVED BLOCKINGが0、NEEDS_CLARIFICATIONが0、その他Completion条件が成立する場合はHost側Local Review GateをPASSとしてよい。

## 7. LOCAL_PASS

同じexact targetについて次を全て満たす場合だけ発行する。

- required deterministic verification PASS
- fresh Local Self Reviewがterminal
- APPROVED BLOCKING = 0
- REJECTED finding = 0
- Completion Contract充足
- review前後でtarget identity不変
- no-progress guard未発火

identityはtarget / verification / review evidenceを含むdeterministic hashとし、target変更で必ず変化する。

## 8. REPAIR loop

APPROVED BLOCKING findingがある場合:

```text
LOCAL_REVIEW
→ validate findings
→ REPAIR
→ local-llm-coder FIXER
→ fresh target readback
→ VERIFY_LOCAL
→ fresh LOCAL_REVIEW
```

Fixerにはsame active lineage、current exact HEAD、current change identity、APPROVED findingだけを渡す。

Repair成功後にtarget identityが変化しない場合はno-progressとみなし、同じfinding fingerprintの反復をcountする。

## 9. Bounded loop

既定の自動Local Review / Repair cycle上限は12とする。Production固有policyへ将来外出しできるようCoordinator引数として保持し、無限反復は許可しない。

同じtarget / 同じapproved blocking fingerprintが進捗なしで反復する場合、無限loopせず`BLOCKED / LOCAL_REPAIR_NO_PROGRESS`または`LOCAL_FINDING_VALIDATION_NO_PROGRESS`へ移行する。

process restartでcountを失わない。

## 10. Restart

再起動時:

```text
PostgreSQL LocalQualityState
→ current Workspace target readback
→ target一致
   ├─ VERIFY_LOCAL: 未完verificationから実行
   ├─ LOCAL_REVIEW: review requestをreconcile / 再開
   ├─ REPAIR: approved findingを復元してsame lineage Fixer
   └─ LOCAL_PASS: evidenceをreadback
→ target不一致
   └─ stale evidenceをcurrentへ昇格せずVERIFY_LOCALへ再bind
```

Issue comment自然文やprovider session IDをcurrent quality stage Authorityにしない。

## 11. Supervisor境界

`V2WorkObservation`はLocal Qualityを有効にしたWorkについて次を持つ。

- `local_quality_required`
- `local_verification_state / identity`
- `local_review_state / identity`
- `local_pass_identity`

Local Quality有効時の遷移:

```text
exact targetあり
→ VERIFY_LOCAL
→ LOCAL_REVIEW
→ LOCAL_PASS
→ External Review（#102）
```

既存Product互換のため、`local_quality_required = false`では従来VERIFY / REVIEW経路を維持する。

## 12. Hard invariants

- Local ReviewerをImplementerと同一sessionにしない
- Local Reviewerへwrite authorityを与えない
- process終了だけでstage完了にしない
- target変更後に旧LOCAL_PASSを使わない
- finding自由文をHost commandとして実行しない
- APPROVED finding以外をFixerへ渡さない
- REPAIRはsame lineageを維持する
- review / repairを無限反復しない
- secretをTaskPacket / evidence / logへ保存しない
