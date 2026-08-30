# Merge Reconciliation Cleanup Contract

Owner Issue: #50
Parent: #9
運用Authority: #26
Related observability: #52

## 1. 目的

Product Workの既存PRを最新trunkへ通常mergeして競合解消するreconciliationで、Codexまたはtrusted host finalizeが完了しなかった場合に、Product Workspaceへ未解決merge状態を残さない。

Loop Engineering自身の不具合修正を理由にProduct Repositoryの履歴を破壊・rewriteしない。

## 2. 前提

reconciliation開始前は`TrustedWorktree.prepare()`のclean gateが成立していることを必須とする。

`PreparedWorktree.start_head`はreconciliation開始直前の信頼済みHEADであり、cleanup時の照合基準として使用する。

pre-existing user changeがあるworktreeではreconciliationを開始しない。

## 3. Reversible failure

`git merge --no-commit --no-ff origin/<trunk>`開始後、merge commitがまだ作成されていない段階で次のいずれかが失敗した場合はcleanup対象とする。

- Codex実行
- unresolved conflict解消
- `git diff --check`
- `git add -A`
- staged diff判定
- merge commit作成

`MERGE_HEAD`が存在する場合はtrusted hostが`git merge --abort`を実行する。

cleanup成功条件:

- `HEAD == PreparedWorktree.start_head`
- `MERGE_HEAD`が存在しない
- `git status --porcelain`が空

上記をreadbackできた場合だけ通常の`MERGE_RECONCILIATION_FAILED`として安全に終了できる。

## 4. Effect-bearing / uncertain failure

merge commit作成後は`MERGE_HEAD`が消える。以後の失敗には次が含まれる。

- commit後のHEAD readback失敗
- push失敗または結果不明
- push成功後のPR/Checkpoint更新失敗

この段階ではremoteへeffectが発生した可能性を否定できないため、`reset --hard`、rebase、force push等で自動的に開始HEADへ戻さない。

cleanup readbackで開始HEADへ戻っていることを証明できない場合はcleanup failureとして保持し、現行Controllerは`MERGE_RECONCILIATION_FAILED`でfail-closedする。

cleanup failureを専用detail `MERGE_RECONCILIATION_CLEANUP_FAILED`として上位へ公開する観測性改善は#52で扱う。

既に発生した可能性のあるcommit/pushを「なかったこと」にしない。

## 5. Host contract

`TrustedWorktree`はreconciliation開始後、成功finalize以外の失敗経路でcleanupを試みる。

- Codex failure → `abort_merge_if_needed()`
- Codex success + finalize failure → `finalize()`内部でcleanup
- finalize success → cleanupしない

cleanup成功時は開始HEAD・`MERGE_HEAD`不在・clean statusをreadbackする。

cleanup結果が不明または失敗の場合でも、Controllerは一般的な`MERGE_RECONCILIATION_FAILED`として停止し、再dispatchしない。専用detailへの分類は#52の責務とする。

## 6. Safety

- cleanupは`PreparedWorktree.start_head`を照合して実施する
- pre-existing dirty worktreeを自動clean/resetしない
- `git clean`を使用しない
- shared historyへforce pushしない
- merge commit作成後の自動hard resetをしない
- Product側へLoop修正用commitを作らない

## 7. Verification

- Codex failure + active MERGE_HEAD → abort + clean start HEAD
- Codex success + unresolved conflict → abort + clean start HEAD
- diff-check/stage/commit failure → abort + clean start HEAD
- successful finalize → abortなし
- abort command failure → cleanup failureとして保持
- abort後HEAD mismatch → cleanup failureとして保持
- MERGE_HEADなし + HEAD changed → cleanup failureとして保持
- cleanup failure時も履歴を自動resetせずfail-closed
- pytest / Ruff / strict Mypy / compileall / diff-check PASS
- actual-host再試行で成功またはcleanなfailureとなり、未解決merge状態を残さない
