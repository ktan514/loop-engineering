# AI Liver Yura → Generic Loop Engineering Migration Reconciliation

Owner: Issue #5
Status: Initial canonical migration draft

## 1. Purpose

`ai-liver-yura` repository内で先行して設計・実装されたLoop Engineering成果を、汎用Platform `ktan514/loop-engineering` へ安全に移管する。

目的はコピーではなく、責務ownershipを一意にし、Yura固有policyを分離し、competing implementation lineageを発生させないことである。

## 2. Fresh live snapshot used for this draft

確認時点の主要live state:

### Preflight

- Yura Issue #463: closed/completed
- merged PR #464
- source branch head: `62077febefc015672c5026669a0a4dd0fd8a2ac8`
- merge commit: `b7839019acb16414ea57527b1a8e31ea115c93fb`

### Mission Supervisor / Scheduler

- Yura Issue #465: open
- Draft PR #466: open / unmerged
- branch: `feature/v2-mission-supervisor-work-scheduler`
- base: `rebuild/v2-foundation@b7839019acb16414ea57527b1a8e31ea115c93fb`
- current exact HEAD: `662ca8cce73d3722c44c444aeabcc1a0d84b80d8`
- changed files include `tools/loop_engine/**`, tests, Yura V2 docs, and `.github/workflows/v2-ci.yml`

At this snapshot, open PR search for Loop Engineering responsibility returned PR #466 as the active implementation lineage.

## 3. Important live inconsistency discovered

Yura #472 body contains a Phase 0 target for PR #466 exact HEAD:

```text
1510cd400e6dcd26726c23d8d236d6ceb1ba566a
```

but current live PR #466 HEAD is:

```text
662ca8cce73d3722c44c444aeabcc1a0d84b80d8
```

Therefore #472 body target is stale and must not be used as current review target. This is concrete evidence for the generic rule:

> Issue/checkpoint text may discover a target candidate, but exact execution/review identity must be fresh-resolved from live source-control state.

No provider call/review may be issued against the stale SHA merely because it appears in Issue text.

## 4. Classification meanings

### ADOPT

Concept/contract is provider/product independent enough to become Platform canonical with minimal semantic change.

### MIGRATE

Valuable design/code/tests exist, but Product/provider-specific assumptions must be removed before Platform ownership.

### SUPERSEDE

New generic Platform design replaces the old ownership/model. Old artifact remains historical evidence but is not a future implementation authority.

### KEEP_PRODUCT_SPECIFIC

The responsibility belongs to Yura and should remain in Yura Profile/policy/canonical docs.

## 5. Responsibility classification

| Yura source | Classification | Generic ownership | Notes |
|---|---|---|---|
| #462 Loop Engineering Parent | SUPERSEDE | loop-engineering #1 + future Platform roadmap | Yura Mission #450/#317 and Project #7 assumptions are Product policy, not generic Platform parent |
| #463 / PR #464 Preflight | MIGRATE | Platform preflight + capability adapters | capability taxonomy/security knowledge is reusable; fixed Yura repo, Project #7, gh/project scope assumptions become Profile/adapter data |
| #465 canonical supervisor design | MIGRATE | `control_loop.md`, `authority_and_state.md` | Observe/Reconcile/Resume/Select/TaskPacket/Write Gate are generic; Mission/Issue/Project identities must be abstracted |
| PR #466 `tools/loop_engine/models.py` | MIGRATE | future generic domain models | strong reusable typed contracts, but currently uses `issue_number:int`, `project_number:int`, `MissionSnapshot`, GitHub/Yura-specific assumptions |
| PR #466 `supervisor.py` | MIGRATE / PARTIAL SUPERSEDE | future generic Supervisor application service | deterministic composition is valuable; hard-coded `#207/#317/#450/#462`, `Project #7`, Yura test/risk strings are prohibited in Core |
| PR #466 reconciliation/scheduler/write_gate | MIGRATE | generic Core/application | expected to be high-value seed after exact diff-level audit; no direct cherry-pick before generic contract tests |
| PR #466 self-improvement / GitHub issue publisher | MIGRATE | generic health lane + PlanningWriter adapter | improvement generation generic; publishing to Yura Project #7 is adapter/profile-specific |
| #467 Autonomous Runner design | MIGRATE / SUPERSEDE DESIGN OWNER | future generic runner in loop-engineering | state-machine knowledge is retained; implementation ownership moves to new repo and follows #3/#4/#6 canonical docs |
| #468 Design Completion Gate | SUPERSEDE | loop-engineering #1 | the new repository's Architecture Completion Gate is the canonical generic design gate |
| #469 move Preflight from `app/operations` to `tools/loop_engine` | SUPERSEDE PHYSICAL TARGET | new repository package | moving to another directory inside Yura is no longer final architecture; destination should be separate Platform repo |
| #470 PostgreSQL Operational Store | MIGRATE | optional RuntimeStore adapter | DB remains operational memory, not current-state Authority; filesystem adapter should permit bootstrap without PostgreSQL |
| #471 Yura E2E Pilot | MIGRATE + KEEP YURA PILOT | generic #6 scenarios + Yura adoption verification | generic recovery scenarios move here; actual Yura V2 pilot remains Product-specific verification |
| #472 Trusted Reviewer Broker | MIGRATE | Host reviewer service + ReviewerPort adapter | credential isolation/exact-target/idempotency are generic; `YURA_TRUSTED_REVIEWER_SOCKET` must become generic service/profile naming |
| #207 project operation authority | KEEP_PRODUCT_SPECIFIC + EXTRACT PRINCIPLES | Yura Profile/policy adapter | Yura Issue hierarchy/status/project fields remain Product policy; generic Resume/lineage rules already extracted |
| #450 Mission | KEEP_PRODUCT_SPECIFIC | maps to `RunGoal(kind=MISSION)` | Platform must not require a Mission GitHub Issue |
| #317 V2 Root | KEEP_PRODUCT_SPECIFIC | Yura RunGoal completion input | product completion semantics stay in Yura |

## 6. PR #466 detailed migration notes

### 6.1 `models.py`

Reusable:

- typed conflicts
- Run disposition
- lineage classification
- SourceIdentity
- Observation/Resume/TaskPacket concepts
- WriteIntent/WriteGateResult

Required generic changes:

- `MissionSnapshot` → generic `RunGoalSnapshot`
- `issue_number:int` → opaque `WorkId`
- `project_number:int` → PlanningProvider/Profile identity outside Core state
- `repository:str` → stable `RepositoryIdentity`
- base/head SHA-only assumptions → generic revision identity with Git specialization in adapter
- `MISSION_COMPLETE` → generic `COMPLETE` disposition
- Yura-specific conflict names may become provider-neutral equivalents

### 6.2 `supervisor.py`

Reusable:

- deterministic decision composition
- global vs Work-scoped reconciliation separation
- wait/yield behavior
- duplicate suppression
- Resume Certificate + Task Packet generation

Must not migrate unchanged:

```text
("#207", "#317", "#450", "#462", ...)
("Project #7 only", ...)
("development tooling", "deterministic supervisor")
```

These values belong to Yura Profile/RunGoal mapping.

Also current code defaults next transition to `IMPLEMENT`; generic state machine must derive transition from observed Work state and may choose DESIGN / REPAIR / VERIFY / REVIEW / INTEGRATE / RECONCILE.

### 6.3 tests

Existing tests are valuable behavioral evidence, not automatically generic tests.

Migration process:

1. extract invariant being tested
2. rewrite fixture using generic IDs/provider-neutral snapshots
3. preserve failure/regression scenario
4. add Yura adapter-specific test separately if required

Do not copy test names/fixtures that encode Project #7/#6 assumptions into Core tests.

## 7. Ownership after migration

### `loop-engineering` owns

- generic Control Loop
- generic Authority/State model
- generic Resume/Write/Integration gates
- generic health/self-improvement engine
- provider Ports
- GitHub/Codex/Reviewer adapters
- Host Runtime Store
- generic Preflight
- generic Runner
- generic security/recovery tests

### `ai-liver-yura` owns

- Yura Product code/runtime
- Yura V2 canonical architecture
- Yura-specific RunGoal definition (#317/#450 equivalent mapping)
- Yura Issue hierarchy/Project mappings
- Yura required CI checks
- Yura Human Verification policy
- `.loop-engineering.yml` or equivalent trusted profile
- Yura pilot evidence that generic Platform can complete real Product Work

## 8. Migration sequence

```text
A. Finish generic architecture (#1-#6)
B. Freeze competing implementation in new repo until Architecture Completion PASS
C. Re-read PR #466 exact current HEAD
D. Diff-level classify reusable domain/app code
E. Create generic implementation Work Issues in loop-engineering
F. Port behavior, not Yura identities
G. Add generic tests
H. Add GitHub/Yura Profile adapters
I. Verify exact generic E2E
J. Adopt Yura as first Product Profile
K. Run Yura pilot
L. Only then close/supersede old Yura Loop Engineering ownership
```

## 9. No-cherry-pick rule for initial migration

PR #466 should not be wholesale cherry-picked into the new repository.

Reasons:

- hard-coded Yura authority references
- GitHub Issue/Project-centric domain types
- Yura CI workflow changes mixed into implementation lineage
- self-improvement publishing coupled to Yura planning
- generic security/Port contracts are now stricter

Selective code migration may preserve implementation logic after exact file-level audit, but must land under new generic design/tests.

## 10. Old-lineage disposition rule

Until the new Platform implementation passes generic E2E and Yura pilot:

- do not delete PR #466/history
- do not create a second Yura-side implementation lineage for the same responsibility
- mark/communicate new repository as future canonical owner
- preserve old exact heads as migration evidence

After successful adoption, Yura issues #462/#465/#467-#472 should be reconciled individually as migrated/superseded/completed rather than silently abandoned.

## 11. Migration Gate

Before the first generic implementation commit that ports Yura code:

- [ ] #1 Architecture Completion PASS
- [ ] current Yura PR #466 exact HEAD fresh-read
- [ ] active Yura implementation lineage count = 1 or fully reconciled
- [ ] source file/classification matrix updated for current HEAD
- [ ] generic destination Issue exists
- [ ] generic canonical design paths identified
- [ ] Yura-specific identifiers removed from Core target
- [ ] tests rewritten around generic contracts
- [ ] old/new ownership recorded in both repositories

Any competing lineage/canonical mismatch causes STOP and reconciliation.
