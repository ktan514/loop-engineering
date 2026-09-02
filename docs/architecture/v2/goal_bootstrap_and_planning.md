# V2 Goal bootstrap / Product Planning仕様

管理Issue: #83
親Issue: #81
上位正本: `autonomous_development_completion_contract.md`

状態: 製造仕様

## 1. 目的

Product側にWork Issue、branch、PR、TaskPacketが存在しない状態から、Hostが与えたProduct登録とGoalを型付きWork Planへ変換し、GitHub Issue / Projectへ冪等に投影できるbootstrap境界を定義する。

本仕様は実行中current Workを決めない。PlanningのAuthorityをGitHubへ確立した後、#84のV2 Supervisor / SchedulerがPostgreSQL実行状態とGitHub typed planning stateを組み合わせてWorkを選択する。

## 2. 初期入力

`ProductDevelopmentRegistration`:

- `product_key`
- `workspace_canonical_path`
- `repository_identity`
- `project_owner`
- `project_number`
- `trunk_branch`
- `goal_definition_identity`
- `goal_revision`
- `goal_text`
- `acceptance_criteria[]`
- `work_branch_template`
- `ci_workflow_name`
- `initial_project_status`
- `human_verification_policy`
- `self_improvement_target?`

Product側の既存Mission Issue番号は必須入力にしない。旧Host互換用`mission_issue`は本bootstrap契約のAuthorityではない。

## 3. Planner境界

`GoalPlannerPort.plan(registration) -> WorkPlanProposal` とする。

PlannerはGitHub mutationやProduct Workspace変更を行わない。出力は次の型付きproposalのみとする。

`WorkPlanProposal`:

- `proposal_identity`
- `goal_revision`
- `works[]`
- `completion_conditions[]`

各`PlannedWork`:

- `logical_key`
- `title`
- `purpose`
- `acceptance_criteria[]`
- `dependencies[]`（logical key）
- `work_kind`
- `human_verification_required`
- `canonical_design_targets[]`

最初のproduction実装では、外部Planning LLMが未接続でもbootstrap能力そのものを失わないよう、Goal全体を1 Workへ正規化する`SingleWorkGoalPlanner`をgeneric fallbackとして持つ。複雑なGoal分解は同Portの別Adapterへ差し替え可能とする。

## 4. Proposal validation

mutation前に次をすべて検証する。

1. Product / Goal identityが空でない。
2. acceptance criteriaが1件以上存在する。
3. Work件数が1〜64の範囲内。
4. logical keyが安全な小文字ASCII識別子で一意。
5. title / purpose / acceptanceが空でない。
6. dependencyが同proposal内の既知logical keyだけを参照する。
7. self dependencyがない。
8. dependency graphにcycleがない。
9. completion conditionが1件以上ある。
10. 同一Goal revisionから決定論的なproposal identityを再生成できる。

不正proposalは外部effectへ変換しない。

## 5. Bootstrap effect状態

最初のIssue作成前にはGitHub Issue番号由来の`WorkRecord`が存在しないため、通常の`loop_effect_attempts.work_identity`へbootstrap mutationを記録できない。

この不足を理由にGitHubへ直接mutationしてはならない。versioned migrationで`loop_bootstrap_effects`を追加し、Goal bootstrap専用のintent / readback / idempotency正本をPostgreSQLへ置く。

最低限:

```text
loop_bootstrap_effects
- idempotency_key PRIMARY KEY
- product_key
- repository
- goal_revision
- kind
- target_identity
- status: INTENT_RECORDED | CONFIRMED | NO_EFFECT | UNCERTAIN
- expected_preconditions JSONB
- expected_effect JSONB
- request_identity?
- confirmed_at?
- recorded_at
```

bootstrap effectも通常V2 effectと同じ順序を守る。

```text
DB intent
→ target限定fresh readback
→ precondition検証
→ mutation最大1回
→ fresh readback
→ DB outcome
```

command failure / timeout後に同じeffectを即再送しない。結果をreadbackできなければ`UNCERTAIN`として停止する。

Goal / Work Issueが確定して通常`WorkRecord`を作成できる段階以降は、#62〜#67の通常V2 work/effect状態へ移行する。

## 6. GitHub Planning Projection

`PlanningProjectionPort.ensure_plan(registration, proposal)` はGitHub側の課題・Project構造を確立する。

Goal管理Issueへ次のmarkerを埋め込む。

```text
<!-- loop-engineering-goal:{product_key}:{goal_revision} -->
```

Product Work Issue本文へmachine bootstrap identityとして次のHTML comment markerを1つだけ埋め込む。

```text
<!-- loop-engineering-work:{product_key}:{goal_revision}:{logical_key} -->
```

markerはbootstrap時の冪等identityであり、current Work / PR / HEAD / next transitionのAuthorityには使用しない。

再bootstrap時はIssue一覧からmarker完全一致を検索し、既存Issueへ収束する。create commandが失敗・timeoutした場合も直ちに再作成せず、同markerをfresh readして存在確認する。

## 7. Issue作成

Goal管理IssueはGoal原文、Goal acceptance criteria、proposal identityを人間向けに保持する。

Work Issue本文は最低限次を持つ。

- marker
- Goal管理Issue参照
- 目的
- スコープ
- 受入条件
- logical dependency一覧
- canonical design target
- Human Verification要否

日付、Status、Priority等、Project fieldを正本とする値を本文へ重複管理しない。

## 8. Project登録

Goal管理IssueとWork Issueを指定Projectへ追加する。

Work Issueには少なくとも次を設定する。

- `Acceptance criteria digest`: acceptance criteriaのcanonical SHA-256
- `Status`: `initial_project_status`と一致するoption

Project field / option identityは名前からfresh解決し、IDをPlatform coreへハードコードしない。必要field・Status optionが無ければfail-closedする。

Project item add / field update後はProject itemをfresh readbackし、対象Issue URL、field valueが一致することを確認する。

## 9. Dependency

Projection結果はlogical dependencyを`ProjectedWork.dependencies`として型付きで保持する。

GitHub native dependency mutationはprovider capabilityとして扱う。利用可能な場合は設定してreadbackする。利用不能な場合でもdependencyを推測で失わず、後続#84へ型付きPlan snapshotとして渡せるようにする。

production Completion Gateまでには、SchedulerがdependencyをGitHub planning stateからfresh取得できるprovider経路を完成させる。

## 10. 冪等性

冪等keyはeffect種別と次の論理identityから決定論的に生成する。

```text
product_key + goal_revision + logical_key? + target role
```

以下を二重作成しない。

- Goal Issue
- Work Issue
- Project item
- Project field updateの論理effect

同markerのIssueが複数存在する場合は競合として停止し、どれかを推測採用しない。

## 11. Self-Improvement境界

Product bootstrapのPlanning projectionはProduct Issueだけを作成する。

Loop Engineering Platform自体の不具合・改善は`self_improvement_target`へ別系統で公開し、Product Repositoryへ汎用Platform改善Issueを混入させない。

## 12. 実装構成

- `v2_goal_planning.py`
  - registration / plan model
  - proposal validator
  - deterministic fallback planner
  - bootstrap service
- `v2_bootstrap_state.py`
  - bootstrap専用PostgreSQL effect state
- `v2_planning_projection.py`
  - GitHub Issue / Project projection adapter
  - marker / digest / effect intent / readback
- migration `0008_v2_bootstrap_effects.sql`
- tests
  - proposal validation
  - cycle / duplicate / invalid dependency
  - repeated bootstrap convergence
  - duplicate marker conflict
  - create command failure後readback
  - Project field不足のfail-closed
  - DB intent未確定ではmutation 0回

## 13. 完了条件

- Product側に事前Issueが0件でもGoalからWork Planを生成できる。
- 最初のIssue作成を含むbootstrap mutationがPostgreSQL intentを経由する。
- 同Goal revisionを再bootstrapしても同一Issue / Project itemへ収束する。
- Acceptance criteria digestをProjectへ投影できる。
- Project field不足・duplicate marker・曖昧readbackではmutationを継続しない。
- Product固有identityをPlatform coreへハードコードしない。
- #82のAuthority境界を維持する。
