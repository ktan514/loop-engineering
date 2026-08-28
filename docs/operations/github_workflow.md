# GitHub Development Workflow

Owner: Issue #1
Status: Initial canonical operations draft

## 1. Purpose

`loop-engineering` 自身の設計・実装を、PlatformがProductへ要求する安全原則と矛盾しない形で運用する。

## 2. Authority

Repository stateの正本:

- GitHub live Issue / PR / branch / commit
- Repository canonical design
- GitHub Project `loop-engineering` planning fields（利用可能な操作経路で管理）

chat summary / memoryをcurrent branch/PR/headの正本にしない。

## 3. Issue hierarchy

基本:

- `Parent`: 複数Work/Integrationを束ねる完成目標
- `Work / Architecture`: 独立設計責務
- `Work / Implementation`: 独立実装責務
- `Integration`: E2E/結合検証
- `Management`: migration/audit/project operation

1 Workは原則1 active implementation lineage。

## 4. Dates

Ready/In progressへ進むIssueにはStart/Target dateを持たせる。

日付はplanning情報であり、品質Gateを緩める理由にしない。

Project fieldsが利用可能ならProject側を日程の正本とし、Issue本文の日付はfallback/plan snapshotとして扱う。

## 5. Branch strategy

Canonical branch:

```text
main
```

設計:

```text
design/<topic>
```

実装:

```text
feature/<topic>
fix/<topic>
```

検証専用:

```text
test/<topic>
```

`main`へ通常開発commitを直接pushしない。初期empty repository bootstrapのような例外は明示する。

force-push/rebaseによる共有lineageの履歴破壊は原則禁止。

## 6. Design before implementation

Product code/Platform implementationを変更するWorkは、実装前にcanonical designを更新する。

設計が複数Issueを横断する場合はParent/Architecture Gateで矛盾を解消してからimplementation lineageを開始する。

## 7. Pull Request policy

- Work開始時に早期Draft PRを作成可能
- PR bodyへOwner Issue / scope / non-goals / canonical design / base/head evidenceを記録
- exact current HEADをCI/review evidenceへbind
- design-only PRとimplementation PRの責務を区別
- unrelated responsibilityを1 PRへ混在させない

## 8. Review policy

実装担当自身の自己確認だけでfinal Review PASSにしない。

必要なWorkではIndependent Reviewerを使用する。

Review evidence:

- exact PR/change target
- trusted canonical design generation
- validation evidence

にbindする。

HEAD変更後は旧reviewをcurrent PASSとして扱わない。

## 9. CI policy

実装開始後に具体的toolchainを確定する。

最低限の思想:

- unit
- type/static checks
- lint/format policy
- compile/build
- integration contracts
- exact target CI

CI PASSはtested target identityを必ず追跡する。

## 10. Resume checkpoint

重要状態遷移でIssueへcheckpointを残す。

最低限:

```text
Target Issue
Canonical docs
PR / branch
Base identity
Head identity
Current phase/status
Last verification
Next action
Conflicts
```

checkpoint後もrestart時はGitHub liveと照合する。

## 11. Architecture Completion Gate

初期Platform実装はIssue #1が完了するまでFreeze。

Pass条件:

- #2-#6設計完成
- canonical docs Repository化
- cross-design blocking contradiction 0
- migration ownership一意
- implementation start order確定
- Human architecture confirmation

## 12. Migration ownership

Yura側にactive Loop Engineering lineageが存在する間、新Repositoryで同責務の実装を無断並行開始しない。

`docs/migration/ai_liver_yura.md` のMigration Gateに従う。

## 13. Provider-specific implementation issues

Architecture Completion後、実装Issueは少なくとも次の責務へ分割する想定:

1. Core domain/state contracts
2. RuntimeStore / Workspace / Lease
3. GitHub SourceControl/Planning adapters
4. Preflight
5. Implementer adapter
6. CI adapter
7. Reviewer broker/adapter
8. Runner/application composition
9. Integration/recovery
10. Yura Product Profile/pilot

実際のIssue分割はCompletion Audit後に確定する。

## 14. Security-sensitive changes

次を通常repair loopと同じ扱いにしない:

- `.github/workflows/**`
- credential/bootstrap
- reviewer/control-plane security code
- Project Profile security fields
- destructive source-control functions

これらを対象とするWorkはscopeを明示し、追加review/verificationを要求する。

## 15. Done semantics

コードがcommitされたこと、PRがopenしたこと、AI reviewが1回通ったことだけでDoneにしない。

WorkのAcceptance、required verification、integration evidence、必要なHuman Gateを満たしてDoneとする。
