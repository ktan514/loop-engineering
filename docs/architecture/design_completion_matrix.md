# Architecture Completion Matrix

Owner: Issue #1
Status: Initial architecture complete pending Human confirmation

## 1. Purpose

初期Platform実装へ進む前に、必要な設計責務がRepository canonicalとして揃っているかを一元確認する。

## 2. Matrix

| Area | Canonical document | Owner Issue | Status |
|---|---|---:|---|
| Platform overview | `docs/architecture/overview.md` | #1 | Draft complete |
| Workspace boundary | `docs/architecture/workspace_boundary.md` | #2 | Draft complete |
| Project Profile | `docs/architecture/project_profile.md` | #2 | Draft complete |
| Runtime layout | `docs/operations/runtime_layout.md` | #2 | Draft complete |
| Control loop | `docs/architecture/control_loop.md` | #3 | Draft complete |
| Authority/state | `docs/architecture/authority_and_state.md` | #3 | Draft complete |
| Self-improvement | `docs/architecture/self_improvement.md` | #3/#5 | Draft complete |
| Ports/adapters | `docs/architecture/ports_and_adapters.md` | #4 | Draft complete |
| Security boundary | `docs/architecture/security_boundary.md` | #4 | Draft complete |
| Canonical review | `docs/architecture/review_pipeline.md` | #4/#5 | Draft complete |
| Yura migration | `docs/migration/ai_liver_yura.md` | #5 | Draft complete |
| Integration/recovery | `docs/architecture/integration_recovery.md` | #6 | Draft complete |
| E2E verification | `docs/operations/e2e_verification.md` | #6 | Draft complete |
| GitHub workflow | `docs/operations/github_workflow.md` | #1 | Draft complete |
| Implementation order | `docs/architecture/implementation_plan.md` | #1 | Draft complete |
| Cross-design audit | `docs/architecture/cross_design_audit.md` | #1 | PASS / blocking contradiction 0 |

## 3. Required design questions

### Boundary

- [x] Platform/Product/Runtimeの物理境界
- [x] Product Workspace外のruntime state
- [x] multi-worktree identity
- [x] typed blocker scope
- [x] trusted Project Profile source
- [x] Profile bootstrap trust anchorをHost registrationに分離
- [x] Profile repositoryとtarget Product repositoryを分離

### Control Loop

- [x] RunGoalとWorkItemをprovider固有Issueから分離
- [x] Observe/Reconcile/Resume/Select/Execute/Readback
- [x] dependency-ready vs actionable
- [x] Run disposition
- [x] ScheduleKey/idempotency
- [x] Write Gate/effect receipt
- [x] Self-Improvementをnormal Work Gateへ接続

### Authority

- [x] external current-state Authority
- [x] design Authority
- [x] operational state
- [x] conversational contextの非Authority化
- [x] exact evidence binding
- [x] lineage conflict model

### Providers

- [x] SourceControl read/write ports
- [x] Planning read/write ports
- [x] CI / Implementer / Reviewer ports
- [x] RuntimeStore / Lease / Workspace ports
- [x] provider-neutral adapter error model
- [x] canonical Reviewer pipeline / request identity

### Security

- [x] Reviewer credential isolation
- [x] Implementer credential boundary
- [x] untrusted Product/Issue/PR data boundary
- [x] Host policy precedence
- [x] protected control file policy
- [x] destructive operation default deny
- [x] Profile self-trust変更禁止
- [x] trusted CI control definitionとuntrusted targetを分離

### Recovery

- [x] mutation crash windows
- [x] ambiguous effect recovery
- [x] CI/review stale rejection
- [x] merge readback
- [x] runtime store outage semantics
- [x] multi-product waiting behavior
- [x] graceful/forced restart

### Migration

- [x] Yura #463/#464 classification
- [x] Yura #465/PR #466 active lineage identification
- [x] #467-#472 ownership classification
- [x] Yura-specific RunGoal/Project policy separation
- [x] no-wholesale-cherry-pick rule
- [x] migration start gate
- [x] stale Issue-recorded review targetをlive HEADより下位Authorityとして確認

### Implementation planning

- [x] Core foundationをadapterより先に実装
- [x] Runtime/Leaseをremote Runnerより先に実装
- [x] GitHub read/Preflight → write effectsの順序
- [x] Implementer/CI/Reviewer boundaryをRunnerより先に確立
- [x] Generic E2EをYura pilotより先に実施
- [x] PostgreSQLをinitial bootstrap必須にしない

## 4. Remaining Architecture Completion actions

- [x] Cross-design auditを実施しblocking contradictionを0にする
- [x] Architecture PR #7を単一lineageとして作成
- [x] implementation start order確定
- [ ] PR #7のfinal exact HEADをIssue checkpointへ同期
- [ ] #2-#6へfinal design checkpointを同期
- [ ] Parent #1へCompletion evidenceを集約
- [ ] Human architecture confirmation

## 5. Implementation Freeze

上記remaining actionsが完了し、Issue #1でHuman confirmationを得るまでPlatform implementationを開始しない。

このFreezeは設計書の修正、GitHub状態監査、migration reconciliationを妨げない。
