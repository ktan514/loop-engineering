# Merge Reconciliation Cleanup Contract

Owner Issue: #50
Parent: #9
運用Authority: #26
Related observability: #52

## 1. 目的

Product Workの既存PRを最新trunkへ通常mergeして競合解消するreconciliationで、Codexまたはtrusted host finalizeが完了しなかった場合に、Product Workspaceへ未解決merge状態を残さない。

Loop Engineering自身の不具合修正を理由にProduct Repositoryの共有履歴を破壊・rewriteしない。

## 2. 前提

reconciliation開始前は`TrustedWorktree.prepare()`のclean gateが成立していることを必須とする。

`PreparedWorktree.start_head`はreconciliation開始直前の信頼済みHEAD、`PreparedWorktree.branch`は開始branchであり、cleanup時の照合基準として使用する。

pre-existing user changeがあるworktreeではreconciliationを開始しない。

## 3. Reversible failure

`git merge --no-commit --no-ff origin/<trunk>`開始後、merge commitがまだ作成されていない段階で次のいずれかが失敗した場合はcleanup対象とする。

- Codex実行
- unresolved conflict解消
- `git diff --check`
- `git add -A`
- staged diff判定
- merge commit作成

`MERGE_HEAD`が存在する場合は、最初にtrusted hostが`git merge --abort`を実行する。

cleanup成功条件:

- `HEAD == PreparedWorktree.start_head`
- current branch == `PreparedWorktree.branch`
- `MERGE_HEAD`が存在しない
- `git status --porcelain`が空

上記をreadbackできた場合だけ通常の`MERGE_RECONCILIATION_FAILED`として安全に終了できる。

## 4. `merge --abort` failure fallback

実運転では、Codexがmerge競合解消中にtracked fileへ追加編集を残した場合、`git merge --abort`が次のように失敗することがある。

```text
fatal: Could not reset index file to revision 'HEAD'.
```

この場合でも、merge commit作成前であり、reconciliation開始前のclean状態へ戻せることをfresh evidenceで証明できる場合だけ、trusted hostは限定fallbackとして`git reset --hard <start_head>`を使用できる。

fallback許可条件はすべて必須とする。

1. `PreparedWorktree.reconciliation_started == true`
2. `PreparedWorktree.pr_number`が存在する
3. current branch == `PreparedWorktree.branch`
4. current HEAD == `PreparedWorktree.start_head`
5. `MERGE_HEAD`が現在も存在する
6. GitHub PRをfresh readし、live head branch == `PreparedWorktree.branch`
7. GitHub PRのlive head SHA == `PreparedWorktree.start_head`
8. `git merge --abort`が実際に失敗した直後である

上記を満たした場合だけ次を実行する。

```text
git reset --hard <PreparedWorktree.start_head>
```

fallback後は通常cleanupと同じく、開始branch・開始HEAD・`MERGE_HEAD`不在・clean statusをreadbackする。

`git reset --hard`ではuntracked fileを削除しない。fallback後にuntracked fileが残ってstatusがcleanでなければcleanup failureとしてfail-closedする。`git clean`は使用しない。

## 5. Effect-bearing / uncertain failure

merge commit作成後は`MERGE_HEAD`が消える。以後の失敗には次が含まれる。

- commit後のHEAD readback失敗
- push失敗または結果不明
- push成功後のPR/Checkpoint更新失敗

この段階ではremoteへeffectが発生した可能性を否定できないため、`reset --hard`、rebase、force push等で自動的に開始HEADへ戻さない。

また、`merge --abort` failure fallbackの許可条件を1つでも証明できない場合も`reset --hard`を使用しない。

cleanup readbackで開始状態へ戻っていることを証明できない場合はcleanup failureとして保持し、現行Controllerは`MERGE_RECONCILIATION_FAILED`でfail-closedする。

cleanup failureを専用detail `MERGE_RECONCILIATION_CLEANUP_FAILED`として上位へ公開する観測性改善は#52で扱う。

既に発生した可能性のあるcommit/pushを「なかったこと」にしない。

## 6. Host contract

`TrustedWorktree`はreconciliation開始後、成功finalize以外の失敗経路でcleanupを試みる。

- Codex failure → `abort_merge_if_needed()`
- Codex success + finalize failure → `finalize()`内部でcleanup
- `merge --abort` success → readback
- `merge --abort` failure + fallback条件成立 →限定`reset --hard <start_head>` → readback
- `merge --abort` failure + fallback条件不成立 → cleanup failure
- finalize success → cleanupしない

cleanup成功時は開始branch・開始HEAD・`MERGE_HEAD`不在・clean statusをreadbackする。

cleanup結果が不明または失敗の場合でも、Controllerは一般的な`MERGE_RECONCILIATION_FAILED`として停止し、再dispatchしない。専用detailへの分類は#52の責務とする。

## 7. Safety

- cleanupは`PreparedWorktree.start_head`と`PreparedWorktree.branch`を照合する
- fallback前にGitHub PR headをfresh readする
- pre-existing dirty worktreeを自動clean/resetしない
- `git clean`を使用しない
- shared historyへforce pushしない
- rebaseを使用しない
- merge commit作成後の自動hard resetをしない
- live PR headが開始時から動いている場合はhard resetしない
- current branchまたはHEADが開始時から動いている場合はhard resetしない
- Product側へLoop修正用commitを作らない

## 8. Verification

- Codex failure + active MERGE_HEAD → abort + clean start HEAD
- Codex success + unresolved conflict → abort + clean start HEAD
- diff-check/stage/commit failure → abort + clean start HEAD
- successful finalize → abortなし
- abort failure + branch/HEAD/live PR head一致 → hard reset fallback + clean
- abort failure + current HEAD mismatch → hard resetなし
- abort failure + current branch mismatch → hard resetなし
- abort failure + live PR head mismatch → hard resetなし
- abort failure + GitHub fresh read失敗 → hard resetなし
- hard reset failure → cleanup failure
- fallback後untracked残存 → cleanup failure、`git clean`なし
- merge commit作成後push failure → hard resetなし
- pytest / Ruff / strict Mypy / compileall / diff-check PASS
- actual-host再試行で成功またはcleanなfailureとなり、未解決merge状態を残さない
