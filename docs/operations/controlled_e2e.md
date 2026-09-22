# Controlled Production E2E Gate

Issue #89の製造Completion Gateを、実GitHub Repository / Projectと実`local-llm-coder` Workerで実行する。

## 目的

初期入力をHost RegistrationとGoalだけに限定し、Planning / DESIGN / IMPLEMENT / Local Quality / exact-head CI / External Review / REPAIR / Integration / Work・Goal完了までを人間の逐次packet発行なしで完遂できることを確認する。

## 前提

- `gh auth status` が成功する
- GitHub tokenへProject権限がある
- `local-llm-coder` Worker #7実装が利用可能
- Ollamaと選択profileのmodelが利用可能
- `OPENAI_API_KEY`が設定されている
- `LOOP_POSTGRES_DSN`が設定されている
- Project #10「雛形（テンプレート）プロジェクト」をcopyできる

## 実行

通常は専用Repository / Project作成からGoal完了監査まで次で進める。

```bash
cd ~/workspace/loop-engineering
./scripts/controlled-e2e.sh run
```

段階確認:

```bash
./scripts/controlled-e2e.sh prepare
./scripts/controlled-e2e.sh once
./scripts/controlled-e2e.sh audit
```

既存Repositoryと同名の場合、`.loop-controlled-e2e` markerが無ければ一切操作せず停止する。既存Productionを初期化してE2Eへ流用しない。

## Review model

既定では`config/loop-engineering.ini`の`[models].reviewer_model`をLevel 1 / 2の両方へ使用する。別modelを使う場合:

```bash
export LOOP_E2E_LOW_REVIEW_MODEL=<low-level-model>
export LOOP_E2E_HIGH_REVIEW_MODEL=<high-level-model>
```

各Levelは`passes_required = 2`で、同じexact targetへfresh reviewを2回取得する。

## PostgreSQL

Host接続が既定。Docker接続の場合:

```bash
export LOOP_E2E_POSTGRES_DRIVER=docker
export LOOP_E2E_POSTGRES_CONTAINER=<container-name>
```

## 再起動試験

`once`は1 iterationでprocessを終了する。同じcommandを繰り返してもPostgreSQL durable stateとGitHub live stateから再開し、Issue / Project item / branch / PRを重複作成しないことを確認する。

`run`はGoal完了までcontinuousに進める。

## 証拠

成功時の最終出力は`CONTROLLED_E2E_AUDIT=PASS`。Repository、Project番号、main exact HEAD、Goal/Work Issue数、merged PR数も表示する。

effect送信直前/直後、UNCERTAIN、stale evidence、competing lineage等は#89のfault-injection Gateと組み合わせて最終Completion判定する。
