# 設定ファイルと秘密情報の境界

管理Issue: #24
状態: 統合正本

## 1. 目的

Loop Engineering本体のコードから、対象ProductのWorkspace path、Repository、GitHub Project、Mission、CI、モデル、API endpoint、運用記憶等の実行設定を分離する。

秘密情報は設定ファイルへ直接保存せず、設定ファイルには利用する環境変数名だけを記録し、実値はGit管理外の`.env`またはホスト側の秘密情報注入機構から供給する。

## 2. 設定ファイル

実行時の既定設定ファイル:

```text
config/loop-engineering.ini
```

このファイルはホスト・Workspace固有情報を含むためGit管理対象外とする。

Repositoryへはtemplateだけを保存する。

```text
config/loop-engineering.example.ini
.env.example
```

設定ファイルを別場所に置く場合は非秘密の環境変数`LOOP_CONFIG_FILE`で明示できる。

## 3. Workspace

対象ProductのローカルWorkspace pathは設定ファイルへ絶対pathで明示する。

例:

```ini
[project]
key = ai-liver-yura
workspace_path = /Users/example/workspace/ai-liver-yura
repository = ktan514/ai-liver-yura
trunk_branch = rebuild/v2-foundation
```

Loop Engineeringはホームディレクトリを暗黙探索しない。

実行前に最低限次をfresh確認する。

- `workspace_path`のcanonical path
- `git rev-parse --show-toplevel`
- remote repository identity
- current branch/ref
- current HEAD
- dirty/untracked state

設定されたRepositoryIdentityと一致しないWorkspaceではCodex/Git mutationを開始しない。

## 4. GitHub / Planning

Repository名、owner、Project番号、Mission/Root/Parent/Integration Issue、CI workflow等は設定ファイルへ置く。

これらをCoreへハードコードしない。

## 5. モデル / API

モデル名、provider種別、API endpoint、timeout等の非秘密設定は設定ファイルへ置く。

例:

```ini
[models]
implementer_provider = codex
implementer_model = default
reviewer_provider = openai
reviewer_model = gpt-5.6-terra
reviewer_api_base = https://api.openai.com/v1
reviewer_api_key_env = OPENAI_API_KEY_REVIEWER
```

`reviewer_api_key_env`には秘密値ではなく環境変数名だけを書く。

## 6. 秘密情報

次のような値は設定ファイル・Repository・Issue・PR・Checkpoint・通常logへ直接保存しない。

- GitHub token
- OpenAI/API key
- database password / DSNに含まれるcredential
- private key
- reviewer credential

実値は`.env`またはホスト側の秘密情報sourceに置く。

例:

```dotenv
GH_TOKEN=...
OPENAI_API_KEY_REVIEWER=...
LOOP_POSTGRES_DSN=...
```

`.env`はGit管理対象外とする。`.env.example`には値を入れず必要な変数名だけを記録する。

## 7. Pipenvとの関係

通常起動はPipenvを使用する。

```text
pipenv run python -m loop_engineering
```

Pipenvがproject rootの`.env`を読み込むため、Loop Engineering自身で独自の秘密情報ファイル探索を行わない。

既にホスト環境へ注入された環境変数も同じ契約で使用できる。

## 8. 設定優先順位

安全policyを低い層から緩和しない。

```text
Host Safety Policy
→ Platform Mandatory Policy
→ 設定ファイル
→ bounded CLI override
```

CLI overrideはWorkspace等の明示的な一時変更に限定し、token/API keyをCLI引数へ渡す方式は採用しない。

## 9. Hard invariants

- Workspace pathは設定ファイルまたは明示CLI指定からのみ解決する
- 秘密情報の実値を設定ファイルへ保存しない
- 設定ファイルには秘密情報の環境変数名を記録できる
- `.env`をcommitしない
- 設定と実Workspace/Repository identityが不一致ならfail-closed
- Product固有値をCoreへハードコードしない
- model/provider変更をCore state machineの変更理由にしない
