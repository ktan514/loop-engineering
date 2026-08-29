# AI Liver ゆらから引き継ぐ日本語化・実運用修正履歴

## 1. 目的

`ai-liver-yura`でLoop Engineeringを実際に運用した際に発生した問題と、その修正を専用Repositoryへ引き継ぐ。単に最終source treeだけをコピーすると、なぜ現在の境界や文章規則が必要になったかが失われるため、再発防止の履歴証拠として保持する。

抽出元:
- Repository: `ktan514/ai-liver-yura`
- branch: `rebuild/v2-foundation`
- snapshot HEAD: `1ef8e5de344e854865187e0dd8c465eca650f333`
- 日本語・Git履歴管理Authority: source Issue #384

## 2. 実運用で発生した問題

### PR #479 — 試験対象の自己再選択

#471が基盤統合後もopenのまま実製品試験証拠を待つため、計画処理が#471自身を再選択し続ける可能性があった。

修正:
- #462 / #471自身を試験候補から除外
- Loop Engineering基盤Issueを試験候補から除外
- 依存関係を満たしたV2製品Workだけを選択
- 候補なしなら待機状態をCheckpointへ明示

### PR #480 — Codex CLI起動互換性

`codex exec --full-auto`がCodex CLI 0.150系で構文エラーになり、Loopが`NEXT_WORK_PLANNING_FAILED`で停止した。

修正後の既定契約:

```text
codex -a never exec --sandbox workspace-write -c sandbox_workspace_write.network_access=true <instruction>
```

Codexはファイル編集・検証を担当し、Git管理情報の変更は信頼済みホストが担当する。

### PR #481 — 実行状態が見えない

長時間無音で、Loopが実行中か停止中か判断できなかった。子プロセスのstderrも失われていた。

修正:
- 標準エラーへ人間向け進捗
- 標準出力は最終JSON専用
- 永続ログ`logs/loop_engine/`
- child failureの安全な診断
- 秘密情報や完全argvをログしない

### PR #482 — `dirty` PRを誤ってmergeへ送る

`mergeable_state=dirty`でも厳密HEAD CI PASSだけを見てmergeを試し、人間介入へ落ちていた。

修正:
- Ready/merge直前にGitHub mergeabilityを再取得
- `dirty`は1回のCodex再調整へ戻す
- rebase / force pushは禁止
- credential/permission/transport失敗を競合として誤分類しない

### PR #483 — console出力過多と生存確認不足

全child outputをterminalへ流すと読みにくく、Codexが無出力になると再び停止に見えた。

修正:
- 既定consoleは主要工程だけ
- 生出力はpersistent logへ保存
- Codex実行中は既定60秒ごとに生存通知（heartbeat）
- `--verbose`だけ詳細表示

### PR #484 — 操作者が毎回再起動しないと進まない

1回の限定遷移ごとにprocessが終了し、Codexにも固定wall-clock timeoutがあった。

修正:
- 既定CLIは`COMPLETED`後に再観測して継続
- `--once`を診断用1遷移として維持
- current-head `CI_PENDING`だけ粗い間隔で自動再確認
- review、人間確認、credential/provider待ちは高頻度監視しない
- Codexを固定wall-clock timeoutでkillしない
- 同一`COMPLETED`反復は`NO_PROGRESS_GUARD`

### PR #485 / #486 — Mission Checkpointの対象形式不一致

計画Codexが`選択した次Work: #471`と書き、Host parserが必須とする`current Work:`を欠落させたため、`GITHUB_OBSERVE_FAILED`になった。

修正:
- 固定項目`- current Work: #<issue>`を必須化
- active PRがあれば`current PR`と`exact HEAD`も必須
- 別名だけでの代用を禁止
- 観測失敗を型付き理由付きで表示

### PR #489 — Codex終了コード0でも前進していない / Git操作境界

Codex processが正常終了してもGitHub stateやCheckpointが進んでいない場合があり、同じ作業を繰り返す危険があった。また`workspace-write`では`.git`操作ができないため、Codexに広い権限を与える案は採用しなかった。

修正:
- Codex終了コードだけで成功判定しない
- PR/HEAD/Checkpointの前進を信頼済みホストが再取得して確認
- Codexは`workspace-write`でファイル編集と検証だけを担当
- branch作成、merge、add、commit、push、PR、Checkpointは信頼済みホストが担当
- `danger-full-access`を正規方式にしない

## 3. 日本語文章問題と是正

### source Issue #384

Repository内とGitHub上の人間向け文章に英語のみ、または不自然な英語技術語混在が残り、AIごとに出力言語が揺れる問題を一つの管理Authorityへ統合した。

### PR #489

- `AGENTS.md`へ文章言語規則を正本化
- 日本語の意味表現を先に書き、必要な英語原語は括弧内へ併記
- 新規commit messageを日本語で生成
- Loop設計・docstringの日本語化開始

### PR #490

- Loop自律実行、統合復旧、正本レビュー、横断監査、運用記憶、自己改善の正本文書を日本語化
- Mission Goalを日本語正本へ更新
- CLI、ログ、生存通知、失敗表示を日本語化
- 健全性状態と自己改善Issue本文を日本語化

### PR #491

- 再調整、型契約、Work選択、Mission監督、書込み判定の説明文を日本語化
- 実ホスト制御のdocstring、エラー説明、Codex作業指示、Mission Checkpoint本文を日本語化
- TaskPacketの人間向け説明を日本語化

### PR #492

- `loop_mission_supervisor.md`を日本語へ全面整理
- V2正本索引説明を日本語化
- `preflight.py`の人間向けdocstringを日本語化

## 4. 専用Repositoryで維持する規則

- 人間が読む文章は日本語を基本言語とする。
- 英語概念語を日本語文の名詞としてそのまま置かず、自然な日本語の意味を先に書く。
- 原語が識別・検索・外部仕様対応に必要な場合だけ括弧内へ併記する。
- status、command、path、branch、SHA、API/class/function/field名、machine-readable値は固定値として維持する。
- 翻訳だけを理由に仕様・挙動を変更しない。
- 翻訳中に機能不具合を発見した場合は、翻訳で隠さず機能修正として分離する。
- commit messageも日本語を主要言語とする。

現在の詳細規則はRepository rootの`AGENTS.md`と`docs/architecture/v2/commit_message_language_policy.md`を正本とする。
