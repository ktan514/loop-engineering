# AI Liver ゆら V2 自律完遂Mission

version: 5
generation: 1

## Mission

Root #317をMission #450の管理下で完成まで進める。IssueとProject #7は課題・作業の統括を、PostgreSQLは停止後に復元する実行作業状態を所有する。PR、branch、厳密HEAD、CIは対象外部効果の正本とし、DB復元後に必要な対象だけを再照合する。固定PR番号や固定HEADをGoal本文へ埋め込まない。

## Authorityと安全境界

- Project #7をV2の計画Authorityとする。Project #6は変更しない。
- Product Repositoryのcanonical designと選択中Work Issueが設計意図を定義する。
- Loop Engineeringは外部Control Planeとして動作し、Product WorkspaceへLoop専用実装や運用文書を戻さない。
- Codexは実装者であり、独立Reviewerは実装branchを書き換えない。
- review、CI、mergeは厳密HEADへ結び付け、HEAD変更後の古い証拠を再利用しない。
- token、API key、database credential、request header、raw provider failureをRepository、Issue、PR、Checkpoint、通常logへ保存しない。
- force pushとrebaseによる履歴破壊を行わない。

## 再開判定

branch作成、実装、push、merge、新規PR作成の前にDBの安全Checkpointと作業パケットを復元し、Issue / Projectの作業定義、現在trunk、正本設計、作業系列、対象CI/review状態を必要な範囲で再照合する。Issue commentを再開入力にしない。

競合lineage、不明なSHA更新、Authority不一致がある場合は再調整してから進む。会話記憶だけで現在対象を確定しない。

## 制御ループ

観測 → 再調整 → 再開判定 → 選択 → 計画 → 設計 → 実装 → 検証 → レビュー・診断 → 修正・統合 → Checkpoint → 反復・外部待機・介入。

個別Workの完了はMission完了を意味しない。Work完了後はGitHub live dependency graphから次のdependency-ready Workをfresh選択する。

## レビューと修正

- `REQUEST_CHANGES`は通常のfix-loop条件でありMission停止理由にしない。
- review待ちだけをHuman Intervention扱いにしない。
- 同一厳密HEADへ重複review依頼を繰り返さない。
- 独立して進められるWorkがある場合はfresh Resume Gateを通して継続する。
- test、lint、type check、CIの失敗は修正可能なら通常の修正対象とする。

## Operational State

PostgreSQLはLoop EngineeringのRun、transition、作業パケット、Checkpoint、blocker、lease、重複実行防止等の実行作業状態に使用する。停止後の再開はDBを起点にする。Issueの目的、受入条件、依存関係、完了判断をDBの状態で上書きしない。

## Mission状態

有用な独立Workが存在する間はMission stateを`ACTIVE`とする。外部結果だけを待つ場合は`YIELD_EXTERNAL`としてRunを終了できる。`PAUSED_FOR_INTERVENTION`は安全に推測できない利用者判断またはAuthority判断が必要な場合だけ使用する。

Missionを完了できるのは、Root #317とMission #450が定義する全体完成条件をGitHub liveと必要なVerification証拠で満たした場合だけとする。

## 復元とidentity

このファイルをYura用Codex Mission GoalのHost側正本とする。Loop EngineeringはこのUTF-8ファイルから`version`、`generation`、SHA-256を取得し、`CODEX_MISSION_GOAL_VERSION`、`CODEX_MISSION_GOAL_GENERATION`、`CODEX_MISSION_GOAL_SHA256`として実行環境へ注入する。

Product Workspace内の旧`docs/operations/loop_mission_goal.md`を通常Authorityとして要求しない。Goalを失った場合も会話要約から再構成せず、このHost側正本から復元する。
