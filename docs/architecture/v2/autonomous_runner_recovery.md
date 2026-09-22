# V2 Autonomous Runner / Recovery仕様

管理Issue: #88
親Issue: #81
依存: #83, #84, #85, #86, #87
上位正本: `autonomous_development_completion_contract.md`

## 1. 目的

Goal bootstrap / Planning / Work Queue / Supervisor / Codex Implementer / GitHub development lineage / exact-head evidence / Integrationを、1つのV2 Application Runnerへ接続する。

人間がWorkごとのTaskPacketやeffectを逐次発行しなくても、初期Product RegistrationとGoal DefinitionからGoal完了まで有用な遷移を継続できることを目的とする。

旧actual-host continuous loopを復活させない。Issue本文・Issueコメント・PR本文の自然文をcurrent stateやresume Authorityにしない。

## 2. Authority

- Requirement / acceptance / dependency / planning field: GitHub Issue / Project
- current Work / transition / checkpoint / dispatch / effect intent / review request: PostgreSQL
- branch / PR / exact HEAD / CI / review / merge: live provider readback
- Goal / Profile: Host Product Registrationで固定したtrusted source

restart時も同じAuthorityを使用する。

## 3. Runner iteration

1回のiterationは次の順序とする。

```text
PREFLIGHT
→ BOOTSTRAP / PLAN (必要な場合のみ)
→ QUEUE SYNCHRONIZE
→ LINEAGE OBSERVE
→ EVIDENCE OBSERVE
→ SUPERVISOR DECIDE
→ DISPATCH INTENT RECORD
→ TRANSITION EXECUTE または SAFE YIELD
→ FRESH READBACK
→ RUNTIME CHECKPOINT
```

1 iterationに複数の外部mutationを無制限に詰め込まない。外部effectを持つtransitionは既存のDB intent/readback契約を使用する。

## 4. AutonomousRuntimeState

PostgreSQLへProduct/Goal単位のrunner状態を保持する。

- `runtime_identity`
- `product_key`
- `repository`
- `goal_revision`
- `status`: `ACTIVE | WAITING | INTERVENTION_REQUIRED | COMPLETED`
- `current_work_identity?`
- `last_schedule_key?`
- `last_progress_fingerprint?`
- `no_progress_count`
- `last_detail`
- `completed_at?`

さらにdispatch journalを保持する。

- `schedule_key`
- `runtime_identity`
- `work_identity`
- `transition`
- `status`: `DISPATCHED | COMPLETED | WAITING | FAILED | SUPERSEDED`

同じexact observationから生成された同じScheduleKeyを、restart後に無条件再dispatchしない。

## 5. Progress fingerprint

Runnerはiteration開始/終了時にtyped stateからfingerprintを生成する。

入力:

- goal revision
- current Work identity
- Work observation identities/revisions
- exact HEAD
- CI/review/Human evidence identities
- latest packet/checkpoint identities
- pending effect有無
- Supervisor decision

同じfingerprintで同じScheduleKeyを繰り返す場合、provider再送を行わない。

bounded `no_progress_count` を超えた場合:

- 外部待ちの根拠がある: `WAITING`
- provider/DB conflictや安全証明不能: `INTERVENTION_REQUIRED`
- repair可能なCI/review failure: REPAIRへ戻す

## 6. Lineage observation

#84 Queueのtyped WorkDefinitionに、live GitHub development lineageを重ねる。

branchはHost Registrationの`work_branch_template`からWork Issue番号を使って決定する。

観測:

- remote branch存在/HEAD
- open PR head/base/draft
- competing open PR
- merged state

0件なら未実装Work、1件ならactive lineage、複数競合ならfail-closed。

historical closed PRをcurrent PRとして採用しない。

## 7. Evidence observation

active PRが存在するWorkだけ #87 EvidenceCoordinatorへ渡す。

- CI PENDING / NOT_RUN: safe wait
- CI FAIL: REPAIR
- CI PASS: independent reviewをexact HEADでensure
- Review REQUEST_CHANGES: REPAIR
- Review PASS: Human Verification policy確認
- Human pending: safe wait
- all PASS: INTEGRATE

old-head evidenceは採用しない。

## 8. Transition execution

### DESIGN / IMPLEMENT / REPAIR

- Supervisor decisionから`DevelopmentTaskPacket`を組み立てる。
- #85 proposal modeを呼ぶ。
- proposalを#86 trusted materializerへ渡す。
- active lineageを#86 remote effectへpublishする。
- fresh PR/head readbackを行う。

DESIGN成功後は変更されたcanonical design targetのidentityを新HEADへbindし、次iterationでIMPLEMENT可能にする。

### VERIFY / REVIEW / HUMAN_VERIFY

これらはevidence observerが外部状態を確認する段階であり、pending状態でmutationを作らない。

必要なprovider requestは#87 ReviewCoordinatorのrequest-key契約だけで行う。

### INTEGRATE

- exact current HEAD
- CI PASS
- review PASS
- required Human PASS
- competing lineageなし
- pending/UNCERTAIN effectなし

をfresh確認後、既存V2 MERGE effectのintent/readback契約でmergeする。

### COMPLETE_WORK

merge readback後、DB lifecycleを`COMPLETED`へ進め、Product Issueをcloseし、Project StatusをDoneへ投影する。Issue close/Project updateもtarget限定readbackを持つ。

## 9. WaitingとHuman Intervention

`WAITING`:

- CI pending
- review provider pending
- Human Verification pending
- dependency pending
- lease held
- provider eventual consistency

`INTERVENTION_REQUIRED`:

- duplicate/competing current lineage
- target identity conflict
- unresolved UNCERTAIN effect
- DB corruption/unavailable
- trusted cleanup証明不能
- required provider credential/config欠落
- policyで人間判断が必須なreview escalation

repair可能なtest/CI/review failureをHuman Interventionへ送らない。

## 10. Restart / crash recovery

起動時にPostgreSQLからruntime/work/effect状態を読む。

- pending `INTENT_RECORDED` / `UNCERTAIN` effectはfresh readback優先
- CONFIRMED済みeffectを再送しない
- active WorkはQueue/live lineageから再構成
- last ScheduleKeyと同一stateならduplicate dispatchを抑止
- Issueコメント自然文をparseしてresumeしない

crash window:

- dispatch intent前: 再decide可
- dispatch intent後 / provider call前: journalにより同一ScheduleKey再送を抑止し、transition固有readbackでreconcile
- provider call後 / outcome確定前: provider/live stateをreadback
- outcome後 / checkpoint前: DB terminal stateからcheckpointを再構成

## 11. Goal completion

Goal完了は次をすべてfreshに満たす場合のみ。

- 全planned Work lifecycle `COMPLETED`
- 全Work Issue/Project planning stateが完了条件と整合
- pending/UNCERTAIN effectなし
- current Workなし
- Goal acceptance evaluator PASS

完了後runtimeを`COMPLETED`へ確定し、自律dispatchを停止する。

## 12. Platform self-improvement境界

Product側の修正で解消できないPlatform contract/safety/provider/recovery不具合はSelfImprovementPortへtyped reportを渡す。

Product Work IssueへPlatform内部の実装詳細を混ぜない。SelfImprovement target未設定時に別Repositoryへ勝手に書き込まない。

## 13. Runner API

```text
V2AutonomousRunner.run(registration, max_iterations=N)
→ AutonomousRunResult
```

status:

- `GOAL_COMPLETED`
- `PROGRESSED`
- `WAITING`
- `INTERVENTION_REQUIRED`
- `ITERATION_LIMIT`

`run()`はbounded iterationを必須とし、無限whileをCore APIに埋め込まない。常駐processは上位launcherがbounded runを繰り返す。

## 14. 完了条件

- bootstrap済み/未bootstrap両方から開始できる。
- Workを人間が逐次選択しない。
- wait-only Workが別のdependency-ready Workを塞がない。
- restart後にDB/live readbackから継続できる。
- pending/UNCERTAIN effectを盲目的再送しない。
- same ScheduleKeyの無限dispatchを防ぐ。
- Work完了後に次Workへ進む。
- fresh evidenceからGoal完了を確定する。
- tests / exact-head CIを通過する。
