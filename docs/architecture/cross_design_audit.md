# Cross-Design Architecture Audit

Owner: Issue #1
Status: Initial audit complete / blocking contradiction 0

## 1. Scope

Audit対象:

- `overview.md`
- `workspace_boundary.md`
- `project_profile.md`
- `control_loop.md`
- `authority_and_state.md`
- `self_improvement.md`
- `ports_and_adapters.md`
- `security_boundary.md`
- `review_pipeline.md`
- `integration_recovery.md`
- `runtime_layout.md`
- `github_workflow.md`
- `e2e_verification.md`
- `ai_liver_yura.md`

監査観点:

- ownership / physical boundary
- Authority / trust anchor
- identity / lineage
- exact-target evidence
- mutation / effect truth
- idempotency / restart
- concurrency / external wait
- reviewer independence
- credential / secret boundary
- Product/provider independence
- migration ownership
- self-improvement recursion
- completion semantics

## 2. Blocking findings discovered and resolved

### A-001 Project Profile self-trust circularity

Severity: Blocking
Status: RESOLVED

Problem:

Profileが自身のtrusted ref/pathを事実上自己決定できる読み方が残っていた。

Resolution:

- Host `ProjectRegistration`をbootstrap trust anchorとして追加
- Profile source repository/ref/pathをHost側が所有
- Profile自身はtrust anchorを自己変更不可

Canonical:

- `project_profile.md`

### A-002 Profile repository vs target Product repository ambiguity

Severity: Blocking
Status: RESOLVED

Problem:

Profileを保存するrepositoryと、Loopが変更するtarget Product repositoryが同一identity名で表現されていた。

Resolution:

- `profile_repository_identity`
- `target_repository_identity`

へ分離。

central profile repository構成も許容し、targetはstable provider identityへresolveする。

### A-003 Self-Improvement ownership gap

Severity: Blocking for Yura migration completeness
Status: RESOLVED

Problem:

Yura PR #466でSelf-Improvementが重要責務なのにGeneric canonicalに専用contractがなかった。

Resolution:

- `self_improvement.md`追加
- HealthEvent / ImprovementCandidate / storm guard / recursion guardをgeneric化
- publication先をPlanning adapter/Profileへ分離

### A-004 Canonical Reviewer pipeline under-specified

Severity: Blocking
Status: RESOLVED

Problem:

ReviewerPort/SecurityだけではReviewRequestKey、canonical generation、stale guard、result validation、broker restartを一意に追えなかった。

Resolution:

- `review_pipeline.md`追加
- exact target / ReviewRequestKey / PASS-REQUEST_CHANGES-ESCALATE-NOT_RUN / broker boundaryを定義

### A-005 Trusted CI control definition missing

Severity: Blocking / Security
Status: RESOLVED

Problem:

Target PRが変更したworkflowをsecret-bearing CIでそのままtrusted gateとして実行できる解釈余地があった。

Resolution:

- trusted CI control definitionとuntrusted target sourceを分離
- target workflow変更のself-application禁止
- secret-bearing contextでuntrusted code実行禁止

Canonical:

- `security_boundary.md`

## 3. Authority consistency audit

Status: PASS

整合確認:

- live provider state = current-state Authority
- canonical trusted revision = design Authority
- RuntimeStore/checkpoint = operational support
- chat summary/memory = non-authoritative discovery/context

`ai_liver_yura.md`でYura #472本文のstale PR #466 SHAを実例として確認し、live exact HEADを優先する設計と一致。

## 4. Identity / lineage audit

Status: PASS

- Project / Repository / Workspace / Run identityを分離
- RunGoal / WorkItemをGitHub Issue identityから分離
- one Work = one active implementation lineage原則
- CI/Review/Human evidenceをexact targetへbind
- profile/canonical generationもdigest/revision identityを保持

Provider固有SHAはGit adapter detailであり、Coreではrevision identityとして抽象化可能。

## 5. Mutation / effect truth audit

Status: PASS

全設計で次が一致:

```text
fresh Write Gate
→ effect intent
→ mutation
→ fresh readback
→ effect receipt
```

Provider response / AI自己申告 / local checkpoint単独をeffect truthにしない。

Crash after mutation before checkpointもexternal readbackでrecoverする。

## 6. Idempotency / restart audit

Status: PASS

Defined identities:

- ScheduleKey
- ReviewRequestKey
- CI/check request identity
- integration target identity
- improvement key
- ExecutionLease

Unknown effectをblind retryしない。

RuntimeStore unavailable時もrequired safety stateが欠けるmutationはfail-closed。

## 7. External wait / concurrency audit

Status: PASS

- dependency-readyとactionableを分離
- pending CI/review/Human verificationをbusy pollしない
- `YIELD_EXTERNAL`でRun終了可能
- independent Work/Productへ切替可能
- GLOBAL/SECURITY blockerのみ広域停止可能

## 8. Security audit

Status: PASS

Confirmed invariants:

- Reviewer credential not available to Implementer/Product Workspace
- source-control write credential not available to Reviewer
- untrusted Product code not imported/executed in secret-bearing Host process
- untrusted PR workflow not self-promoted to trusted CI control
- Issue/PR/diff/model output treated as data, not Host command
- Profile cannot weaken Host mandatory policy
- secrets absent from repository/checkpoint/normal logs
- destructive effects default deny

## 9. Provider independence audit

Status: PASS

Core abstractions do not require:

- GitHub Issue
- GitHub Project
- GitHub Actions
- Codex
- OpenAI
- PostgreSQL

Initial adapters may use them.

Implementation tests must preserve this by using fake/alternate adapters at contract level.

## 10. Product independence audit

Status: PASS

Removed from generic Core design:

- Yura #207/#317/#450/#462 identities
- Project #7/#6 assumptions
- `YURA_TRUSTED_REVIEWER_SOCKET`
- Yura-specific priority/status/area strings

These remain Profile/Policy/adapter mappings.

## 11. Migration audit

Status: PASS WITH IMPLEMENTATION FREEZE

Current source lineage:

- Yura PR #466 remains active Draft at audited snapshot
- new repository has only design PR #7, not competing implementation

Migration rules prevent wholesale cherry-pick and require exact-head re-audit before implementation port.

Therefore no competing implementation lineage has been introduced by this architecture work.

## 12. Self-improvement audit

Status: PASS

Self-Improvement:

- observes operational health
- creates bounded candidates
- publishes through Planning adapter
- enters normal Work Gate
- cannot directly rewrite Platform code
- has duplicate/storm/recursion guards

Mandatory Human Gate is not automatically classified as waste/manual defect.

## 13. Completion semantics audit

Status: PASS

`COMPLETE` requires RunGoal completion evidence.

Not equivalent to:

- no actionable Work
- all Work waiting
- one PR merged
- reviewer unavailable
- one Work completed

Those map to `YIELD_EXTERNAL`, selection of another Work, or intervention as appropriate.

## 14. Non-blocking implementation choices intentionally deferred

These are not architecture contradictions and should be decided by implementation Work:

- Python package/tooling exact layout
- concrete dataclass/Pydantic choice
- filesystem RuntimeStore serialization format
- OS-specific default `LOOP_ENGINEERING_HOME`
- PostgreSQL adapter timing/necessity
- initial Implementer mode: proposal-first vs remote-effects compatibility rollout
- exact GitHub API/GraphQL transport details
- Project planning field schema for `loop-engineering` Project
- process model for scheduler (single-run CLI first vs later service/event trigger)

Any choice must preserve canonical invariants.

## 15. Audit result

```text
Blocking contradictions: 0
Resolved blocking findings: 5
Implementation Freeze: remains active pending Human Architecture Confirmation
```

Repository architecture is internally consistent enough to proceed to Human confirmation and then implementation planning.
