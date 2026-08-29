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
GH_TOKEN=
OPENAI_API_KEY=
```

Repository rootの`.env`はPipenvが自動読込します。通常起動前に`source .env`は行いません。Loop EngineeringのPythonコードも`.env`を直接読みません。

`CODEX_BIN`や`PATH`などshell自体の設定はzsh側で管理し、`.env`へshell command substitutionを置きません。

`config/loop-engineering.ini`と`.env`はGit管理対象外です。Repositoryへはexampleだけを保存します。

`LOOP_POSTGRES_DSN`と`LOOP_TRUSTED_REVIEWER_SOCKET`は、利用契約を確定するまではローカル`.env`へ未確定値を定義しません。

## standalone自身を対象にした実運転設定

`loop-engineering`自身をProduct Workspaceとして検証する場合、GitHub側の現在Authorityは次です。

- Repository: `ktan514/loop-engineering`
- Project: `loop-engineering` / Project #9
- Mission: Issue #33
- Parent: Issue #9
- 運用Authority: Issue #26
- 現在の実運転Work: Issue #27
- `root_issue`: 現時点では未設定

ローカルの`config/loop-engineering.ini`では、Workspace pathだけ実際の絶対pathへ置き換えます。

```ini
[project]
key = loop-engineering
workspace_path = /absolute/path/to/loop-engineering
repository = ktan514/loop-engineering
trunk_branch = main
project_owner = ktan514
project_number = 9
mission_issue = 33
root_issue =
parent_issue = 9
integration_work = 27
label = loop-engineering
authority_refs = #26, #33
ci_workflow_name = Loop Engineering Deterministic CI
improvement_area = Runtime / Infrastructure
issue_level = Work
```

Project / Mission / Parent / Integration Workを同じIssue番号へ便宜的に潰しません。GitHub liveとMission #33を現在状態の正本として扱います。

## 起動

```bash
pipenv run python -m loop_engineering
```

PipenvがRepository rootの`.env`を環境へ注入し、Loop Engineering本体は`config/loop-engineering.ini`をロードします。

別の設定ファイルを一時的に使用する場合だけ`--config`を指定できます。

```bash
pipenv run python -m loop_engineering --config /path/to/another.ini
```

CLIからWorkspace path自体を直接上書きする方式は通常経路にしません。Workspace pathのAuthorityは選択された設定ファイルです。

起動時に設定されたWorkspaceのcanonical path、Git root、remote Repository identity、HEAD、dirty stateを確認し、設定対象と一致しないWorkspaceでは変更を開始しません。

## 設計・運用

- Platform / Product / Workspace境界: `docs/architecture/workspace_boundary.md`
- Project Profile: `docs/architecture/project_profile.md`
- 設定と秘密情報: `docs/architecture/configuration_and_secrets.md`
- env読込境界: `docs/operations/env_loading_contract.md`
- GitHub運用: `docs/operations/github_project_management.md`
- active Mission Goal: `docs/operations/loop_mission_goal.md`

現在のstandalone完成MissionはGitHub Issue #33で管理し、設定Authority整備はIssue #31、旧`tools/loop_engine`整理はIssue #32、実運転確認はIssue #27で管理します。
