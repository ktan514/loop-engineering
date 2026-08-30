# GitHub Project Rate Limit / External Wait Contract

Owner Issue: #48
Parent: #9
運用Authority: #26

## 1. 目的

長時間のcontinuous runでGitHub Projects v2のGraphQL quotaが一時的に枯渇しても、認証・権限不備と誤分類して人間介入停止せず、安全なexternal waitを経てfresh readから自動再開する。

GitHub live Issue / PR / Projectはcurrent-state Authorityのままとし、rate limit中にPostgreSQLや過去snapshotを現在状態の代替Authorityとして使用しない。

## 2. Failure classification

GitHub Project capability確認は次を区別する。

- `AVAILABLE`: Projectをreadでき、必要なwrite authorityも確認できる
- `RATE_LIMITED`: GitHub APIが明示的にrate limit exhaustionを返した
- `UNAVAILABLE`: 認証、権限、Project identity、CLI、network等の理由で必要capabilityを証明できない

`RATE_LIMITED`だけは一時的external waitとして扱う。`UNAVAILABLE`は従来どおりfail-closedで`INTERVENTION_REQUIRED`とする。

## 3. Preflight request budget

PreflightはProject contentsを取得する場所ではなくcapability gateである。

各PreflightでProject read/writeを確認するために、同一Projectへ複数の`project view` / `field-list` / `item-list`を実行しない。`projectV2`の存在と`viewerCanUpdate`を取得する小さな単一GraphQL queryを使用する。

Project item一覧はWork選択等、実際にProject current-stateが必要なtransitionでのみ取得する。同一Codex transition内ではfull item-listを原則1回だけfresh取得し、そのtransition内の照合に再利用する。

## 4. Runtime transition

PreflightでProject API rate limitを検出した場合:

```text
HostTransitionStatus.YIELD_EXTERNAL
Detail = GITHUB_PROJECT_RATE_LIMIT
```

とする。

- GitHub mutationを開始しない
- Codexをdispatchしない
- Product Workspaceを変更しない
- Operational Storeには通常のexternal wait / transition evidenceとして記録可能
- continuous CLIはbounded backoff後に新しいtransitionを開始し、必ずfresh Project readを再試行する

rate limit以外の`GITHUB_PROJECT_READ` / `GITHUB_PROJECT_WRITE` failureは`PREFLIGHT_BLOCKED:*`のままにする。

## 5. Backoff

reset時刻を信頼できる形で取得できない場合でもbusy loopしない。continuous CLIはProject rate limit専用のbounded exponential backoffを使用する。

初期値は5分、最大15分とする。成功した通常transition後は初期値へ戻す。

## 6. Console

構造ラベル・status・detailは英語を基本とし、人間向け説明を日本語にする。

例:

```text
Transition 17: YIELD_EXTERNAL detail=GITHUB_PROJECT_RATE_LIMIT
External Wait: GitHub Project API rate limitのため300秒後に再開します
```

正常なpredicate resultを`失敗`と誤表示する問題は別のconsole改善責務として扱う。

## 7. Verification

- Project capability probeがPreflightあたり1 GraphQL requestである
- Project存在 + `viewerCanUpdate=true`でread/write PASS
- `viewerCanUpdate=false`はwrite blocker
- explicit API rate limitはtyped diagnosticとなる
- rate limitはdurable hostで`YIELD_EXTERNAL`へ変換される
- auth/permission/identity failureは`INTERVENTION_REQUIRED`のまま
- continuous CLIがProject rate limitで自動再開する
- retry時にfresh probeを実行する
- Project cache / PostgreSQLだけでcurrent stateを代替しない
