# Loop Engineering 運用ハブ

この文書はGitHub上の専用運用ハブIssueと対になるRepository正本である。

## Authority

- 課題と作業の統括: GitHub Issue / Project
- 作業再開: PostgreSQLの作業記録と最後の安全Checkpoint
- 外部効果の確認: GitHubのPR / branch / commit SHA / Actions
- 計画: GitHub Project `loop-engineering` のlive field
- 設計: Repository正本文書
- 会話記憶・summary: 候補発見のみ

Issue commentは人間向けの状況報告であり、再開対象の機械入力に使わない。詳細は`docs/architecture/v2/work_state_and_issue_boundary.md`を参照する。

## 必須原則

- 1 Work = 原則1能動実装作業系列
- 設計→コード→試験
- 通常開発を`main`へ直接pushしない
- force push / rebaseによる共有履歴破壊をしない
- 通常統合はmerge commit
- CI / review / mergeを厳密HEADへ結び付ける
- 変更前に現在条件を再取得し、変更後に効果を再取得する
- review待ちだけをMission STOPにしない
- 実環境確認が必要ならVerificationを省略しない
- Start date / Target dateを計画情報として設定する
- 人間向け文章・commit messageは日本語を基本言語とする

詳細:
- `AGENTS.md`
- `docs/operations/github_project_management.md`
- `docs/operations/chatgpt_resume_gate.md`
- `docs/architecture/v2/branch_lifecycle_and_commit_hygiene.md`
- `docs/architecture/v2/commit_message_language_policy.md`
