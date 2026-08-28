# GitHub Project Operations

Owner: Issue #21
Source policy: `ai-liver-yura` Issue #207 / Loop Engineering Project #7

## Target

既存GitHub Project `loop-engineering` をplanning Authorityとして使用する。

Default:

- owner: `ktan514`
- title: `loop-engineering`
- repository: `ktan514/loop-engineering`

## One-time setup

```bash
bash scripts/setup_github_project.sh
```

このscriptはProjectをtitleでlive discoveryし、存在しなければ作成する。Repositoryをlinkし、不足している次のfieldだけを追加する。

- Status
- Priority
- Area
- Issue level
- 作業種別
- Start date
- Target date

`Status`はGitHub標準fieldを使用する。必要option:

- Backlog
- Ready
- In progress
- Review
- Verification
- Blocked
- Done

GitHub CLIが既存Status option編集を提供しない環境では、setup scriptが不足optionを表示する。その場合だけGitHub Project UIでStatus optionを一度合わせる。

## Issue synchronization

IssueをProjectへ追加しplanning fieldsを同期する:

```bash
bash scripts/project_sync_issue.sh \
  https://github.com/ktan514/loop-engineering/issues/20 \
  "In progress" \
  P0 \
  "Core" \
  Work \
  2026-08-28 \
  2026-08-29 \
  実装
```

field ID / option IDはscript内へ固定しない。`gh project`がProject/field/optionをlive resolveする。

## Authority rules

- 日程・Statusの通常AuthorityはProject
- Issue本文のStart/Targetはfallback planning snapshot
- Project mutation前後にlive readbackする
- StatusとIssue/PR実態を乖離させない
- codeだけ完了してもDoneにしない
- Human/System確認が必要なWorkはVerificationで止める

## GitHub CLI permission

`gh project`には`project` scopeが必要。権限不足時のみ次を実行する。

```bash
gh auth refresh -s project
```

## Label

Loop Engineering自身のWork/PRは `loop-engineering` labelを使用する。Product固有Issueと接続する場合も、Platform側の作業identityはこのlabelで区別する。
