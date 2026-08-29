# ブランチのライフサイクルとコミット履歴品質

状態: 正本追補案
適用日: 2026-08-13
親Issue: #317
管理Issue: #384
関連正本: `docs/architecture/v2/project_v2_management_spec.md`

## 1. 目的

Git GraphとGitHubの状態だけから、ブランチが作業中、一時停止、採用中止、統合済み、検証専用のどれかを推測なしで判断できる履歴を維持する。

また、仮ファイル、空の起動用コミット、一時的な`NOOP` / `nonexistent`ファイルなど、履歴を動かすことだけを目的としたコミットが共有履歴へ入ることを防ぐ。

## 2. ブランチ状態

基幹以外の各ブランチは、紐づくIssueとPRから一意に判断できる運用状態を1つだけ持つ。

### ACTIVE

- 紐づくIssueは`In progress`または`Review`。
- 能動作業系列にOpen PRが1つ存在する。
- 設計・実装が未完了ならPRはDraft、レビューを意図的に依頼するときだけReadyにする。
- Resume Checkpointへbranch、base SHA、head SHA、現在状態、次作業を記録する。

### BLOCKED

- 紐づくIssueへ停止要因を記録する。
- 明確な終了理由がなければPRはOpen Draftのまま保持する。
- 停止要因が解消するまで実装コミットを追加しない。
- Resume Checkpointへ厳密な停止条件を記録する。

### MERGED

- PRが統合済みである。
- 通常の統合方式は`merge`とし、作業ブランチの系譜を対象基幹へ可視的に合流させる。
- 統合先の履歴に作業ブランチ最終HEADが祖先として含まれる。
- 最終source HEADとmerge commit SHAを記録する。
- 統合後のsource branchへ追加コミットしない。
- 統合確認後にbranch refを削除する。

### ABANDONED

- PRを未統合で終了する。
- Issue/PRへ、作業系列を不採用・置換・取消とした理由を記録する。
- 有用な設計・試験・失敗知見をIssueまたは正本履歴へ回収してから整理する。
- disposition記録後にbranch refを削除する。

### TEST_ONLY

- 一時的なCI・検証だけに使用する。
- PRへ「統合しない」ことを明示する。
- 結果と最終HEAD SHAを所有Issue/PRへ転記する。
- PRを未統合で終了し、証拠取得後すぐbranch refを削除する。

## 3. 統合方針

通常PRはGitHubの`merge`方式を使用する。

通常は次を使用しない。

- squash merge
- rebase merge

理由は、ブランチの祖先関係をGit Graph上で可視に保つためである。完了した作業ブランチは対象基幹へ実際に合流していることが履歴から確認できなければならない。

統合前に次を行う。

1. 現在PR HEAD SHAを再取得する。
2. 現在の統合先branch SHAを再取得する。
3. 必須review/test gateを確認する。
4. 期待するHEAD SHAを固定して統合する。
5. 返されたmerge commitを確認する。
6. source ancestryが対象基幹に存在することを確認する。
7. 完了Checkpointを記録する。
8. source branch refを削除する。

作業ブランチのローカル履歴を整理する必要がある場合は、squash mergeで隠すのではなくreview/merge前に整理する。他の能動作業系列が依存する共有branchを、明示的な再調整なしに書き換えない。

## 4. 統合後の追加修正

統合済みbranchは完了済みとして扱い、再利用しない。

追加修正が必要な場合は次のようにする。

- 統合済みsource branchへコミットを追加しない。
- 最新の統合先HEADから開始する。
- 新しい`fix/*`、`feature/*`、`docs/*`、`management/*`等の目的別branchを作る。
- 同じ責務の追補である場合だけ同一Issueへ紐付ける。
- 別責務なら新しいWork/Bug/Management Issueを作る。

これにより1本のbranchが「統合済み」と「まだ作業中」を同時に表す状態を防ぐ。

## 5. 履歴だけを動かすコミットの禁止

共有履歴へ、主目的が自動化の起動、branch pointerの移動、GitHub反応確認だけのコミットを入れない。

禁止例:

- `NOOP`、`nonexistent`、`.trigger`、`dummy`等の仮ファイルを作る。
- 実変更がないのに件名だけ`x`、`noop`、`trigger`等にする。
- CI/review再起動だけのため空コミットを作る。
- 活動を発生させるためだけに一時ファイルを追加し直後のコミットで削除する。
- 新SHAを作るためだけの空白・コメント変更を行う。

自動化の再起動は履歴を汚さない正式な仕組みを使う。

- 意図したreview起動条件である場合のDraft → Ready遷移
- 対応している`workflow_dispatch`
- GitHub Actionsの再実行
- 信頼済み制御系の専用操作
- 対応している安全なレビューワーcommand/comment

## 6. コミット前・送信前の確認契約

共有branchを変更する前に次を確認する。

### コミット前

- 現在branchが意図した作業branchである。
- `main`、`develop`、`rebuild/v2-foundation`等の保護基幹ではない。
- 紐づくIssue/PRが意図した能動作業系列である。
- stage済みpathが現在タスクの想定範囲である。
- 仮・sentinel fileがstageされていない。
- 差分に実際の設計・コード・試験・運用変更がある。

### コミット後・push前

- 新しいcommit messageを確認する。
- changed pathと統計を確認する。
- parent SHAを確認する。
- 意図した変更だけが入っていることを確認する。
- `NOOP`、`nonexistent`、仮ファイル、生成事故ファイル、認証情報、一時成果物がないことを確認する。
- 1つでも失敗した場合はpushしない。

### push後

- remote branch HEADを再取得する。
- remote HEADが意図したcommitと一致することを確認する。
- 紐づくPRが同じ厳密SHAを指すことを確認する。
- 重要変更なら作業Checkpointへ新HEADを記録する。

## 7. 自動コミット履歴品質検査

PRで導入されるコミットを検査し、明らかな履歴事故を安全側で拒否する機械検査を持つ。

最低限検出するもの:

- 自動化起動だけを目的とする空コミット。
- 変更全体が既知の仮・sentinel pathだけで構成されるコミット。
- root直下`NOOP`、`nonexistent`等の既知仮path導入。
- 同じPR作業系列内での仮ファイル追加→削除の組。
- この方針で明示禁止した起動専用pattern。

1行だけの正当な小変更を機械的に拒否しない。任意の最低差分サイズではなく、path/contentと変更意図の決定論的signalで判断する。

検査は違反commit SHAと理由を報告し、共有履歴を自動で書き換えない。

## 8. 事故コミットの復旧

push前に発見した場合は、共有前のローカル履歴を修復し、事故を残すためだけの打消しコミットを作らない。

未統合branchへpush済みで、他の能動作業系列が依存していない場合:

- branch作業を停止する。
- 必要なら事故SHAをIssue/PRへ監査証拠として記録する。
- 追加・削除のcleanup commitを積み重ねるより、正しい信頼済みbaseからclean branchを作り直すことを優先する。
- 置換されたPRを終了する。
- 復旧後に汚れたbranchを削除する。

すでに保護・共有基幹へ入った場合:

- 保護履歴を黙って書き換えない。
- 影響に応じて明示的な修正コミット、または別途承認した履歴再構成計画を使用する。
- 事故と是正内容を記録する。

## 9. 既存branchの整理

この方針より前のbranchは次の手順で整理する。

1. `ACTIVE / BLOCKED / MERGED / ABANDONED / TEST_ONLY`へ分類する。
2. 正本証拠をIssue/PR/正本文書へ保持する。
3. Git Graphを合流させるためだけに事故コミットを統合しない。
4. 過去のsquash mergeを接続して見せるためだけに公開済み基幹履歴を書き換えない。
5. 確認後に不要branch refを削除する。
6. 基幹branchと明示的所有者を持つ現在の作業中・停止中branchだけを残す。

独立AIレビュー構築中に確認された既知の事故コミット:

- `40dcdefd1dc5378f35780a49a405547988eccb8b`（`x`、`nonexistent`追加）
- `f08e3bc6066210865eb4c9dfa3330ba02d44f65f`（上記事故ファイル削除）
- `a703f8be7bf74c189d4302e1327cfe62ea65ec92`（`noop`、`NOOP`追加）

これらは有効な製品・設計履歴ではなく、保存のために基幹へ伝播させない。

## 10. #384完了条件

- この方針を通常のmerge commitで統合する。
- 自動コミット履歴品質検査を実装・検証する。
- 現在branchを分類する。
- ツールで可能な範囲で統合済み・検証用・採用中止branch refを整理する。
- 作業中・停止中branchをlive Issue/PRへ明示的に結び付ける。
- 今後の通常統合はmerge commitを既定にする。
- 統合済みsource branchを再利用しない。
