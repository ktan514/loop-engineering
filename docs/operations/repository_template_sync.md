# 共通Repository雛形同期設計

管理Issue: #71

状態: 実装仕様

## 1. 目的

後から作成された共通Repository雛形 `ktan514/repository-template` のGitHub運用規約と標準ファイルを、既存の`loop-engineering`固有設計・運用を失わずに導入する。

共通規約を複製して別正本を増やさず、雛形と同じ責務分離へ移行する。

- `AGENTS.md`: AI作業開始時の入口
- `docs/GITHUB_OPERATION_RULES.md`: 共通GitHub運用規約
- `docs/REPOSITORY_RULES.md`: `loop-engineering`固有の追加・強化規約

## 2. Authority

共通GitHub運用規約の同期元は、雛形Repository `ktan514/repository-template` の`main`とする。

今回参照した雛形commit:

`8f74c9e2cce23552f5db0b4999b63e1c637b0109`

`loop-engineering`固有規約は、現行`AGENTS.md`、既存の正本文書、GitHub live状態を正本として保存する。

雛形の`README.md`は「テンプレートRepositoryそのもの」の説明なのでコピーしない。`loop-engineering`のREADMEは製品READMEとして維持する。

## 3. 同期対象

### 3.1 雛形からそのまま同期するファイル

- `docs/GITHUB_OPERATION_RULES.md`
- `.github/ISSUE_TEMPLATE/01_work.md`
- `.github/ISSUE_TEMPLATE/02_bug.md`
- `.github/ISSUE_TEMPLATE/config.yml`
- `.github/pull_request_template.md`
- `.github/copilot-instructions.md`
- `CONTRIBUTING.md`
- `scripts/apply-repository-settings.sh`

### 3.2 雛形構造へ合わせて更新するファイル

- `AGENTS.md`
  - 詳細規約を保持する場所ではなく、共通規約・固有規約・対象仕様を読む入口へ縮小する。
- `docs/REPOSITORY_RULES.md`
  - 現行`AGENTS.md`のRepository固有ルールを移設する。
  - 実行環境、検証コマンド、保護対象branch、既存正本文書への参照を追加する。
- `README.md`
  - 開発規約の正本への入口だけを追加する。
- `docs/architecture/v2/commit_message_language_policy.md`
  - Repository固有言語規約の参照先を`AGENTS.md`から共通規約・固有規約へ変更する。
- `docs/operations/github_workflow.md`
  - Issue本文の日程fallback記載を廃止し、Project fieldを唯一の計画日程Authorityにする。
- `docs/architecture/v2/project_v2_management_spec.md`
  - Issue本文の日程fallback記載を廃止する。
  - 文章言語規約の参照先を新しい共通規約・固有規約へ変更する。

## 4. 維持するRepository固有規約

雛形共通規約より強い次のルールは削除せず、`docs/REPOSITORY_RULES.md`へ移す。

1. ファイルシステム変更前に対象pathと既存namespaceを確認する安全規則。
2. 人間向け文章を日本語で成立させ、英語概念語を必要以上にそのまま文章へ持ち込まない規則。
3. commit prefixは機能追加で`feat:`、branch prefixは`feature/`とする規則。
4. 通常PRはmerge commitを使用し、統合済みbranchを再利用しない規則。
5. exact HEADへCI・review evidenceをbindする規則。
6. Autonomous Completion Missionの継続、停止、review待ち、Work選択の規則。
7. `.github/workflows/**`等のsecurity-sensitive変更を通常変更より厳しく扱う既存運用。

共通規約を緩和する内容は追加しない。

## 5. Issue / PR template

今後の新規Issueは、通常作業と不具合の共通見出しを雛形と一致させる。

Project fieldで管理する次の値はIssue本文へ重複記録しない。

- Status
- Priority
- 作業種別
- 領域
- 工程
- Iteration
- Quarter
- Start date
- Target date
- Assignees

既存のRepository正本文書にもIssue本文の日程をfallback Authorityとして残さない。Projectを使用する`loop-engineering`では`Start date` / `Target date` fieldを計画日程の正本とする。

既存Issue本文は履歴として一括書換えしない。新規作成・通常更新から標準書式を適用する。

PR本文も雛形templateを導入し、関連Issue、目的、変更内容、設計・仕様、検証、Human Verification、影響・リスク、最終確認を標準化する。

## 6. GitHub branch protection

雛形の`apply-repository-settings.sh`は既定branchに次を設定する。

- Pull Request経由
- force push禁止
- branch削除禁止
- adminにも適用

現時点の`loop-engineering/main`はGitHub live readbackで`protected=false`である。

ChatGPTのGitHub接続にはRepository admin権限がないため、本PRではscript同期までを行う。PR統合後、owner権限を持つローカル`gh`で次を実行する。

```bash
./scripts/apply-repository-settings.sh ktan514/loop-engineering
```

その後、`main`のlive protectionを再読戻しして完了判定する。

## 7. GitHub Project雛形

雛形Project #10のfield / view / workflow同期は、Repositoryファイル同期と責務を分ける。

現在のChatGPT GitHub接続ではProjects v2のfield/view/workflowを取得・更新する操作が公開されていないため、推測でfield IDやoption IDを書かない。

Project #10のlive stateを取得できる操作経路が用意された時点で、#71上で差分監査を再開する。

## 8. 検証

Repository変更では次を確認する。

1. 雛形から同期対象としたファイル内容が同期元commitと一致する。
2. `AGENTS.md`から共通規約・固有規約へ到達できる。
3. 現行`AGENTS.md`の固有ルールが`REPOSITORY_RULES.md`へ失われず移っている。
4. commit/branch命名規則に矛盾がない。
5. Issue / PR templateがRepositoryに存在する。
6. Project field管理値をIssue本文へ重複するRepository正本が残っていない。
7. 既存CI workflowを削除・上書きしていない。
8. Ruff、strict Mypy、全pytest、compileall、diff-checkのexact-head CIがPASSする。
9. PR統合後にbranch protectionをowner権限で適用し、live readbackする。
10. Project #10の同期未完了状態をDoneとして隠さない。

## 9. 非対象

- V2 runtime / DB / effect実装の変更
- PR #70の作業系列への変更混入
- 既存Issue本文の一括整形
- 雛形RepositoryのREADMEによる`loop-engineering` READMEの置換
- Project #10のfield ID・option IDの推測
- admin権限や認証状態の独断変更
