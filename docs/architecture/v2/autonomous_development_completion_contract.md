# V2自律開発 完成契約

管理Issue: #82
親Issue: #81

状態: 製造仕様

## 1. 結論

Loop Engineering V2は、安全な外部effect実行基盤だけでは完成としない。

完成品は、HostからProductの初期条件と開発Goalだけを受け取り、Product側に事前のWork Issue、branch、PR、TaskPacketが存在しない状態から、次を自律的に行える開発制御系である。

```text
BOOTSTRAP
→ PLAN
→ OBSERVE
→ RECONCILE
→ RESUME
→ SELECT
→ DESIGN
→ IMPLEMENT
→ VERIFY
→ REVIEW
→ HUMAN_VERIFY?
→ REPAIR?
→ INTEGRATE
→ COMPLETE_WORK
→ SELECT_NEXT | COMPLETE_GOAL
```

各外部変更は、#62〜#67で確立したV2安全契約を必ず経由する。

```text
DBへ意図を確定
→ fresh target readback
→ Write Gate
→ effectを最大1回実行
→ target限定readback
→ DBへ結果を確定
→ Checkpoint
```

この2つ、すなわち「何を次にするかを決定する自律制御」と「決定された変更を安全に1回だけ実行する制御」の両方が接続されて初めてLoop Engineering V2と呼ぶ。

## 2. 完成判定を誤った原因と是正

既存の `docs/architecture/control_loop.md` と `docs/architecture/implementation_plan.md` は、当初からWork選択、Implementer、CI、Reviewer、Runner、E2Eまでを完成品の責務としていた。

V2再構築では、旧actual-hostの事故原因を除去するため、PostgreSQL作業状態、TaskPacket、lease、effect intent、readback、outbox、旧Host拒否を先に厳密化した。この安全基盤自体は必要かつ有効である。

誤りは、安全基盤の完成を製品全体の完成と扱い、上位のPlanning / Supervisor / Implementer / Verification / RunnerをV2へ再接続する製造工程をCompletion Gateから落としたことである。

今後は次を禁止する。

- 1つの層やadapter群が完成したことを理由にPlatform全体を完成扱いする。
- mock / unit / component testだけで自律開発Platformの完成を判定する。
- Product側の初期IssueやTaskPacketを人間が代行作成した状態だけでbootstrap能力をPASSとする。
- TaskPacketを人間が1件ずつ発行する運用を自律Runnerの代替とする。

製造Completion Gateは第14節だけを正とする。

## 3. 初期入力契約

HostがProductごとに与える初期入力を `ProductDevelopmentRegistration` と呼ぶ。

最低限、次を型付きで持つ。

```text
ProductDevelopmentRegistration
- product_key
- workspace_canonical_path
- repository_identity
- project_owner
- project_number
- trunk_branch
- goal_definition_identity
- goal_revision
- goal_text
- acceptance_criteria
- branch_policy
- ci_policy
- review_policy
- human_verification_policy
- self_improvement_target
```

`goal_text` と `acceptance_criteria` はPlanning LLMへ渡す要求の入力であるが、runtime current-state Authorityにはしない。

登録後にPlanningで生成したWork構造はGitHub Issue / Projectへ型付き投影し、実行中のcurrent Work、TaskPacket、Checkpoint、lease、effectはPostgreSQLを正本とする。

秘密情報はRegistrationへ直接保存しない。credentialはHost側環境からadapterへ注入する。

## 4. Authority

| 情報 | Authority |
| --- | --- |
| Product登録、対象Repository / Project / Workspace | Host Product Registration |
| Product Goalと受入条件の原文 | Goal Definition |
| Workの目的、受入条件、依存関係、優先度、完了判断 | GitHub Issue / Project |
| 設計判断 | Product Repository canonical design |
| current selected Work、実行段階、TaskPacket、Checkpoint | PostgreSQL |
| lease、idempotency、未確定effect | PostgreSQL |
| branch / PR / HEAD / merge結果 | GitHub live |
| CI結果 | exact HEADへ結び付いたCI provider live state |
| review結果 | exact HEAD / canonical revisionへ結び付いたReviewer evidence |
| Human Verification | 対象exact HEADへ結び付いた明示的なverification evidence |

Issue commentは人間向け報告であり、current Work / PR / HEAD / next transitionを復元する機械入力にしない。

## 5. Planning

PlanningはGoalから実装コマンドを直接生成しない。まず型付き `WorkPlanProposal` を生成する。

```text
WorkPlanProposal
- proposal_identity
- goal_revision
- parent_work?
- works[]
  - logical_key
  - title
  - purpose
  - acceptance_criteria
  - dependencies[]
  - work_kind
  - human_verification_required
  - canonical_design_targets[]
- completion_conditions
```

Planning出力は次の順に検証する。

1. schema / size / closed enum
2. Goalとの対応
3. dependency cycleなし
4. logical key重複なし
5. Product外scopeなし
6. 完了条件欠落なし
7. 既存GitHub Workとの競合なし

検証後にだけ、Issue作成・Project登録を型付きeffectへ変換する。

同じGoal revisionと同じlogical keyのWorkは再bootstrapで同一Workへ収束させる。create失敗が不明な場合は再作成せず、logical identityを使ってreadbackする。

## 6. Work選択と状態機械

Work単位の論理状態を次へ統一する。

```text
PLANNED
DESIGN_REQUIRED
DESIGN_ACTIVE
IMPLEMENTATION_REQUIRED
IMPLEMENTATION_ACTIVE
VERIFICATION_REQUIRED
CI_PENDING
REVIEW_REQUIRED
REVIEW_PENDING
HUMAN_VERIFICATION_REQUIRED
HUMAN_VERIFICATION_PENDING
REPAIR_REQUIRED
INTEGRATION_READY
INTEGRATING
COMPLETED
BLOCKED
```

PostgreSQLの実行lifecycleとGitHub Project Statusは同じ値を二重正本化しない。上記はSupervisorがtyped observationから導出する制御状態であり、DBにはcurrent transitionと安全Checkpointを保持する。

Schedulerは次の優先順位で選択する。

1. DBで安全にresume可能なcurrent Work
2. dependency-readyかつactionableな既存Work
3. 外部待ちではない次のWork

CI / review / Human Verification待ちのWorkだけを理由にMission全体をHuman Interventionへ変更しない。独立Workがあればそちらへ進む。

## 7. DESIGN / IMPLEMENT / REPAIR

Codex Implementerへのdispatchは、すべて型付きTaskPacketを介する。

最低限のTaskPacket:

```text
DevelopmentTaskPacket
- packet_identity
- work_identity
- generation
- transition: DESIGN | IMPLEMENT | REPAIR | RECONCILE
- authority_identities
- goal_revision
- issue_revision
- canonical_design_identities
- exact_base_sha
- expected_branch_identity
- scope
- non_goals
- acceptance_checks
- safety_constraints
```

### DESIGN

設計が必要なWorkは必ずDESIGNを先行させる。DESIGNの成果はProduct Repositoryのcanonical designへ保存し、fresh blob SHAをevidenceとする。

### IMPLEMENT

必要なcanonical design identityが揃っていない場合はIMPLEMENTを発行しない。

### REPAIR

CI failure、REQUEST_CHANGES、再現可能なblocking findingは同一Work / 同一active lineageへREPAIRを発行する。別branchや別PRを作って逃げない。

Codexの終了コードは「作業プロセスが終了した」証拠であり、Git変更成功・push成功・PR成功のAuthorityではない。必ずGit / GitHub liveをfresh readbackする。

## 8. 開発lineageと外部effect

V2 effectは既存の `PUSH` / `READY` / `MERGE` / `ISSUE_UPDATE` に加え、自律開発に必要なcreate系effectを扱う。

最低限:

- `ISSUE_CREATE`
- `PROJECT_ITEM_ADD`
- `PROJECT_FIELD_UPDATE`
- `BRANCH_CREATE`
- `PUSH`
- `PR_CREATE`
- `READY`
- `REVIEW_REQUEST`
- `ISSUE_UPDATE`
- `MERGE`

各effectは次を必須とする。

- idempotency key
- typed target identityまたは作成後に確定可能なlogical identity
- expected preconditions
- expected effect
- readback方法

create系effectでもcommand failure後に同じcreateを即再送しない。logical key、branch名、PR head/base、Issue marker等から対象限定readbackし、存在を証明できればCONFIRMED、存在しないことを証明できればNO_EFFECT、証明不能ならUNCERTAINとする。

1 Work = 1 active implementation lineageを不変条件とする。

## 9. CI / Review / Human Verification

### CI

CIはexact HEADへ結び付ける。old-head SUCCESSはcurrent HEADのPASSではない。

CI failureは通常REPAIRへ戻す。CI pendingは外部待ちであり、Human Interventionではない。

### Review

ReviewerはImplementerと独立した境界を維持する。同一exact HEADへのreview requestは原則1回とし、HEAD変更時だけ新しいrequest identityを作る。

REQUEST_CHANGESはREPAIRへ戻す。古いHEADのApproveをcurrent HEADへ流用しない。

### Human Verification

GUI、音声、映像、操作感、実機等、自動検証で完了判定できないWorkだけを対象とする。

Human Verification evidenceもexact HEADへ結び付ける。HEAD変更後は必要に応じて再確認する。

## 10. Integration / Work Completion

INTEGRATION_READYにできるのは、Work policyが要求する次のevidenceがすべてcurrent exact HEADへ成立した場合だけとする。

- automated verification
- exact-head CI
- independent review
- Human Verification（必要な場合）
- unresolved blocking conflictなし

mergeはexpected PR head SHAを固定し、通常はmerge commit方式を使用する。

merge readback後に、Issue / ProjectのWork完了を更新する。merge command成功だけでWorkを完了しない。

## 11. Goal Completion

Goal完了は「actionable Workがない」だけでは成立しない。

次をfresh readしてすべて確認する。

- Goal配下のplanned Workがすべてterminal completion
- open dependency未完了なし
- active implementation lineageなし
- pending / uncertain effectなし
- required Human Verification pendingなし
- Goal acceptance criteriaに対応するcompletion evidenceあり

これらを満たした場合だけ `COMPLETE_GOAL` とする。

## 12. 停止・再起動

process停止後の最初の入力はPostgreSQLの安全Checkpointである。

再開順序:

```text
DB recovery
→ Product Registration照合
→ Issue / Project typed definition sync
→ current Work / packet pointer検証
→ pending effectがあればtarget限定readback
→ branch / PR / HEAD等の必要最小限readback
→ Supervisor再判定
→ lease取得
→ 次の最大1遷移
```

`INTENT_RECORDED` / `UNCERTAIN` effectを再送しない。

## 13. 既存実装の移行分類

| 現行module | 判定 | V2での扱い |
| --- | --- | --- |
| `scheduler.py` | MIGRATE | dependency-ready選択、priority、ScheduleKeyの考え方をtyped V2 stateへ移す |
| `supervisor.py` | MIGRATE | reconcile / select / TaskPacket生成の純粋判断をV2 Authorityへ合わせて移す |
| `runner.py` | SUPERSEDE + 部分MIGRATE | Protocol分離とCodex境界は再利用し、旧Checkpoint中心のcompositionはV2 Runnerへ置換 |
| `trusted_worktree.py` | MIGRATE | Workspace安全確認と可逆cleanupをV2 Implementer境界へ移す |
| `v2_execution_state.py` | ADOPT / EXTEND | V2 DB安全状態の基礎として維持・拡張 |
| `v2_resume.py` | ADOPT / EXTEND | pending effect優先の復元を自律transitionへ拡張 |
| `v2_effect_executor.py` | ADOPT / EXTEND | create系を含む開発effectへ拡張 |
| `v2_work_definition.py` | ADOPT / EXTEND | 既存Work同期に加えbootstrap後のWork graph読取りへ拡張 |
| `v2_host_entrypoint.py` | ADOPT / EXTEND | 1 packet実行の安全核として維持し、上位Runnerから利用する |
| 旧actual-hostの自然文Checkpoint解析 | REJECT | 再導入しない |

#82実装開始前に上表をfile-level監査し、責務差分があれば本書を更新する。

## 14. 製造Completion Gate

製造完了は、unit / integration testに加え、controlled real Repository / Projectで次を全て証明した場合だけ成立する。

初期状態:

- 雛形Repository
- 作成済みProject
- Product Registration
- Goal Definition
- Product Work Issueなし
- active branch / PRなし
- V2 TaskPacketなし

そこからLoop Engineering自身が次を完遂する。

1. Goal bootstrap
2. Work graph作成
3. Issue / Project登録
4. dependency-ready Work選択
5. DESIGN
6. IMPLEMENT
7. automated verify
8. exact-head CI
9. independent review
10. 意図的なfailureまたはREQUEST_CHANGESからREPAIR
11. 再verify / re-review
12. 必要なHuman Verification
13. exact-head merge
14. Work completion
15. next Work selection
16. 全Work完了
17. Goal completion

さらに次を注入する。

- create effect直前 / 直後crash
- push直前 / 直後crash
- review request直前 / 直後crash
- merge直前 / 直後crash
- DB restart
- stale CI / stale review
- competing lineage
- historical/HOLD PR
- `UNCERTAIN` effect

全シナリオで重複effect、trunk直接push、historical PR誤昇格、Issue comment自然文からのcurrent-state復元が0件であることを確認する。

このGateがPASSし、#81の全Completion Gateが成立するまで製造完了としない。

## 15. 受入検証との境界

#74〜#80は本製造Completion Gate通過後の受入・実動作検証である。

製造Gateは「Loop Engineeringとして走行可能な完成品か」を証明する。

受入検証は、その完成品を小3・中2・大1の異なるProductへ適用し、実運用上の品質・汎用性・故障傾向をPDCAで評価する。

両者を再び同一Gateとして扱わない。