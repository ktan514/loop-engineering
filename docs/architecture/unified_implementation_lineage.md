# Loop Engineering 統一実装正本

管理Issue: #24
運用Authority: #26
状態: 統合正本

## 1. 目的

`loop-engineering` に存在する初期汎用設計、旧抽出PR #22、旧GitHub運用PR #23、現行Yura同期PR #25を競合した別実装として残さず、1本の実装正本へ統合する。

統合では単純な最新版優先やファイル上書きを行わない。各責務について、実運用で成熟した挙動と、専用Platformとして必要な汎用性・安全境界の両方を満たす解決結果を採用する。

## 2. Git上の統一lineage

統合branchは次の1本だけとする。

`sync/ai-liver-yura-loop-engineering-20260829`

このbranchへ次の旧lineageを履歴上も合流させる。

- `feature/extract-yura-loop-engine@bb870412072516b7aed1ddc0b47a6b26a34320a8`
- `management/yura-github-operations@c69265f4d4936b0a4b0c7a8392306868f711fdc2`
- 現行Yura同期lineage

統合後はPR #25を通常mergeで`main`へ統合し、`main`を唯一のRepository正本branchとする。旧branchは新規作業に再利用しない。

## 3. 実装配置

専用Platformの実装正本は`src/loop_engineering/**`とする。

`ai-liver-yura`で`tools/loop_engine/**`に置かれていた理由は、製品runtime package `app/**` から開発制御系を物理分離するためである。専用RepositoryではPlatform自身がpackageなので、その実装を`src/loop_engineering/**`へ昇格する。

最終treeで`tools/loop_engine/**`を独立実装として維持しない。移行元の内容とSHAはGit履歴および`docs/history/**`で追跡可能にする。

## 4. 責務ごとの統合規則

### 4.1 Core / Mission監督

現行Yura版の次の成熟挙動を採用する。

- 観測→再調整→再開判定→選択→実行→再取得
- Work単位競合とMission全体競合の分離
- `ScheduleKey`による重複抑止
- `CONTINUE` / `YIELD_EXTERNAL` / `INTERVENTION_REQUIRED` / `MISSION_COMPLETE`
- 自己改善候補生成
- 書込み前条件と効果再取得

同時に旧PR #22の`LoopEngineConfig`相当を維持し、Repository、owner、Project、trunk、Authority、Mission等のProduct固有identityをCoreへ固定しない。

### 4.2 実ホストRunner

Yura現行版の次を採用する。

- 継続実行CLI
- Codex生存通知
- Codex終了コード0だけを進捗証拠にしない
- `workspace-write`のCodexと信頼済みホストGit操作の分離
- CI待機だけを粗い間隔で自動再観測
- `dirty` PRの再調整
- `NO_PROGRESS_GUARD`
- 秘密情報を含まないログ

専用側では対象Repository、trunk、Mission Issue等を設定/Profileから解決する。

### 4.3 GitHub / Project運用

旧PR #23の補助script自体は採用しない。

一方で次の契約は現行運用正本へ統合して維持する。

- GitHub liveを現在状態の正本とする
- Project field / option / item identityを変更直前にlive解決する
- 変更後に効果を再取得する
- Status、Priority、Area、Issue level、Start date、Target dateを管理する
- `Verification`を必要に応じて経由する
- 1 Work = 原則1能動実装lineage
- merge前に厳密HEADを再取得する
- 通常統合はmerge commit方式
- review待ちだけでMission全体を停止しない

正本運用文書は`docs/operations/github_project_management.md`と`docs/operations/loop_engineering_operation_hub.md`へ統合する。古い同名・重複文書がある場合は内容を失わないよう差分を吸収してから正本関係を明示する。

### 4.4 日本語

`AGENTS.md`と`commit_message_language_policy.md`を継承する。

人間が読む文章は日本語を基本言語とし、英語技術語は必要に応じて日本語の意味を先に示す。status値、branch名、command、path、SHA、API/class/function/field名など機械契約は変更しない。

## 5. 抽出元の扱い

`ktan514/ai-liver-yura`は読取専用の移行元であり、この統合作業から一切変更しない。

移行元に問題を発見した場合も、修正は専用`loop-engineering`側へ実装する。

## 6. 既知の実運用問題

移行元の継続実行で次が観測されている。

```text
transition 1: COMPLETED detail=PILOT_PLANNING_DISPATCHED
transition 2: COMPLETED detail=WORK_MERGED
transition 3: INTERVENTION_REQUIRED detail=GITHUB_OBSERVE_FAILED:MISSION_CHECKPOINT_TARGET_UNRESOLVED
```

この問題は統合後の実動作確認対象とする。統合によって自然に解消しない場合、専用側で実運転しながら修正する。

`WORK_MERGED`直後のCheckpointが次の`current Work`をまだ持たない正常な遷移状態なのか、Checkpoint契約違反なのかを区別し、正常な「次Work計画待ち」を観測失敗として停止させない設計を検討する。ただしsource側の挙動を推測で書き換えず、専用側で再現確認後に修正する。

## 7. Merge Gate

PR #25は次を満たすまで`main`へmergeしない。

1. 旧PR #22 / #23のbranch HEADが統合branchの祖先として記録される
2. 現行Yura実装の機能が`src/loop_engineering/**`へ統合される
3. Product固有固定値がProfile/Config境界へ移される
4. `tools/loop_engine/**`との二重実装が解消される
5. 旧運用文書の有効規則が現行運用正本へ吸収される
6. 英語のみの人間向け新規文章が残らない
7. Pipenvでpytest / Ruff / strict Mypy / compileall / `git diff --check`がPASSする
8. PR #25の厳密HEADと検証対象HEADが一致する
9. merge後の`main`をfresh readbackする

## 8. merge後の正本

PR #25の統合後は`main`だけを新規作業の起点とする。

旧設計branch、旧抽出branch、旧管理branch、同期branchは履歴証拠としてのみ扱い、新しいcommitを追加しない。
