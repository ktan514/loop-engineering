# Mission Goal Source境界

管理Issue: #27
関連: #2 / #3 / #31 / #44
状態: canonical architecture

## 1. 目的

Loop Engineeringが複数Product Workspaceを扱うとき、Mission Goalの正本をProduct Workspace内の固定pathへ暗黙依存させない。

Mission Goalは実行対象Productの現在Missionを定義する信頼済みHost設定であり、Product source tree、GitHub current-state、PostgreSQL Operational Stateとは別のAuthorityとして扱う。

## 2. Authority分類

Mission Goalは「現在のPR/HEAD」を表すCurrent-state Authorityではない。

役割を次のように分離する。

```text
GitHub live
→ Issue / PR / Project / branch / HEAD / CI / reviewの現在状態

Mission Goal Source
→ Missionの目的、継続条件、安全境界、復元identity

PostgreSQL Operational Store
→ Run / transition / checkpoint / blocker / lease等の運用状態

Product Workspace
→ Product source / tests / canonical product design
```

PostgreSQL Operational Storeの内容だけでMission GoalやGitHub current-stateを上書きしない。

## 3. Bootstrap trust anchor

Mission Goal SourceはHost側から解決し、Productのfeature branch自身に選択させない。

現在の解決順序:

1. Host環境で明示された`LOOP_MISSION_GOAL_PATH`
2. Loop Engineering Platform内のHost registry
3. standalone移行前互換用のProduct内旧path

Host registryはRepository identityから決定論的に解決する。

```text
config/goals/<owner>__<repository>.md
```

例:

```text
config/goals/ktan514__ai-liver-yura.md
```

`LOOP_MISSION_GOAL_PATH`で相対pathを指定した場合はLoop Engineering Platform rootから解決し、Product Workspaceからは解決しない。

将来Project RegistrationをDB等へ拡張する場合も、Goal Sourceのbootstrap trust anchorをuntrusted Product branchだけへ委譲しない。

## 4. Goal identity

Goal Sourceは最低限次を持つ。

```text
version: <value>
generation: <value>
```

起動時にUTF-8 file全体のSHA-256を計算し、次を実行環境へ注入する。

- `CODEX_MISSION_GOAL_VERSION`
- `CODEX_MISSION_GOAL_GENERATION`
- `CODEX_MISSION_GOAL_SHA256`
- `LOOP_MISSION_GOAL_PATH`

CLI、起動器、事前確認は同じGoal Source identityを使用する。別pathのGoalを個別に読み直して混在させない。

## 5. Product Workspaceとの境界

Product Workspaceの `docs/operations/loop_mission_goal.md` を標準配置として要求しない。

standalone移行前の互換性のため、明示Host pathもHost registryも存在しない場合だけ旧path:

```text
<workspace>/docs/operations/loop_mission_goal.md
```

を読む。

新しいProductではHost registryまたは明示Host pathを使用する。Product WorkspaceにLoop専用Goal fileが無いこと自体を異常としない。

## 6. 複数Product

各Projectは別々のGoal Sourceを持てる。

```text
Project A → Goal A
Project B → Goal B
Loop Engineering self → standalone Goal
```

Platform Coreへ特定ProductのMission番号やGoal本文をハードコードしない。Product固有GoalをHost registryへ置くことは、Coreへのハードコードとは区別する。

## 7. PostgreSQLとの境界

PostgreSQLは#44でOperational Stateの永続正本として使用する。

Mission Goal本文をDBだけに保存してbootstrap trust anchorとする設計は採用しない。

理由:
- DB接続確立前にも実行対象と安全方針を識別できる必要がある
- DBの古いOperational StateをGoal Authorityへ昇格させない
- Product RegistrationとGoal Sourceの変更をfile digestで監査可能にする

DBにはGoal identity（version、generation、digest、秘密でないsource ref）をRun evidenceとして記録してよいが、Goal本文のAuthorityはHost側のtrusted sourceに残す。

## 8. Fail-closed

次の場合は `MISSION_GOAL` capabilityをBLOCKEDとする。

- Goal Sourceが存在しない
- UTF-8として読めない
- `version`または`generation`がない
- 起動時注入identityと事前確認時identityが一致しない
- Goal Sourceを一意に解決できない

無効な明示pathが指定された場合、別ProductのGoalや旧Goalへ暗黙fallbackしない。古いGoal、PostgreSQL上の過去Run、会話記憶もfallbackにしない。

## 9. 完了条件

- Goal SourceがProduct Workspace固定pathから分離される
- CLI / 起動identity注入 / Preflightが同じGoal Sourceを使用する
- 旧Product内pathは互換fallbackに限定される
- Yuraの最新trunkにLoop専用Goal fileが無くても起動できる
- standalone self-targetの既存Goalは回帰しない
- PostgreSQL Operational StoreとGoal Authorityが混同されない
