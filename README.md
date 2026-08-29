# loop-engineering

汎用的なLoop Engineering Platformを構築するためのリポジトリです。

本リポジトリは特定Productに依存せず、複数のProduct Workspaceに対して、安全なObserve / Reconcile / Resume / Execute / Verify / Review / Integrate / Checkpointループを提供します。

## 初回設定

依存環境はPipenvで再現します。

```bash
pipenv sync --dev
```

実行設定templateをローカル設定へコピーします。

```bash
cp config/loop-engineering.example.ini config/loop-engineering.ini
cp .env.example .env
```

`config/loop-engineering.ini`には、修正対象ProductのWorkspace path、Repository、GitHub Project、Mission、CI、利用モデル、API endpoint等の非秘密設定を記入します。

```ini
[project]
workspace_path = /absolute/path/to/product-workspace
repository = owner/repository
```

API key、token、database credential等の秘密値は設定ファイルへ直接書きません。設定ファイルには環境変数名だけを記載し、実値はGit管理外の`.env`へ定義します。

```dotenv
GH_TOKEN=...
OPENAI_API_KEY_REVIEWER=...
LOOP_POSTGRES_DSN=...
```

`config/loop-engineering.ini`と`.env`はGit管理対象外です。Repositoryへはexampleだけを保存します。

## 起動

```bash
pipenv run python -m loop_engineering
```

別の設定ファイルを一時的に使用する場合だけ`--config`を指定できます。

```bash
pipenv run python -m loop_engineering --config /path/to/another.ini
```

起動時に設定されたWorkspaceのcanonical path、Git root、remote Repository identity、HEAD、dirty stateを確認し、設定対象と一致しないWorkspaceでは変更を開始しません。

## 設計・運用

- Platform / Product / Workspace境界: `docs/architecture/workspace_boundary.md`
- Project Profile: `docs/architecture/project_profile.md`
- 設定と秘密情報: `docs/architecture/configuration_and_secrets.md`
- GitHub運用: `docs/operations/github_project_management.md`

現在の統合作業はGitHub Issue #24 / PR #25で管理します。
