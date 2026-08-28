# Post-Architecture Implementation Plan

Owner: Issue #1
Status: Planned order only / implementation frozen

## 1. Purpose

Architecture Completion Gate通過後の実装順を定義し、依存関係を逆転させず、Yura既存lineageとの重複実装を避ける。

本書はImplementation Freezeを解除しない。実装Issue作成・branch開始はHuman Architecture Confirmation後に行う。

## 2. Principles

- Core domainをprovider adapterより先に確定
- Runtime safety primitivesをremote effect runnerより先に実装
- Read adapterをWrite adapterより先に検証可能にする
- Implementer/Reviewer credential boundaryをRunnerより先に確立
- Generic fake/controlled E2EをYura pilotより先に通す
- Yura PR #466をfresh re-auditしてからbehaviorを移植

## 3. Phase I — Generic Core Foundation

Responsibilities:

1. identities / typed snapshots
2. RunGoal / WorkItem / Lineage
3. conflicts / evidence
4. Resume Certificate / TaskPacket
5. scheduler / reconciliation
6. Write Gate / Effect Intent / Receipt
7. disposition / ScheduleKey
8. health/self-improvement domain

Dependencies: Architecture Gate only

No GitHub/Codex/OpenAI imports in Core.

## 4. Phase II — Host Runtime Foundation

Responsibilities:

1. ProjectRegistration
2. Project Profile parser/validator
3. Workspace identity/inspection
4. Filesystem RuntimeStore
5. ExecutionLease
6. blocker lifecycle
7. sanitized event/checkpoint persistence

Dependencies: Phase I contracts

PostgreSQL is not required for first bootstrap.

## 5. Phase III — Source/Planning Observation Adapters + Preflight

Responsibilities:

1. GitHub SourceControlReader
2. GitHub PlanningReader
3. capability Preflight
4. trusted profile/source resolution
5. exact branch/PR/head/canonical observation
6. adapter failure normalization

Write capability can be introduced after read contracts pass.

Yura #463/#464 behavior is migration input, not wholesale code source.

## 6. Phase IV — Mutation Adapters

Responsibilities:

1. SourceControlWriter
2. PlanningWriter
3. fresh Write Gate integration
4. effect readback
5. protected mutation policy
6. conditional write where provider supports it

No Runner automation until these effects can be idempotently reconciled.

## 7. Phase V — Implementer / CI / Reviewer Boundaries

Parallelizable subtracks after required Core/Runtime contracts:

### Implementer

- Codex adapter
- proposal mode where practical
- remote-effects compatibility mode with readback
- credential/environment isolation

### CI

- trusted CI definition resolver
- exact-target dispatch/read
- stale rejection
- pending yield

### Reviewer

- ReviewerPort
- Trusted Host Broker
- ReviewRequestKey
- exact-target/canonical generation binding
- restart/idempotency

These tracks may be separate Work Issues/PRs.

## 8. Phase VI — Generic Runner/Application Composition

Connect:

```text
PREFLIGHT
→ OBSERVE
→ RECONCILE
→ RESUME
→ SELECT
→ EXECUTE
→ READBACK
→ VERIFY
→ REVIEW
→ INTEGRATE
→ CHECKPOINT
```

Requirements before start:

- Phase I-IV required contracts pass
- selected Phase V capability required by target transition exists
- no bypass path around Write/readback/credential boundaries

## 9. Phase VII — Generic E2E / Recovery

Use fake providers then controlled real repository.

Required:

- happy path
- REQUEST_CHANGES repair
- CI failure
- stale evidence
- crash windows
- ambiguous effect
- duplicate suppression
- reviewer restart
- RuntimeStore outage
- competing lineage
- multi-product wait/continue
- prompt/CI control injection tests

## 10. Phase VIII — Yura Migration / Product Profile / Pilot

Before code migration:

- fresh PR #466 exact HEAD read
- file-level source classification refresh
- old/new ownership checkpoint in both repositories

Then:

1. create trusted Yura Project Profile
2. implement Yura planning/canonical mapping policy
3. migrate reusable behavior/tests under generic contracts
4. run controlled Yura dry-run
5. run actual Yura V2 pilot
6. reconcile old Yura #462/#465/#467-#472 ownership

## 11. Optional Phase IX — PostgreSQL Operational Store

Add only if filesystem RuntimeStore limits become material or review/event analytics require it.

PostgreSQL remains an adapter/operational memory and does not become current-state Authority.

This phase may move earlier if concrete reliability requirements justify it, but Runner bootstrap must not be unnecessarily coupled to DB availability.

## 12. Planned implementation Issue decomposition

Human confirmation後に少なくとも以下をIssue化する想定:

1. Core state/control contracts
2. Host Runtime/Profile/Workspace/Lease
3. GitHub read + Preflight
4. GitHub write/planning effects
5. Codex Implementer adapter
6. CI adapter
7. Trusted Reviewer Broker/adapter
8. Runner composition
9. Generic E2E/recovery
10. Yura migration/Profile/pilot
11. optional PostgreSQL store

各IssueへStart/Target dateを設定してから着手する。

## 13. Migration safety

Yura PR #466をmergeしてからでなければ新Platformを実装できない、とはしない。

ただし同じ責務のYura側implementationを同時に伸ばしながら新Platformへ別実装を作ることは避ける。

Architecture Confirmation後のmigration checkpointで:

- Yura current lineageをpreserve/freeze
- new generic owner Workを明示
- exact source SHAを記録

してからgeneric implementationを開始する。

## 14. Gate

Implementation開始条件:

- [ ] PR #7 Architecture content Human approved
- [ ] Architecture docs mainへcanonicalize
- [ ] implementation Work Issues created with dates
- [ ] target Work Resume Gate PASS
- [ ] Yura migration source lineage fresh reconciled when migration-derived code is involved
