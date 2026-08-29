# Platform / Product / Runtime Workspace Boundary

Owner: Issue #2
Status: canonical architecture

## 1. Goal

Loop Engineeringの制御責務をProduct Workspaceから分離し、同一Platformから複数Product・複数checkout/worktreeを安全に扱える物理境界を定義する。

## 2. Identity hierarchy

同一repositoryでも複数workspaceが存在し得るため、identityを分離する。

```text
ProjectIdentity
└─ RepositoryIdentity
   └─ WorkspaceIdentity
      └─ RunIdentity
```

### ProjectIdentity

論理的なProduct/開発対象。repository URLそのものではない。

例:

```text
ai-liver-yura
sample-web-app
```

### RepositoryIdentity

Source Control上のrepositoryを一意に表す。

最低限:

- provider kind
- provider account/namespace
- stable repository id
- canonical repository name

表示用URLだけをstable identityとして使用しない。

### WorkspaceIdentity

ローカルcheckout/worktreeを一意に表す。

最低限:

- repository identity
- canonicalized filesystem path
- workspace-local stable id
- current checkout/ref snapshot

同じrepositoryの別worktreeを同一workspaceとみなさない。

### RunIdentity

1回のSupervisor実行を表す。Runは再起動可能であり、Mission/Project/Workのidentityとは別物。

## 3. Product Workspace ownership

Product Workspaceが所有するもの:

- product source
- tests
- product canonical architecture/docs
- product-specific CI definitions
- product-specific policy/config
- product-specific AGENTS等のinstruction

Product Workspaceが所有しないもの:

- global execution lease
- generic runtime blocker
- reviewer credential
- generic host policy
- Platform runtime database
- cross-product scheduling state

## 4. Runtime State placement

Runtime stateはProduct Workspace外に置く。

論理layout:

```text
$LOOP_ENGINEERING_HOME/
├─ projects/
│  └─ <project-key>/
│     ├─ workspaces/
│     │  └─ <workspace-id>/
│     │     ├─ sessions/
│     │     └─ local-state/
│     └─ blockers/
├─ leases/
├─ services/
├─ cache/
└─ logs/
```

実装ではOS conventionsに合わせてroot defaultを決めるが、Product root配下を既定にしない。

## 5. Blocker model

`blocker` はファイルの存在そのものをdomain contractにしない。

Domain model:

```text
Blocker
- blocker_id
- scope
- subject_identity
- kind
- reason_code
- evidence_refs[]
- created_at
- created_by
- resolution_condition
- expiry_policy?
- status
```

### Scope

- `GLOBAL`: Platform全体
- `PROJECT`: 1 Project
- `WORKSPACE`: 1 checkout/worktree
- `WORK`: 1 WorkItem
- `TRANSITION`: 特定transitionのみ

### Kind

- `AUTHORITY`
- `CONFLICT`
- `EXTERNAL`
- `HUMAN`
- `CAPABILITY`
- `SECURITY`
- `LOCAL_STATE`

`Blocked`というPlanning StatusとRuntime Blockerを同一概念にしない。

Planning側のBlockedは外部system上の作業状態表現、Runtime BlockerはSupervisorの安全interlockである。

## 6. RuntimeStore boundary

Filesystemは初期adapter候補であり、Core contractではない。

```text
RuntimeStorePort
- load_blockers(scope)
- put_blocker(blocker)
- resolve_blocker(id, evidence)
- load_session(id)
- save_session(snapshot)
- append_event(event)
```

将来PostgreSQL等へ置換可能とする。

RuntimeStoreの障害時に、GitHub等のlive stateを捏造して継続してはならない。必要なidempotency/recovery evidenceが失われる場合は該当transitionを停止する。

## 7. Workspace discovery / registration

Workspace pathはHost側の設定ファイルへ明示的に登録する。

既定設定:

```text
config/loop-engineering.ini
```

例:

```ini
[project]
key = ai-liver-yura
workspace_path = /absolute/path/to/ai-liver-yura
repository = ktan514/ai-liver-yura
```

CLIから任意のworkspace pathを直接注入することを通常経路にしない。別設定を使用する場合、CLIは`--config <path>`で設定ファイルを選択するだけとする。

ホームディレクトリ全体を走査してProductを暗黙発見しない。

Workspace登録・起動時に最低限検証する:

- path canonicalization
- `git rev-parse --show-toplevel`
- remote repository identity
- writable/read-only capability
- current ref / head
- dirty/untracked state
- nested repository ambiguity
- Product Profile source

設定ファイルのRepositoryIdentityとWorkspaceで観測したRepositoryIdentityが一致しなければfail-closedする。

## 8. Workspace mutation guard

mutation前に以下をfresh確認する。

- expected WorkspaceIdentity
- repository identity
- expected branch/ref
- expected head/base
- dirty/untracked state policy
- active execution lease
- competing process/workspace conflict

期待と異なるworkspaceへmutationしない。

## 9. Multiple products

Supervisorは1 Productのwait-only stateによって他Productを止めない。

```text
Project A: REVIEW_PENDING
Project B: actionable
→ Bを選択可能
```

複数Productを扱う場合も、各ProjectのWorkspace pathとRepositoryIdentityはそれぞれの設定ファイルまたは将来のProject Registration indexから明示的に解決する。

ただしGLOBAL/SECURITY blockerは全Productへ適用できる。

## 10. Hard invariants

- Runtime stateはProduct Workspace外が既定
- secretをProduct profile/runtime checkpointへ保存しない
- 同一repositoryの複数workspaceを区別する
- filesystem pathだけでProjectIdentityを決めない
- Workspace pathはHost設定ファイルへ明示する
- CLIで任意workspaceを直接差し替えることを通常経路にしない
- blockerを単純なstop-file semanticsへ固定しない
- Product内のuntrusted branchからHost Runtime policyを上書きさせない
