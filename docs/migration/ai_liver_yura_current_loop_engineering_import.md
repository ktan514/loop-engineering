# AI Liver ゆら 現行Loop Engineering取り込み台帳

管理Issue: #24

## 1. 目的

`ktan514/ai-liver-yura` 内で実運用されているLoop Engineering関連の設計、ソースコード、テスト、運用規則、Issue / PR履歴を、専用リポジトリ `ktan514/loop-engineering` へ取り込む。

過去の中間PRを再構成するのではなく、現在の完成treeとGitHub上の履歴証拠を基準にする。

## 2. 抽出元の固定

- Repository: `ktan514/ai-liver-yura`
- branch: `rebuild/v2-foundation`
- exact HEAD: `1ef8e5de344e854865187e0dd8c465eca650f333`
- tree: `67840cccef01e6abf8e757ecfcb39e017478ece3`
- Loop Engine subtree: `596d53bf444ada1ebdbee64cb4ad572fcf8a70a6`
- Loop Engine test subtree: `b7de202a9e9cce571a9b2f9018aea36ef7c847bf`

このSHAより新しいゆら側変更は、本取り込みとは別の同期世代として扱う。

## 3. Current-state Authority

現在状態の確定には次を使用する。

1. GitHub live branch / Issue / PR / commit
2. Repository内の現行正本文書
3. 最新Checkpoint
4. 会話要約やmemoryは候補発見の補助に限定する

主要Authority:

- Issue #207: GitHub運用・引き継ぎ索引
- Issue #462: Loop Engineering親責務
- Issue #471: 実環境E2E / Pilot統合
- Issue #384: Git履歴・日本語文章・commit運用
- `AGENTS.md`: Repository文章言語・Mission継続規則

## 4. ソースコードの取り込み範囲

`tools/loop_engine/` の現行treeを取り込む。

- CLI / 継続実行
- CI判定
- GitHub Issue / Project連携
- 健全性監視と自己改善
- 実ホスト起動・実行
- 統合制御
- 運用記憶
- PostgreSQL migration
- 事前確認
- 再調整
- レビュー判定
- Runner
- 実行コンソール / 診断ログ
- 作業選択
- Mission監督
- 信頼済みworktree操作
- 書込み前判定

対応する `tests/tools/loop_engine/` も同一世代から取り込む。

取り込み段階ではYura固有値を勝手に削除・抽象化せず、まずsource snapshotとして忠実に保存する。汎用化は専用側で別責務として管理する。

## 5. 設計・運用文書

最低限、次の正本を現行版から取り込む。

- `loop_mission_supervisor.md`
- `loop_self_improvement.md`
- `loop_design_completion_matrix.md`
- `loop_canonical_review_pipeline.md`
- `loop_operational_store.md`
- `loop_autonomous_runner.md`
- `loop_integration_recovery.md`
- `loop_cross_design_audit.md`
- `trusted_host_reviewer_boundary.md`
- `branch_lifecycle_and_commit_hygiene.md`
- `commit_message_language_policy.md`
- `project_v2_management_spec.md`
- `docs/operations/loop_environment_preflight.md`
- `docs/operations/loop_mission_goal.md`
- `AGENTS.md`

## 6. 日本語文章規則

人間が読む文章は日本語を唯一の基本言語とする。

英語の技術概念を文章内で使用する場合は、自然な日本語の意味を先に記し、識別・検索・外部仕様との対応に必要な場合だけ原語を括弧内へ併記する。

機械識別子、status値、command、file path、branch名、SHA、class / function / field名、machine-readable JSON、外部仕様の固定値は変更しない。

翻訳だけを理由に仕様・挙動を変更しない。

## 7. 実運用で発生した重要な修正履歴

現行treeだけでなく、問題が起きた理由も専用側へ残す。

- #479: Loop自身を実製品Pilot対象として再選択する循環を防止
- #480: Codex 0.150系の`exec`起動契約へ対応
- #481: 実行進捗と診断ログを可視化
- #482: `dirty` PRをHuman STOPではなく再調整へ戻す
- #483: consoleを簡潔化しCodex生存通知を追加
- #484: operatorの再実行を不要にする継続実行へ移行
- #485 / #486: Planning Checkpointの`current Work`等のliteral契約を固定
- #489: Codex終了コードだけを成功扱いしない、進捗なし検出、Git操作を信頼済みホストへ限定、`AGENTS.md`日本語規則を導入
- #490〜#492: Loop正本、ログ、docstring、作業指示、索引、事前確認説明を日本語へ統一

## 8. Git操作の安全境界

- 通常作業で基幹branchへ直接commitしない
- 通常mergeはmerge commit方式
- force push / rebaseで共有lineageを破壊しない
- merge直前にexpected HEADを固定する
- Codexはworkspace内のファイル編集と検証を担当する
- branch作成、commit、push、PR、基幹merge、Mission Checkpoint更新は信頼済みホスト側が担当する
- `danger-full-access`を正規方式として使用しない

## 9. Issue / PR履歴の取り込み

専用側に移行台帳を作り、少なくとも次を追跡する。

- 親 / Work / Integration / Managementのsource Issue番号と状態
- 実装・修正PRの番号とmerge状態
- source exact HEAD / merge後trunk世代
- supersede / duplicate / historical-onlyの区別

GitHub上のsource Issueを専用側の新しいcurrent-state Authorityとして偽装しない。source番号は移行元証拠として保持する。

## 10. 完了条件

- source HEADと取り込みmanifestのSHAが一致する
- `tools/loop_engine/` と対応testの現行ファイル一覧が一致する
- 指定した正本文書が取り込まれている
- `AGENTS.md`の日本語文章・Mission継続規則が専用側で有効
- Issue / PR移行台帳から実運用修正の由来を追跡できる
- 新規に追加する人間向け文章が英語のみになっていない
