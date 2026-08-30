# AI Liver ゆら V2 自律完遂Mission

version: 5
generation: 1

## Mission

Root #317をMission #450の管理下で完成まで進める。現在状態の正本はGitHub liveのIssue、PR、branch、厳密HEAD、CI、Project #7とする。固定PR番号や固定HEADをGoal本文へ埋め込まず、最新Mission Checkpointとfresh readbackから現在対象を解決する。

## Authorityと安全境界

- Project #7をV2の計画Authorityとする。Project #6は変更しない。
- Product Repositoryのcanonical designと選択中Work Issueが設計意図を定義する。
- Loop Engineeringは外部Control Planeとして動作し、Product WorkspaceへLoop専用実装や運用文書を戻さない。
- Codexは実装者であり、独立Reviewerは実装branchを書き換えない。
- review、CI、mergeは厳密HEADへ結び付け、HEAD変更後の古い証拠を再利用しない。
- token、API key、database credential、request header、raw provider failureをRepository、Issue、PR、Checkpoint、通常logへ保存しない。
- force pushとrebaseによる履歴破壊を行わない。

## 再開判定

branch作成、実装、push、merge、新規PR作成の前にGitHub live、最新Mission/Work Checkpoint、現在trunk、canonical design、active lineage、CI/review状態をfresh確認する。

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

PostgreSQLはLoop EngineeringのRun、transition、checkpoint、blocker、lease、重複実行防止等のOperational Stateに使用する。GitHub current-state AuthorityやMission GoalをDBの過去状態で上書きしない。

## Mission状態

有用な独立Workが存在する間はMission stateを`ACTIVE`とする。外部結果だけを待つ場合は`YIELD_EXTERNAL`としてRunを終了できる。`PAUSED_FOR_INTERVENTION`は安全に推測できない利用者判断またはAuthority判断が必要な場合だけ使用する。

Missionを完了できるのは、Root #317とMission #450が定義する全体完成条件をGitHub liveと必要なVerification証拠で満たした場合だけとする。

## 復元とidentity

このファイルをYura用Codex Mission GoalのHost側正本とする。Loop EngineeringはこのUTF-8ファイルから`version`、`generation`、SHA-256を取得し、`CODEX_MISSION_GOAL_VERSION`、`CODEX_MISSION_GOAL_GENERATION`、`CODEX_MISSION_GOAL_SHA256`として実行環境へ注入する。

Product Workspace内の旧`docs/operations/loop_mission_goal.md`を通常Authorityとして要求しない。Goalを失った場合も会話要約から再構成せず、このHost側正本から復元する。
