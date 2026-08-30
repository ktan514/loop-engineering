# Merge Reconciliation Cleanup Contract

Owner Issue: #50
Parent: #9
運用Authority: #26
Related observability: #52

## 1. 目的

Product Workの既存PRを最新trunkへ通常mergeして競合解消するreconciliationで、Codexまたはtrusted host finalizeが完了しなかった場合に、Product Workspaceへ未解決merge状態を残さない。

同時に、Codexが競合ファイル内容を正しく解消した場合は、その解消結果をtrusted hostがindexへ登録し、merge commitとして正常に確定できることを必須とする。

Loop Engineering自身の不具合修正を理由にProduct Repositoryの共有履歴を破壊・rewriteしない。

## 2. 前提

reconciliation開始前は`TrustedWorktree.prepare()`のclean gateが成立していることを必須とする。

`PreparedWorktree.start_head`はreconciliation開始直前の信頼済みHEAD、`PreparedWorktree.branch`は開始branchであり、cleanup時の照合基準として使用する。

pre-existing user changeがあるworktreeではreconciliationを開始しない。

## 3. 競合解消の正常finalize順序

CodexにはGit管理情報の変更を許可せず、競合ファイルの内容編集と必要な検証だけを担当させる。したがってCodexが内容上の競合を解消しても、trusted hostがstageするまではGit index上のunmerged entryが残ることは正常である。

そのため、`diff-filter=U`や`git ls-files -u`を`git add`より前に最終失敗判定へ使用してはならない。

trusted hostはCodex成功後、次の順序でfinalizeする。

1. `git add -A`でCodexが作業領域へ残した解消結果と付随変更をstageする
2. `git ls-files -u`でunmerged index entryが0件であることを確認する
3. `git diff --cached --check`でstaged差分のconflict marker / whitespace errorを検査する
4. staged差分が存在することを確認する
5. merge commitを作成する
6. exact HEADを取得する
7. 通常pushする
8. PR / Mission Checkpointを更新する

`git add -A`後も`git ls-files -u`が残る、または`git diff --cached --check`が失敗する場合は競合解消失敗としてcleanupへ進む。

Codexが競合解消に付随してtestや契約文書を更新した場合、それらも同じtrusted host finalize対象とする。merge開始前はclean gateが成立しているため、pre-existing user changeとの混同はしない。

## 4. Reversible failure

`git merge --no-commit --no-ff origin/<trunk>`開始後、merge commitがまだ作成されていない段階で次のいずれかが失敗した場合はcleanup対象とする。

- Codex実行
- `git add -A`
- `git ls-files -u` readback / unmerged残存
- `git diff --cached --check`
- staged diff判定
- merge commit作成

`MERGE_HEAD`が存在する場合は、最初にtrusted hostが`git merge --abort`を実行する。

cleanup成功条件:

- `HEAD == PreparedWorktree.start_head`
- current branch == `PreparedWorktree.branch`
- `MERGE_HEAD`が存在しない
- `git status --porcelain`が空

上記をreadbackできた場合だけ通常の`MERGE_RECONCILIATION_FAILED`として安全に終了できる。

## 5. `merge --abort` failure fallback

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

## 6. Effect-bearing / uncertain failure

merge commit作成後は`MERGE_HEAD`が消える。以後の失敗には次が含まれる。

- commit後のHEAD readback失敗
- push失敗または結果不明
- push成功後のPR/Checkpoint更新失敗

この段階ではremoteへeffectが発生した可能性を否定できないため、`reset --hard`、rebase、force push等で自動的に開始HEADへ戻さない。

また、`merge --abort` failure fallbackの許可条件を1つでも証明できない場合も`reset --hard`を使用しない。

cleanup readbackで開始状態へ戻っていることを証明できない場合はcleanup failureとして保持し、現行Controllerは`MERGE_RECONCILIATION_FAILED`でfail-closedする。

cleanup failureを専用detail `MERGE_RECONCILIATION_CLEANUP_FAILED`として上位へ公開する観測性改善は#52で扱う。

既に発生した可能性のあるcommit/pushを「なかったこと」にしない。

## 7. Host contract

`TrustedWorktree`はreconciliation開始後、成功finalize以外の失敗経路でcleanupを試みる。

- Codex success + content resolution → trusted host stage → unmerged gate → cached diff-check → commit/push
- Codex failure → `abort_merge_if_needed()`
- Codex success + finalize failure → `finalize()`内部でcleanup
- `merge --abort` success → readback
- `merge --abort` failure + fallback条件成立 →限定`reset --hard <start_head>` → readback
- `merge --abort` failure + fallback条件不成立 → cleanup failure
- finalize success → cleanupしない

cleanup成功時は開始branch・開始HEAD・`MERGE_HEAD`不在・clean statusをreadbackする。

cleanup結果が不明または失敗の場合でも、Controllerは一般的な`MERGE_RECONCILIATION_FAILED`として停止し、再dispatchしない。専用detailへの分類は#52の責務とする。

## 8. Safety

- Codexには`git add` / commit / pushを許可しない
- stageとunmerged解消確認はtrusted hostだけが実施する
- staged差分はcommit前に`git diff --cached --check`を通す
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

## 9. Verification

- Codexが競合内容を解消しindexがUのまま → trusted host stage後にunmerged 0 → merge commit成功
- stage後もunmerged残存 → cleanup
- staged差分にconflict marker / whitespace error → cleanup
- Codex failure + active MERGE_HEAD → abort + clean start HEAD
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
- actual-host再試行で#384 / PR #441の競合解消がcommit/pushされる、またはcleanなfailureとなる
