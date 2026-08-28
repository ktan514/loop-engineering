# Generic Core Foundation Implementation Design

Owner: Phase I Generic Core Foundation
Status: Implementation design baseline
Canonical parents:
- `docs/architecture/control_loop.md`
- `docs/architecture/authority_and_state.md`
- `docs/architecture/self_improvement.md`
- `docs/architecture/implementation_plan.md`

## 1. Purpose

Phase I Generic Core Foundationを、provider非依存・side-effect freeなDomain/Coreとして実装するための責務分割と依存順を定義する。

本PhaseではGitHub、GitHub Projects、GitHub Actions、Codex、OpenAI、filesystem runtime store等のadapterを実装しない。

## 2. Package boundary

初期実装ではPythonのsrc-layoutを採用する。

```text
pyproject.toml
src/
└─ loop_engineering/
   ├─ __init__.py
   └─ core/
      ├─ identities.py
      ├─ models.py
      ├─ conflicts.py
      ├─ evidence.py
      ├─ reconciliation.py
      ├─ scheduler.py
      ├─ resume.py
      ├─ task_packet.py
      ├─ effects.py
      ├─ health.py
      ├─ self_improvement.py
      └─ __init__.py
tests/
```

実装中に循環依存を避けるための小規模な再配置は許容するが、責務境界を変更する場合は本書を同じPRで更新する。

## 3. Work decomposition

### 0. Python Package / Toolchain Bootstrap

Owns:
- `pyproject.toml`
- src-layout package skeleton
- pytest bootstrap
- Ruff configuration
- strict Mypy configuration
- compileall / diff-check runbook-level commands
- minimal repository ignore rules needed by the Python toolchain

Rules:
- Core domain modelを先行実装しない
- GitHub Actions等のCI providerを導入しない
- provider SDK dependencyを追加しない
- secret/config credentialを追加しない
- #10以降のWorkが同一toolchain contractを再利用できる状態を作る

### A. Core State Contracts

Owns:
- SourceIdentity
- ExecutionTarget
- RunGoal
- WorkItem
- Lineage
- CanonicalGeneration
- Evidence
- Checkpoint DTO
- Conflict / scope / severity
- ObservationEpoch

Rules:
- provider固有ID型をCoreへ持ち込まない
- mutable provider clientを保持しない
- secret/raw credentialを保持しない
- identity equality / canonical serializationを決定論的にする

### B. Decision Core

Owns:
- reconciliation
- dependency-ready / actionable
- selection
- ResumeCertificate
- TaskPacket
- RunDisposition
- ScheduleKey / duplicate suppression decision

Rules:
- 入力snapshotだけから決定可能
- network/filesystem/subprocessなし
- same inputからsame decisionを返す
- wait-only Workは独立actionable Workを止めない
- conflict scopeを尊重する

### C. Effect Safety Core

Owns:
- WriteGate input/result
- EffectIntent
- EffectReceipt
- expected-before / observed-after identity validation
- stale / ambiguous / no-effect classification

Rules:
- effect自体は実行しない
- provider responseだけをeffect truthにしない
- readback evidenceからCONFIRMED等を判定する
- unsafe ambiguityはfail-closed

### D. Health / Self-Improvement Domain

Owns:
- HealthEvent
- health accumulation
- ImprovementCandidate
- ImprovementKey
- storm / duplicate / self-recursion guard
- ownership routing candidate

Rules:
- Planning Issueを直接作成しない
- Product current-state Authorityを持たない
- mandatory Human Gateを自動化対象として扱わない
- publicationは後続PlanningWriter adapterの責務

### E. Core Integration Verification

Owns:
- A-Dを組み合わせたfake snapshot scenarios
- exact identity / stale / competing lineage / wait-only / duplicate / effect ambiguity / health escalationの結合確認
- provider import boundary test

Integration IssueはA-Dの独立責務を再実装しない。

## 4. Dependency order

```text
0 Python Package / Toolchain Bootstrap
   └─> A Core State Contracts
          ├─> B Decision Core
          ├─> C Effect Safety Core
          └─> D Health / Self-Improvement

B + C + D
   └─> E Core Integration Verification
```

B/C/DはA完了後、相互にファイル競合しないよう境界を維持できる場合は並行可能。

## 5. Toolchain baseline

Phase I初期baseline:
- Python: `>=3.10`
- packaging/build: standard `pyproject.toml` + setuptools backend
- tests: pytest
- lint/static quality: Ruff
- typing: Mypy strict
- layout: `src/`

Python 3.10互換を壊すlanguage featureを導入する場合は、別のdesign decisionとして本書/関連Issueを更新する。

## 6. Verification policy

Bootstrap Work最低限:
- package import smoke
- pytest smoke
- Ruff
- strict Mypy
- compileall
- `git diff --check`

各Core Work最低限:
- targeted unit tests
- Ruff
- strict Mypy
- full pytest（テスト母集団確立後）
- compileall
- `git diff --check`

Phase I Integrationでは追加で:
- provider import boundary test
- deterministic repeated-decision test
- stale/competing lineage fail-closed scenarios
- wait-only + independent actionable scenario
- crash/effect ambiguity decision scenario
- health storm/self-recursion guard scenario

## 7. Implementation lineage

- 1 Work Issue = 1 active implementation lineage
- Parent Issue自身ではimplementation branch/PRを作らない
- #15 Bootstrap WorkをAより先にmainへ統合する
- Integration IssueはA-D merge後の`main`から開始する
- stacked PRが必要な場合は同一lineageであることをIssue checkpointへ明記する

## 8. Non-goals

- GitHub adapter
- Project adapter
- filesystem RuntimeStore
- ExecutionLease backend
- Codex launcher/adapter
- CI dispatch
- Reviewer broker
- Runner CLI/application composition
- Yura固有mapping
- PostgreSQL

## 9. Phase completion

Phase I complete条件:
- Bootstrap + A-D Workがmainへcanonicalized
- E IntegrationがPASS
- Coreからprovider SDK/importが存在しない
- canonical architectureとのblocking contradiction 0
- Phase II Host Runtimeが依存できるstable Core contractが確立
