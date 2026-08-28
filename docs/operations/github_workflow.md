# GitHub Development Workflow

Owner: Issue #21
Status: canonical operations
Source policy: `ktan514/ai-liver-yura` Issue #207 / Loop Engineering Parent #462

## 1. Authority

Current stateはGitHub liveを正本とする。

- Issue / PR / branch / exact HEAD
- canonical design
- CI / review / Verification evidence
- GitHub Project `loop-engineering` planning fields

chat summary / memoryは候補発見には使えるがcurrent state Authorityにしない。

## 2. Issue hierarchy

Issueはモジュール単位ではなく、独立して完成・検証できる変更責務で作る。

- `Parent`: 複数Work/Integrationを束ねる完成目標。原則コードを書かない
- `Work`: 通常の基本単位。原則1 Draft PRで完了
- `Integration`: 複数Workの結合/System Verification
- `Management`: Project運用、監査、migration、roadmap

1 Work = 原則1 active implementation lineage。

設計・実装・unit test・docs更新は同一責務ならWork内タスクとし、独立責務だけ別Issueへ分ける。

## 3. Project fields

未完了Issueは原則としてProject `loop-engineering` で次を管理する。

- `Status`
  - Backlog
  - Ready
  - In progress
  - Review
  - Verification
  - Blocked
  - Done
- `Priority`
  - P0
  - P1
  - P2
  - P3
- `Area`
  - Core
  - GitHub / Planning
  - Implementer
  - Reviewer
  - CI / Verification
  - Runtime / Infrastructure
  - Self Improvement
  - Documentation / Management
- `Issue level`
  - Parent
  - Work
  - Integration
  - Management
- `Start date`
- `Target date`

必要に応じて `工程` / Iteration / Quarter / Assigneesを追加する。

field ID / option IDは推測しない。Project mutation前に必ずlive readbackする。

## 4. Dates

- Readyへ移す時点でStart date / Target dateを設定する
- In progressで実際の着手日に補正する
- Verificationが必要なWorkはユーザー確認期間もTarget dateへ含める
- Target超過時は理由をIssueへ記録し更新する
- 日付を理由に品質Gateを緩めない

ChatGPTからProject field mutationできない場合もIssue本文へStart/Targetを必ず記録し、Project同期可能になった時点で補正する。

## 5. Status transition

```text
Backlog
→ Ready
→ In progress
→ Review
→ Verification
→ Done
```

依存・権限・外部結果待ちは `Blocked`。

- code commitだけでDoneにしない
- PR openだけでDoneにしない
- 実機確認が必要ならVerificationで止める
- Issue / PR / Projectの状態を乖離させない

## 6. Branch / PR

Canonical branch:

```text
main
```

通常作業:

```text
design/<topic>
feature/<topic>
fix/<topic>
test/<topic>
management/<topic>
```

- mainへ通常開発commitを直接pushしない
- force push / rebaseで共有lineageを破壊しない
- Work開始後は早期Draft PRを作成してよい
- unrelated responsibilityを1 PRへ混在させない
- PR bodyへOwner Issue / scope / non-goals / canonical / base / exact HEADを記録する

## 7. Design before code

コード変更はcanonical designまたはWork内の設計判断を先に更新する。

既存実装を抽出・移植するWorkでは、抽出元のexact source identityと最小変換ルールを先に記録すればよい。不要な再設計は行わない。

## 8. Start / Resume Gate

作業開始・再開前にfresh readbackする。

- Target Issue
- canonical design
- active implementation lineage
- working branch / PR
- base SHA
- head SHA
- current status
- last verification
- next action
- conflicts

同一Workに複数active lineage、canonical mismatch、説明不能HEAD差分があればSTOPしてreconcileする。

checkpoint / chat / memoryだけから再開しない。

## 9. Write Gate

mutation前にtarget identityをfresh確認する。

- mainへの直接content writeは禁止
- branch content writeはbranch / PR / head SHAを確認する
- Project writeはproject / field / optionをlive resolveする
- provider応答だけを成功とせずmutation後readbackする
- effect不明時にblind retryしない

## 10. Verification order

原則:

1. targeted/module tests
2. adjacent contract tests
3. full pytest
4. Ruff
5. strict Mypy
6. compileall/build
7. `git diff --check`
8. exact-head CI / independent review（必要なWork）
9. Human/System Verification（必要なWork）

HEAD変更後は旧CI/reviewをcurrent PASSとして扱わない。

## 11. Review independence

Implementer自身の確認だけをfinal review PASSにしない。

canonical reviewが必要なWorkではreview対象をexact HEADへ固定する。REQUEST_CHANGES後は同一lineageで修正し、新HEADへreviewを取り直す。

## 12. Wait semantics

- review待ちだけでProject/Mission全体をSTOPしない
- Verification待ちWorkがあっても独立したReady Workがあれば継続する
- 全て外部待ちならbusy pollingせずyieldする
- Human判断が本当に必要な場合だけescalateする

## 13. Security

- token / API key / credentialをRepository、Issue、PR、Checkpoint、通常logへ保存しない
- untrusted Issue/PR/model outputをshell commandとして実行しない
- Project field ID / option IDを固定値として推測利用しない
- destructive source-control操作はdefault deny

## 14. Done

WorkはAcceptance、required verification、必要なReview/Verificationを満たした時のみDone。

Parentは子Work/Integrationのcompletion evidenceをfresh確認してDoneにする。
