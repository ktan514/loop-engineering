# V2 Autonomous Runner / Recovery仕様

管理Issue: #88
親Issue: #81
依存: #83, #84, #85, #86, #87, #98, #100, #101, #102
上位正本: `autonomous_development_completion_contract.md`, `local_llm_coder_integration.md`

## 1. 目的

任意ProductionのHost RegistrationとGoal Definitionから、Planning、設計、実装、Local Quality、External Review、修正、統合、次Work選択、Goal完了までを人間の逐次packet発行なしで自動継続する。

Loop Engineeringを唯一の上位Orchestratorとし、`local-llm-coder`はImplementer / Self Reviewer / Fixerのbounded Worker Backendとして扱う。

## 2. 完成フロー

```text
Product Registration + Goal
→ PREFLIGHT
→ BOOTSTRAP / PLAN
→ Work生成 / Project投影
→ SELECT dependency-ready Work
→ DESIGN
→ publish exact lineage
→ IMPLEMENT
→ publish exact lineage
→ VERIFY_LOCAL
→ fresh LOCAL_REVIEW
   ├─ finding → same-lineage LOCAL REPAIR
   │            → publish
   │            → VERIFY_LOCALから再取得
   └─ LOCAL_PASS
→ EXTERNAL_REVIEW Level 1 / pass 1..N
→ Level 2..N / pass 1..N
   ├─ finding → same-lineage REPAIR
   │            → publish
   │            → VERIFY_LOCAL
   │            → fresh LOCAL_REVIEW
   │            → LOCAL_PASS
   │            → External Level 1 / pass 1から全て再取得
   └─ EXTERNAL_PASS
→ HUMAN_VERIFY?（Product policyでrequiredの場合）
→ INTEGRATE
→ COMPLETE_WORK
→ next dependency-ready Work
→ 全Work完了 + Goal acceptance PASS
→ GOAL_COMPLETED
```

## 3. Production非依存境界

CoreへProduct固有repository、model名、test command、LOW/HIGH reviewer名を固定しない。

- Product identity / Workspace / GitHub Project / branch template: Host Registration
- Implementer Backend / model profile: Host設定
- Local Reviewer profile: local-llm-coder profile
- External Review Level 1..N / passes_required: Host設定
- Product verification command: trusted command descriptor
- canonical design target / acceptance: Planning result
-変更scope: trusted Work/Profile。generic fallbackはrepository root `.`

`.`は「shell wildcard」ではなくtyped repository-root scopeとして扱う。path traversalは引き続き拒否する。

## 4. Workspace-effect Backend

local-llm-coderはActive Production Workspaceを直接変更できる。

RunnerはWorker resultだけを信用せず、Host readback後に次を行う。

1. exact HEAD / branch / changed pathを確認
2. scope外変更を拒否
3. dirty changeならHostがstage / diff-check / commit
4. Workerがforward commit済みならancestor関係を確認
5. trusted `MaterializedProposal`へ正規化
6.既存GitHub lineage effectでbranch/PRをpublish
7. fresh remote PR/head readback

Codex proposal Backendも同じ`V2ImplementerPort`の別経路として維持する。

## 5. Quality target

Local / External ReviewはHEADだけでなく`change_identity`へbindする。

committed remote PRをreviewするときは、work branch + exact HEAD + clean workspaceから`local-llm-coder-change-v2` identityを再構成する。

修正・commit・HEAD移動が1回でも起きた場合、旧targetのLocal/External PASSはcurrent targetへ流用しない。

## 6. Durable stage

PostgreSQLが少なくとも次を保持する。

- autonomous runtime
- accepted Goal plan / projection
- current Work / selected transition
- dispatch journal / ScheduleKey
- Local Quality stage / evidence
- External Review level / pass / evidence
- task/checkpoint/effect intent
- pending/UNCERTAIN effect

Issue commentやLLM session終了をresume Authorityにしない。

## 7. WAITINGと再開

`WAITING`は再実行可能なidempotent stageを表す。

例:

- Local Worker INCOMPLETE
- Reviewer provider一時待機
- CI / Human Verification pending
- eventual consistency

WAITING dispatchを永続的なduplicate suppression対象にしてはならない。同じstageは各内部request identity/idempotency contractを使って安全に再開する。

一方、provider/effect送信後に結果未確定の`DISPATCHED`は盲目的再送せずreadback/reconcileを優先する。terminal `COMPLETED` ScheduleKeyも再dispatchしない。

## 8. REQUEST_CHANGES

External Reviewで承認済みblocking findingが出た場合:

```text
External Review
→ durable REQUEST_CHANGES
→ Supervisor REPAIR
→ Fixerへapproved_findingsだけ渡す
→ same lineageで変更
→ publish
→ new exact target
→ Local Qualityから全Gate取り直し
```

旧External level途中から再開しない。

## 9. Integration Gate

merge直前に同じcurrent targetについて最低限次をfresh確認する。

- Local PASS
- External PASS
- required Human PASS
- current PR exact HEAD
- competing lineageなし
- pending/UNCERTAIN effectなし

旧targetのPASSや自然文「review済み」をmerge根拠にしない。

## 10. Goal completion

Goal完了は次をすべて満たした場合だけ。

- planned Workが全て`COMPLETED`
- Product Issue / Project状態が完了条件と整合
- pending/UNCERTAIN effect 0
- current Workなし
- Goal acceptance evaluator PASS

完了後はruntimeを`COMPLETED`へ確定し、自律dispatchを停止する。

## 11. 起動

通常の自律production経路:

```bash
pipenv run python -m loop_engineering --v2-autonomous
```

bounded確認:

```bash
pipenv run python -m loop_engineering --v2-autonomous-once
```

Operational Store migrationは起動前にcurrentでなければならない。

## 12. Hard invariants

- OrchestratorはLoop Engineeringだけ
- main/trunkへ直接開発commitしない
- local Workerのprocess終了だけでWork完了にしない
- Reviewerは実装sessionと分離
- target変更後に旧quality evidenceを使わない
- approved finding以外をFixerへ渡さない
- WAITINGはsafe resume可能
- DISPATCHED/UNCERTAIN effectはblind retryしない
- provider/model/Product固有値をCoreへ固定しない
- secret値をTaskPacket/DB/logへ保存しない
- Goal完了をWork未完の状態で確定しない
