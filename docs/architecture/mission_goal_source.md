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

Mission Goal SourceのpathはHost側Project Registrationが所有する。

現在のINI設定では `[project] mission_goal_path` を使用する。

```ini
[project]
key = ai-liver-yura
workspace_path = /absolute/path/to/product-workspace
repository = ktan514/ai-liver-yura
mission_goal_path = /absolute/path/to/trusted/mission-goal.md
```

`mission_goal_path`は秘密情報ではない。

相対pathを指定した場合はLoop Engineering Platform rootから解決し、Product Workspaceからは解決しない。これによりfeature branch上のProduct fileがHost Mission Goalを自己変更できない。

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

事前確認は同じconfigured Goal Sourceを読み、version / generation / SHA-256を照合する。

起動器と事前確認が別pathを参照してはならない。

## 5. Product Workspaceとの境界

Product Workspaceの `docs/operations/loop_mission_goal.md` を標準配置として要求しない。

standalone移行前の互換性のため、`mission_goal_path`未設定時だけ旧path:

```text
<workspace>/docs/operations/loop_mission_goal.md
```

を互換fallbackとして読める。

ただし新しいProduct Registrationでは明示的な`mission_goal_path`を使用する。

Product WorkspaceにGoal fileが無いこと自体を異常としない。

## 6. 複数Product

各Projectは別々のGoal Sourceを持てる。

```text
Project A config → Goal A
Project B config → Goal B
Loop Engineering self config → standalone Goal
```

Platform Coreへ特定ProductのMission番号やGoal本文をハードコードしない。

## 7. PostgreSQLとの境界

PostgreSQLは#44でOperational Stateの永続正本として使用する。

Mission Goal本文をDBだけに保存してbootstrap trust anchorとする設計は採用しない。

理由:
- DB接続確立前にも実行対象と安全方針を識別できる必要がある
- DBの古いOperational StateをGoal Authorityへ昇格させない
- Product RegistrationとGoal Sourceの変更をGit revision/file digestで監査可能にする

DBにはGoal identity（pathを直接保存する必要がある場合は秘密でない正規化参照、version、generation、digest）をRun evidenceとして記録してよいが、Goal本文のAuthorityはHost設定で選ばれたtrusted sourceに残す。

## 8. Fail-closed

次の場合は `MISSION_GOAL` capabilityをBLOCKEDとする。

- configured Goal Sourceが存在しない
- UTF-8として読めない
- `version`または`generation`がない
- 起動時注入identityと事前確認時identityが一致しない
- Goal Sourceを一意に解決できない

古いGoal、別ProductのGoal、PostgreSQL上の過去Runを暗黙fallbackにしない。

## 9. 完了条件

- Goal SourceがProduct Workspace固定pathから分離される
- 設定→起動identity注入→Preflightが同じGoal Sourceを使用する
- 旧Product内pathは互換fallbackに限定される
- Yuraの最新trunkにLoop専用Goal fileが無くても起動できる
- standalone self-targetの既存Goalは回帰しない
- PostgreSQL Operational StoreとGoal Authorityが混同されない
