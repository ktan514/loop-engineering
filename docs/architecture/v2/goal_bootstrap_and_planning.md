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

## 5. GitHub Planning Projection

`PlanningProjectionPort.ensure_plan(registration, proposal)` はGitHub側の課題・Project構造を確立する。

Product Work Issue本文へmachine bootstrap identityとして次のHTML comment markerを1つだけ埋め込む。

```text
<!-- loop-engineering-work:{product_key}:{goal_revision}:{logical_key} -->
```

このmarkerはbootstrap時の冪等identityであり、current Work / PR / HEAD / next transitionのAuthorityには使用しない。

再bootstrap時はIssue一覧からmarker完全一致を検索し、既存Issueへ収束する。create commandが失敗・timeoutした場合も直ちに再作成せず、同markerをfresh readして存在確認する。

## 6. Issue作成

Issue本文は人間向け情報と受入条件を保持する。

最低限:

- marker
- 目的
- スコープ
- 受入条件
- logical dependency一覧
- canonical design target
- Human Verification要否

日付、Status、Priority等、Project fieldを正本とする値を本文へ重複管理しない。

## 7. Project登録

Issueを指定Projectへ追加し、少なくとも次を設定する。

- `Acceptance criteria digest`: acceptance criteriaのcanonical SHA-256
- `Status`: templateに存在する初期未着手optionを使用

Project field / option identityは名前からfresh解決し、IDをPlatform coreへハードコードしない。必要fieldが無ければfail-closedする。

Project item add / field update後はProject itemをfresh readbackし、対象Issue URL、field valueが一致することを確認する。

## 8. Dependency

Projection結果はlogical dependencyを`ProjectedWork.dependencies`として型付きで保持する。

GitHub native dependency mutationはprovider capabilityとして扱う。利用可能な場合は設定してreadbackする。利用不能な場合でもdependencyを推測で失わず、後続#84へ型付きPlan snapshotとして渡せるようにする。

production Completion Gateまでには、SchedulerがdependencyをGitHub planning stateからfresh取得できるprovider経路を完成させる。

## 9. 冪等性

冪等key:

```text
product_key + goal_revision + logical_key
```

以下を二重作成しない。

- Issue
- Project item
- Project field updateの論理effect

同markerのIssueが複数存在する場合は競合として停止し、どれかを推測採用しない。

## 10. Self-Improvement境界

Product bootstrapのPlanning projectionはProduct Issueだけを作成する。

Loop Engineering Platform自体の不具合・改善は`self_improvement_target`へ別系統で公開し、Product Repositoryへ汎用Platform改善Issueを混入させない。

## 11. 実装構成

- `v2_goal_planning.py`
  - registration / plan model
  - proposal validator
  - deterministic fallback planner
  - bootstrap service
- `v2_planning_projection.py`
  - GitHub Issue / Project projection adapter
  - marker / digest / readback
- tests
  - proposal validation
  - cycle / duplicate / invalid dependency
  - repeated bootstrap convergence
  - duplicate marker conflict
  - create command failure後readback
  - Project field不足のfail-closed

## 12. 完了条件

- Product側に事前Issueが0件でもGoalからWork Planを生成できる。
- 同Goal revisionを再bootstrapしても同一Issue / Project itemへ収束する。
- Acceptance criteria digestをProjectへ投影できる。
- Project field不足・duplicate marker・曖昧readbackではmutationを継続しない。
- Product固有identityをPlatform coreへハードコードしない。
- #82のAuthority境界を維持する。
