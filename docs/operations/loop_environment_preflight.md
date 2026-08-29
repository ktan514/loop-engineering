# Loop環境・利用能力の事前確認

管理Issue: #32
Mission: #33

Loop Engineeringは作業系列を選択する前に実行環境と対象Workspaceを確認し、誤ったRepository / Project / Missionへ変更を開始しない。

## 正本と境界

- 対象Repository、Project、Mission等は選択された`config/loop-engineering.ini`から解決する。
- 現在状態はGitHub live Issue / PR / branch / HEAD / Project fieldを正本とする。
- Workspace pathは設定ファイルから解決し、ホームディレクトリを暗黙探索しない。
- 事前確認はProjectを変更しない。Repository / Projectの書込能力は副作用のない権限照会で確認する。
- レビューワー、Docker、PostgreSQL等の利用能力不足はWork単位の利用不可として扱い、bootstrap必須能力と分離する。
- 生のtoken、API key、database URL、外部提供元の失敗本文を通常出力へ流さない。

## 実行入口

通常の事前確認は正本packageから実行する。

```bash
pipenv run python -m loop_engineering.preflight
```

信頼済みホスト起動器から確認する場合:

```bash
python scripts/launch-loop-engineering.py --preflight
```

`tools.loop_engine` namespaceはstandalone実行入口として使用しない。

## 出力契約

事前確認はJSONオブジェクトを1つ出力する。

```json
{
  "status": "PASS",
  "capabilities": {"github_repo_read": true},
  "blocking_for_loop_bootstrap": [],
  "work_scoped_unavailable": [],
  "diagnostics": []
}
```

`status`はbootstrap必須能力がない場合`BLOCKED`、Work単位能力だけがない場合`DEGRADED`、それ以外は`PASS`とする。

## bootstrap必須確認

- 設定された`workspace_path`が存在する。
- `git rev-parse --show-toplevel`が設定Workspaceと一致する。
- `origin`のRepository identityが設定`repository`と一致する。
- HEADとworking tree状態を読み取れる。
- GitHub CLIを利用できる。
- 設定Repositoryを読み取れる。
- 設定Repositoryへの必要な書込権限を副作用なく確認できる。
- 設定Projectを`view` / `field-list` / `item-list`できる。
- 設定Projectへの更新権限を副作用なく確認できる。
- active `docs/operations/loop_mission_goal.md`のversion / generation / SHA-256が起動器から注入されたidentityと一致する。
- Project Python環境、pytest、Ruff、Mypy、compileall、Codex CLIを利用できる。

`loop-engineering`自身を検証する現在の設定ではProject #9 / Mission #33を使用する。値はコードへ固定せず設定ファイルから受け取る。

## Work単位確認

次は利用不能でもMission bootstrap全体を直ちに停止させない。

- 信頼済みレビューワー境界
- Docker
- PostgreSQL client / server / database / migration

`LOOP_POSTGRES_DSN`と`LOOP_TRUSTED_REVIEWER_SOCKET`は現在利用契約が未確定である。未定義なら該当能力を利用不可として報告し、値を推測したり空値を設定済み扱いしたりしない。

## Workspace確認

Git操作前に最低限次を確認する。

```bash
git rev-parse --show-toplevel
git remote get-url origin
git branch --show-current
git rev-parse HEAD
git status --short
```

設定されたRepositoryIdentityと一致しないWorkspaceではCodex / Git mutationを開始しない。

## 厳密HEAD CI

CI、レビュー、統合は現在PRの厳密HEADへ結び付ける。merge ref、古いCheckpoint、過去の成功結果だけを現在HEADの証拠にしない。

GitHub ActionsはPR番号からlive head/baseを解決し、期待HEADとの一致を確認してからcheckout・品質判定を行う。

## macOSホスト起動器

`python scripts/launch-loop-engineering.py`をRepository rootから使用する。

起動器は既にホスト環境へ注入された`GH_TOKEN`を使用し、Goalのversion / generation / SHA-256をactive Mission Goalから導出する。既存認証が利用できない場合に、起動器自身が`gh auth login`やtool installを開始しない。

Codex実体はホストの既存PATHから解決する。外部toolが見つからない場合は`AGENTS.md`のファイルシステム安全規則に従い、既存binary / 設定 / Repository標準経路を確認する前に環境へ新規導入しない。

## 信頼済みホストの独立レビューワー

独立レビューは通常の事前確認から分離し、`docs/architecture/v2/trusted_host_reviewer_boundary.md`の契約に従う。

OpenAI API keyの標準環境変数名は`OPENAI_API_KEY`である。API keyそのものをCodex作業領域、Issue、PR、Checkpoint、通常ログへ渡さない。
