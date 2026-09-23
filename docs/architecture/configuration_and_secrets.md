# 設定ファイルと秘密情報の境界

管理Issue: #31, #36
状態: standalone設定正本

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

設定ファイルを別場所に置く場合は、CLIの`--config <path>`または非秘密の環境変数`LOOP_CONFIG_FILE`で設定ファイル自体を選択できる。

Workspace pathそのものをCLI引数や環境変数で通常上書きしない。Workspace pathのAuthorityは選択された設定ファイルとする。

Loop EngineeringのPythonコードがファイルとして直接ロードする実行設定はこのconfigであり、`.env`を直接探索・読込しない。

## 3. Workspace

対象ProductのローカルWorkspace pathは設定ファイルへ絶対pathで明示する。

例:

```ini
[project]
key = sample-product
workspace_path = /Users/example/workspace/sample-product
repository = owner/sample-product
trunk_branch = main
work_branch_template = loop/work-{issue}
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

`loop-engineering`自身を対象にしたstandalone実運転では、現在のAuthorityを次とする。

- Repository: `ktan514/loop-engineering`
- Project: `loop-engineering` / Project #9
- Mission: Issue #33
- Parent: Issue #9
- 運用Authority: Issue #26
- 現在のIntegration Work: Issue #27
- `root_issue`: 未設定

これらの役割を便宜的に同一Issue番号へ統合しない。

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
reviewer_api_key_env = OPENAI_API_KEY
```

`reviewer_api_key_env`には秘密値ではなく環境変数名だけを書く。OpenAI API keyの標準環境変数名は`OPENAI_API_KEY`とし、`OPENAI_API_KEY_REVIEWER`は使用しない。

## 6. 秘密情報と`.env`

次のような値は設定ファイル・Repository・Issue・PR・Checkpoint・通常logへ直接保存しない。

- GitHub token
- OpenAI/API key
- database password / DSNに含まれるcredential
- private key
- reviewer credential

Repository rootの`.env`はPipenvが標準機能で自動読込するdotenvファイルとして使用する。

現時点の最小構成:

```dotenv
OPENAI_API_KEY=
GH_TOKEN=
```

`.env`には単純な環境変数だけを記載する。shell command substitution、`find`、`dirname`、PATH組立処理を置かない。

`CODEX_BIN`や`PATH`などshell自体の実行環境設定はzsh側の設定ファイルで管理する。

`LOOP_POSTGRES_DSN`と`LOOP_TRUSTED_REVIEWER_SOCKET`は利用契約が未確定のため、ローカル`.env`へ空値や自己参照値を置かない。必要性と値を確定した時点で追加する。

`.env`はGit管理対象外とする。`.env.example`には実値を入れない。

## 7. Pipenvとの関係

通常起動はPipenvを使用する。

```text
pipenv run python -m loop_engineering
```

Pipenvがproject rootの`.env`を自動読込して子process環境へ注入する。通常起動前に`source .env`を要求しない。

Loop Engineering自身は`.env`をloadせず、注入済み環境変数を参照しながら`config/loop-engineering.ini`をロードする。

既にホスト環境へ注入された環境変数も同じ契約で使用できる。

## 8. 設定優先順位

安全policyを低い層から緩和しない。

```text
Host Safety Policy
→ Platform Mandatory Policy
→ 設定ファイル
→ bounded CLI override
```

CLI overrideは設定ファイルの選択等の明示的な一時変更に限定する。Workspace pathやtoken/API keyをCLI引数へ直接渡す方式は採用しない。

## 9. Hard invariants

- Workspace pathは選択された設定ファイルから解決する
- Pythonコードは`.env`を直接loadしない
- `.env`はPipenvが自動読込するdotenvとして扱う
- `.env`へshell command substitutionを書かない
- `CODEX_BIN` / `PATH`はzsh側で管理する
- 通常起動前に`source .env`を要求しない
- 秘密情報の実値を設定ファイルへ保存しない
- 設定ファイルには秘密情報の環境変数名を記録できる
- OpenAI API keyの標準環境変数名は`OPENAI_API_KEY`とする
- `.env`をcommitしない
- 未確定の秘密情報を空値で「設定済み」にしない
- 設定と実Workspace/Repository identityが不一致ならfail-closed
- Product固有値をCoreへハードコードしない
- model/provider変更をCore state machineの変更理由にしない

## 10. Worker ProfileとReview Level設定

Issue #98以降、モデル設定Authorityを二層へ分離する。

### 10.1 Loop Engineering側

Loop Engineeringの設定は「工程でどのBackend / Profileを使うか」と外部Review Levelを所有する。

概念例:

```ini
[models]
implementer_provider = local-llm-coder
implementer_profile = local-main
local_reviewer_provider = local-llm-coder
local_reviewer_profile = local-main

[local_llm_coder]
endpoint = http://127.0.0.1:8765
production_name = product-workspace

[review.level.1]
provider = openai
model = <level-1-model>
required = true

[review.level.2]
provider = openai
model = <level-2-model>
required = true
```

既存の`implementer_model` / `reviewer_model`単一設定は移行期間の互換入力とし、新しいprofile / level設定へ正規化する。`implementer_provider = local-llm-coder`の場合、`[local_llm_coder]`の`endpoint` / `production_name`と`implementer_profile`をBackend設定へ正規化する。Loop EngineeringはBackend repository rootを保持せず、Product `workspace_path`とWorker requestのcanonical pathをlocal-llm-coder側preflightで照合する。Core state machineへmodel名を埋め込まない。

### 10.2 local-llm-coder側

`local-llm-coder`は選択されたlocal profileから実provider / model / endpointを解決する。

実環境profileはGit管理外設定とし、Repositoryへはexample/schemaだけを保存する。

現在の固定Ollama modelを含む`config/opencode.json`は将来runtime templateへ変更し、selected profileからmanaged runtime configを生成する。

model / endpoint変更だけでLoop EngineeringのWork/Review状態機械を変更しない。

API key等の秘密値は既存どおり環境変数参照だけを設定へ保存し、実値をprofile、TaskPacket、Checkpoint、Worker resultへ含めない。

詳細は `docs/architecture/local_llm_coder_integration.md` を正本とする。

## 11. Verification command設定

自律RunnerのLocal Qualityで実行するProduction固有test/lintはHostのtrusted configへ宣言する。CoreへPython/Node等の特定toolchainを固定しない。

```ini
[verification.command.1]
identity = tests
argv_json = ["python", "-m", "pytest"]
working_directory = .
timeout_seconds = 1200
required = true

[verification.command.2]
identity = lint
argv_json = ["ruff", "check", "src", "tests"]
working_directory = .
timeout_seconds = 300
required = true
```

`argv_json`は文字列JSON配列として読み、shell interpolationを行わず`subprocess`へargvとして渡す。working directoryはWorkspace内のrelative pathだけを許可する。

明示設定がない既存構成では互換用に`git diff --check HEAD`だけを既定verificationとして使用する。Production完成Gateとして十分なtestを要求する場合は、Productに適したrequired commandをHost設定へ明示する。

secret値をargvへ埋め込まない。credentialが必要な検証は別のtrusted capability policyを設計してから追加する。
