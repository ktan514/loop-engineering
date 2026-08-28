# Architecture Completion Matrix

Owner: Issue #1
Status: Initial architecture complete / Human confirmed / canonicalized

## 1. Purpose

初期Platform実装へ進む前に、必要な設計責務がRepository canonicalとして揃っているかを一元確認する。

## 2. Matrix

| Area | Canonical document | Owner Issue | Status |
|---|---|---:|---|
| Platform overview | `docs/architecture/overview.md` | #1 | Complete |
| Workspace boundary | `docs/architecture/workspace_boundary.md` | #2 | Complete |
| Project Profile | `docs/architecture/project_profile.md` | #2 | Complete |
| Runtime layout | `docs/operations/runtime_layout.md` | #2 | Complete |
| Control loop | `docs/architecture/control_loop.md` | #3 | Complete |
| Authority/state | `docs/architecture/authority_and_state.md` | #3 | Complete |
| Self-improvement | `docs/architecture/self_improvement.md` | #3/#5 | Complete |
| Ports/adapters | `docs/architecture/ports_and_adapters.md` | #4 | Complete |
| Security boundary | `docs/architecture/security_boundary.md` | #4 | Complete |
| Canonical review | `docs/architecture/review_pipeline.md` | #4/#5 | Complete |
| Yura migration | `docs/migration/ai_liver_yura.md` | #5 | Complete |
| Integration/recovery | `docs/architecture/integration_recovery.md` | #6 | Complete |
| E2E verification | `docs/operations/e2e_verification.md` | #6 | Complete |
| GitHub workflow | `docs/operations/github_workflow.md` | #1 | Complete |
| Implementation order | `docs/architecture/implementation_plan.md` | #1 | Complete |
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

## 4. Architecture Completion evidence

- [x] Cross-design audit: blocking contradiction 0
- [x] Architecture lineage: PR #7 / `design/initial-architecture`
- [x] Approved exact design HEAD: `2399e5a0c5fc2a8401a590f3ea2ee0939eee7665`
- [x] Human Architecture Confirmation: PASS on 2026-08-28
- [x] PR #7 merged by normal merge with expected-head guard
- [x] Canonical `main` readback: `8afea3d9c4f9000ab5524bd639baf9e54b366d83`
- [x] #2-#6 design responsibilities completed
- [x] Parent #1 completion evidence aggregated

## 5. Implementation Gate

Initial Architecture Freezeは解除済み。

ただし実装は`docs/architecture/implementation_plan.md`の順序と各実装Work Issueのfresh Start/Resume Gateに従う。

- Architecture Completionを理由に既存Yura側implementation lineageを無条件移植しない。
- 新しいimplementation Workは責務単位でIssue化し、開始予定日・終了予定日を設定する。
- 同一責務にactive implementation lineageが存在する場合は新規branch作成前にreconcileする。
- source-of-truth conflict、canonical mismatch、competing lineageがあればfail-closedする。
