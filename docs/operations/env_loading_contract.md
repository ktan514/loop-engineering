# `.env` / Pipenv / config の読込契約

管理Issue: #36
状態: standalone運用正本

## 1. 目的

Loop Engineeringの起動時に、`.env`、Pipenv、`config/loop-engineering.ini`の責務を分離する。

## 2. `.env`

Repository rootの`.env`は、Pipenvが標準機能で自動読込するdotenvファイルとして使用する。

`.env`には単純な環境変数だけを記載する。

```dotenv
OPENAI_API_KEY=
GH_TOKEN=
```

shell command substitution、`find`、`dirname`、PATH組立処理などは`.env`へ置かない。

`LOOP_POSTGRES_DSN`と`LOOP_TRUSTED_REVIEWER_SOCKET`は利用契約が未確定のため、確定するまで定義しない。

## 3. shell環境

`CODEX_BIN`や`PATH`など、shell自体の実行環境設定はzsh側の設定ファイルで管理する。

`.env`へ`CODEX_BIN` / `PATH`を置かない。

## 4. Python / Loop Engineering

`src/loop_engineering/**`は`.env`を直接探索・読込しない。

Loop Engineeringがファイルとして直接ロードする実行設定は次である。

```text
config/loop-engineering.ini
```

設定ファイルにはWorkspace、Repository、Project、Mission、モデル等の非秘密設定と、秘密値を参照する環境変数名を記載する。秘密値そのものは保存しない。

## 5. 通常起動

通常起動前に`source .env`を要求しない。Pipenvの標準dotenv読込へ任せる。

```bash
pipenv run python -m loop_engineering
```

1遷移だけ実行する場合:

```bash
pipenv run python -m loop_engineering --once
```

別のconfigを使う場合:

```bash
pipenv run python -m loop_engineering --config /path/to/another.ini
```

## 6. Hard invariants

- Pythonコードから`.env`をロードしない
- `.env`へshell command substitutionを書かない
- `CODEX_BIN` / `PATH`を`.env`で管理しない
- 通常起動前に`source .env`を要求しない
- 実行設定ファイルのAuthorityは`config/loop-engineering.ini`
- 秘密値をconfigへ直接保存しない
