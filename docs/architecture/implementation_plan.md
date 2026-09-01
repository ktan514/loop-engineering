# Loop Engineering V2 製造実装計画

歴史上のOwner: Issue #1
現行製造Authority: Issue #81
設計Work: Issue #82
Status: MANUFACTURING_RESUMED

## 1. 目的

Loop Engineering V2を、初期Product GoalからProduct開発を自律的に計画・実装・検証・統合し、Goal完了まで継続できる完成品へ仕上げるための製造順序を定義する。

旧版の本書は、Architecture Completion後のPhase I〜VIIとしてCore、Host Runtime、Read/Write adapter、Implementer、CI、Reviewer、Runner、Generic E2Eを計画していた。この責務構成自体は妥当だったが、V2再構築では安全実行基盤の完成後に上位制御を再接続する工程をCompletion Gateから落とした。

2026-09-02に製造完了判定を撤回し、#81で製造を再開した。本書を現行の製造順序の正本へ更新する。

詳細な完成契約は `docs/architecture/v2/autonomous_development_completion_contract.md` を正本とする。

## 2. 製造原則

- 初期GoalだけからProduct側のWork構造を作れることをbootstrapの必須条件とする。
- Product側のIssue / Projectは計画Authority、PostgreSQLは実行中状態Authority、GitHub liveは外部effect Authorityとして分離する。
- Issue comment自然文をcurrent Work / PR / HEAD / next transitionの機械入力へ戻さない。
- 設計→実装の順序をTaskPacketと状態機械で強制する。
- Codexの出力や終了コードを外部effect成功のAuthorityにしない。
- すべてのmutationはDB intent → fresh readback → Write Gate → effect → readback → DB outcomeを通す。
- 1 Work = 1 active implementation lineageを維持する。
- CI / review / Human Verificationはexact HEADへ結び付け、古いevidenceを流用しない。
- 修正可能なfailureは同一lineageのREPAIRへ戻す。
- 外部待ちだけをHuman Interventionへ昇格しない。
- controlled E2Eを通す前に製造完了としない。
- #74の段階的サンプル検証は製造完了後にのみ再開する。

## 3. 既存Phaseの現状監査

| 旧Phase | 当初責務 | 現在状態 | 製造対応 |
| --- | --- | --- | --- |
| Phase I | Core state / scheduler / reconciliation / Write Gate | PARTIAL / LEGACY + V2 | `scheduler.py` / `supervisor.py`の有用な純粋判断を#84でV2へ移行。Write GateはV2基盤を維持 |
| Phase II | Registration / Workspace / Runtime / Lease | V2で大幅再構築済み | PostgreSQL Work State / lease / CheckpointをADOPTし、Goal Registrationを#83で拡張 |
| Phase III | Source / Planning read + Preflight | PARTIAL | 既存Work同期はV2に存在。Goal bootstrap後のWork graph観測を#83/#84で完成 |
| Phase IV | Source / Planning write + readback | PARTIAL | V2は`PUSH` / `READY` / `MERGE` / `ISSUE_UPDATE`まで。create系・Project・branch・PR等を#83/#86で完成 |
| Phase V | Codex Implementer / CI / Reviewer | 実装断片は存在するがV2未接続 | #85 / #87でV2状態機械へ接続 |
| Phase VI | Generic Runner composition | V2未完成 | #88でV2専用Runnerを完成 |
| Phase VII | Generic E2E / Recovery | component受入のみ。Goal→完了E2Eなし | #89でcontrolled real E2Eを製造Completion Gateとして実施 |
| 旧Phase VIII | Yura Pilot | 現方針では先行しない | #74の小3・中2・大1サンプル受入検証を先に実施し、全PASS後に実製品を検討 |
| 旧Phase IX | Optional PostgreSQL | SUPERSEDED | V2ではPostgreSQL Work Stateが停止復元の必須基盤。optional扱いへ戻さない |

## 4. 現行製造Phase A — 完成契約

Work: #82

Responsibilities:

1. 完成品の定義
2. Product Registration / Goal Definition
3. Authority境界
4. bootstrapからGoal completionまでの状態機械
5. 既存上位制御のADOPT / MIGRATE / SUPERSEDE分類
6. Planning / Implementer / effect / CI / review / Human Verification / Runner境界
7. 製造Completion Gate

Exit:

- `autonomous_development_completion_contract.md`がmainへ正本化される。
- #83〜#89の責務と依存順序が矛盾しない。
- #61の事故経路を再導入しない設計レビューがPASSする。

## 5. Phase B — Goal Bootstrap / Product Planning

Work: #83
Depends on: #82

Responsibilities:

1. ProductDevelopmentRegistration
2. Goal Definition validation
3. WorkPlanProposal
4. Product管理Issue / Work Issue作成
5. Project item登録 / field設定
6. dependency作成
7. acceptance criteria digest
8. bootstrap effect idempotency
9. ProductとSelf-Improvement公開先の分離

Exit:

Product側に事前Work Issueがなくても、初期Goalから型付きWork graphを構築できる。

## 6. Phase C — V2 Supervisor / Scheduler

Work: #84
Depends on: #83

Responsibilities:

1. `scheduler.py`のdependency-ready選択規則をV2へMIGRATE
2. `supervisor.py`のreconcile / select / TaskPacket判断をV2へMIGRATE
3. DB current Work / Checkpoint / pending effect復元
4. GitHub typed Work definition同期
5. DESIGN / IMPLEMENT / REPAIR / VERIFY / REVIEW / HUMAN_VERIFY / INTEGRATE / COMPLETE遷移判定
6. duplicate dispatch suppression
7. next Work / Goal completion candidate

Exit:

自然文current-state解析なしで、任意のfresh stateから次の安全な遷移を決定できる。

## 7. Phase D — Codex Implementer

Work: #85
Depends on: #84

Responsibilities:

1. DESIGN TaskPacket
2. IMPLEMENT TaskPacket
3. REPAIR TaskPacket
4. design-before-code enforcement
5. canonical Workspace / repository / branch / HEAD確認
6. Codex credential isolation
7. fresh Git readback
8. failure cleanup

Exit:

CodexがV2 Supervisorのexact Workだけを安全に設計・実装・修正できる。

## 8. Phase E — GitHub Development Lineage Effects

Work: #86
Depends on: #84, #85

Responsibilities:

1. `ISSUE_CREATE`
2. `PROJECT_ITEM_ADD`
3. `PROJECT_FIELD_UPDATE`
4. `BRANCH_CREATE`
5. `PUSH`
6. `PR_CREATE`
7. `READY`
8. `REVIEW_REQUEST`
9. `ISSUE_UPDATE`
10. `MERGE`
11. create系effectのreadback / idempotency
12. 1 Work = 1 active lineage

Exit:

Product開発のGitHub lifecycleをV2 effect契約から迂回せず完遂できる。

## 9. Phase F — CI / Review / Human Verification / Repair

Work: #87
Depends on: #85, #86

Responsibilities:

1. exact-head CI
2. stale CI rejection
3. independent review request/readback
4. stale review rejection
5. duplicate review request suppression
6. Human Verification state/evidence
7. CI failure / REQUEST_CHANGES → REPAIR
8. new HEAD evidence invalidation
9. pending yield / independent Work continuation
10. merge-candidate evidence Gate

Exit:

検証・レビュー・人間確認がcurrent exact HEADへ正しく結び付き、修正ループを同一lineageで閉じられる。

## 10. Phase G — Autonomous Runner / Recovery

Work: #88
Depends on: #83〜#87

Connect:

```text
BOOTSTRAP
→ PREFLIGHT
→ OBSERVE
→ RECONCILE
→ RESUME
→ SELECT
→ DESIGN | IMPLEMENT | REPAIR
→ READBACK
→ VERIFY
→ REVIEW
→ HUMAN_VERIFY?
→ INTEGRATE
→ COMPLETE_WORK
→ SELECT_NEXT | COMPLETE_GOAL
```

Responsibilities:

- 1遷移ごとの安全transaction/effect契約
- external wait yield
- independent Work continuation
- next Work selection
- Goal completion
- process stop / crash recovery
- pending / UNCERTAIN effect readback priority
- no-progress guard
- Self-Improvement分離

Exit:

人間がTaskPacketを1件ずつ発行せず、Goal完了まで有用な遷移を継続できる。

## 11. Phase H — Manufacturing Completion Gate

Work: #89
Depends on: #88

初期状態:

- 雛形Repository
- 作成済みProject
- Product Registration
- Goal Definition
- Product Work Issue 0
- active implementation branch / PR 0
- TaskPacket 0

この状態からLoop Engineering自身が、Planning → Work graph → DESIGN → IMPLEMENT → CI → REVIEW → REPAIR → Human Verification（必要時）→ merge → next Work → Goal completionまで完遂する。

Required failure / recovery scenarios:

- REQUEST_CHANGES
- CI failure
- stale CI / review
- crash before/after create effect
- crash before/after push
- crash before/after review request
- crash before/after merge
- DB restart
- ambiguous / UNCERTAIN effect
- duplicate suppression
- competing lineage
- historical/HOLD PR
- Product issueとPlatform issueの分類

Exit:

#89 Completion Gateが全PASSし、発見した製造不具合が0件になるまで再実行済みであること。

## 12. 受入検証への移行Gate

#74〜#80は製造Phaseではない。

次をすべて満たした後にだけ#74を再開する。

- [ ] #82 PASS
- [ ] #83 PASS
- [ ] #84 PASS
- [ ] #85 PASS
- [ ] #86 PASS
- [ ] #87 PASS
- [ ] #88 PASS
- [ ] #89 PASS
- [ ] #81 Completion Gate全項目PASS
- [ ] repository gate PASS
- [ ] exact-head CI PASS

その後、小3・中2・大1のサンプルProductでPDCA受入検証を行う。

## 13. 禁止されるshortcut

- サンプルProduct側にCh4t9ptが初期Work Issueを作成してbootstrap不足を隠す。
- 人間がTaskPacketを逐次発行してRunner不足を隠す。
- old-head CI / reviewを流用する。
- pending effectを再送する。
- Issue commentからcurrent Work / PR / HEADを推測する。
- 旧actual-hostをV2 Runnerの代わりに呼ぶ。
- controlled E2E未実施のまま「製造完了」と報告する。
