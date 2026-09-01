# Project Profile Contract

Owner: Issue #2
現行製造Authority: #81 / #82
Status: canonical architecture / V2 manufacturing

## 1. Purpose

Project Profileは、Product固有の開発ルールをLoop Engineering Platformへ注入するための宣言的設定契約である。

Platform CoreへProduct固有Issue番号、Project番号、branch名、CI workflow名、canonical pathをハードコードしない。

新規Productのbootstrapでは「既存Mission Issue番号」を必須入力にしない。Host Product RegistrationとGoal DefinitionからProduct Work graphをLoop Engineering自身が作成できることを前提とする。

## 2. Trust model

Product Profileは「repositoryにあるからtrusted」ではない。

Control PlaneはProfileをtrusted sourceから解決し、snapshot identityを保持する。

```text
ProjectProfileSnapshot
- project_identity
- profile_source_kind
- profile_repository_identity
- trusted_ref
- trusted_commit_sha
- config_path
- config_blob_sha
- target_repository_identity
- schema_version
- normalized_profile_digest
```

feature/PR branch上のProfile変更を、そのPR自身のReview/Write policyへ即時適用しない。

### 2.1 Bootstrap trust anchor

Profile自身に「どのrepository/ref/pathのProfileを信頼するか」を決めさせない。

最初のtrust anchorはHost側のProduct Registrationが所有する。現在のローカル構成では`config/loop-engineering.ini`がHost Product Registrationの入口を兼ねる。

```text
ProductDevelopmentRegistration
- product_key
- workspace_canonical_path
- target_repository_identity
- planning_owner
- planning_project_number
- profile_repository_identity
- profile_source_kind
- trusted_profile_ref
- trusted_profile_path
- goal_definition_source
- registration_revision
- self_improvement_target
```

解決順序:

```text
Host設定 / Product Registration
→ Workspace / target repository identity検証
→ trusted Project Profile resolve
→ Profile schema / policy validation
→ Goal Definition resolve
→ Goal snapshot / revision固定
→ bootstrap Preflight
→ Product Planning
```

`profile_repository_identity` はProfileを保管するrepository、Profile内の `source_control.repository` は開発対象Product repositoryであり、同じとは限らない。

### 2.2 Goal Definition trust anchor

Goal DefinitionもProduct Issue本文の偶然の自然文から推測しない。

Host RegistrationはGoal Definitionのsourceを明示する。初期V2ではHostが管理するstructured fileを標準とする。

例:

```json
{
  "schema_version": 1,
  "goal_id": "sample-text-stats-cli-v1",
  "revision": 1,
  "title": "文字統計CLIを完成させる",
  "goal": "Python製の文字統計CLIを設計・実装・検証する",
  "acceptance_criteria": [
    "ファイル入力に対応する",
    "標準入力に対応する",
    "行数・単語数・文字数を出力する",
    "JSON出力を提供する",
    "異常系と自動試験を備える"
  ]
}
```

読み込み時にschema validationとdigestを行い、`GoalDefinitionSnapshot`として固定する。Goal変更時はrevision/digest差分としてPlanningへ伝え、silentに既存Workへ上書きしない。

Goal本文はPlanning入力であり、runtime current Work / PR / HEAD / next transitionのAuthorityではない。

### 2.3 Target repository binding

Host設定が指定したtarget repositoryはRun開始時にstable provider identityへ解決する。

表示名/URL文字列だけでexecution targetを確定しない。

設定ファイルの`workspace_path`で観測したremote repository identityと、設定されたtarget repositoryが不一致ならconfiguration conflictとしてfail-closedする。

## 3. Configuration precedence

低い設定が高い安全policyを緩和してはならない。

```text
Host Safety Policy        highest
Platform Mandatory Policy
Host Product Registration / Project Profile
Run Invocation Override   lowest, bounded only
```

例:

- Reviewer credential isolationをProductから無効化不可
- force-push禁止をRun optionで許可不可
- Profileでtest commandやcanonical branchを指定可能
- Run Invocation Overrideで秘密情報をCLI引数として渡さない

## 4. Host設定schema

V2自律開発用の標準形:

```ini
[project]
key = sample-text-stats-cli
workspace_path = /absolute/path/to/sample-text-stats-cli
repository = ktan514/sample-text-stats-cli
trunk_branch = main
work_branch_template = feature/work-{issue}
project_owner = ktan514
project_number = 11
profile_source_kind = repository
profile_repository = ktan514/sample-text-stats-cli
trusted_profile_ref = main
trusted_profile_path = .loop-engineering.yml

[goal]
definition_path = /absolute/path/to/sample-text-stats-cli.goal.json

[self_improvement]
enabled = true
repository = ktan514/loop-engineering
project_owner = ktan514
project_number = 9
label = loop-engineering
area = Runtime / Infrastructure
issue_level = Work

authority_refs = #81

[models]
planning_provider = openai
planning_model = default
implementer_provider = codex
implementer_model = default
reviewer_provider = openai
reviewer_model = gpt-5.6-terra
reviewer_api_base = https://api.openai.com/v1
reviewer_api_key_env = OPENAI_API_KEY_REVIEWER

[credentials]
github_token_env = GH_TOKEN

[operational_store]
dsn_env = LOOP_POSTGRES_DSN
required = true
migration_policy = required
```

実際のfield名は#83実装時にschema versionとともに確定し、example config・parser・本書を同じWorkで同期する。

### 4.1 既存Mission設定の扱い

`mission_issue` / `parent_issue` / `integration_work` 等の既存Issue番号を使う構成は、既存Planning graphへ接続する互換モードとして扱える。

ただし新規Product bootstrapではこれらを必須にしない。これらが空であることを「設定不足」として、初期GoalからのPlanningを拒否してはならない。

互換モードでもIssue comment自然文をexecution resume Authorityへ戻さない。

## 5. Allowed profile responsibilities

Profile/Host設定で指定してよいもの:

- provider adapter selection
- target repository identity mapping
- Workspace path
- canonical branch/ref
- planning provider/project mapping
- Goal Definition source
- WorkItem mapping strategy
- CI workflow/check requirements
- planning/implementer/reviewer model
- API endpoint
- 秘密情報を参照する環境変数名
- canonical design discovery strategy
- product-specific status/priority mappings
- verification / Human Verification policy mapping
- product-specific command/test descriptors
- safe path scopes

Product Workのbranch名は`work_branch_template`で宣言する。templateは許可placeholderだけを含む安全なGit refでなければならず、任意shell、空branch、`..`、path traversal、危険なref文字を許可しない。

自己改善Issueの公開先は`[self_improvement]`で指定し、ProductのRepository、Project、label、Authority、Issue levelとは別の設定境界とする。`enabled = false`なら自己改善のGitHub mutationは行わない。

## 6. Forbidden profile responsibilities

Profile/Host設定へ直接置かないもの:

- access token
- API key
- private key
- database credentialの実値
- reviewer credentialの実値
- arbitrary executable shell injected into trusted host without policy validation
- Host policyを弱めるoverride
- live current HEAD / current PRを永続的truthとして固定する値
- Profile自身のbootstrap trust anchorを自己承認する値
- Issue commentをruntime Checkpointとして読む設定

secretは`.env`またはhost credential providerからruntime injectionする。

## 7. Command descriptors

Product固有test/lint等を扱う場合、Profileの文字列をそのままshellへ渡す設計を避ける。

```text
CommandDescriptor
- executable
- argv[]
- working_directory_scope
- environment_allowlist[]
- timeout
- network_policy
- credential_policy
```

shell interpolationを必要とする場合は明示的なhigh-risk capabilityとして別policy gateを持つ。

## 8. Profile resolution

標準解決フロー:

```text
Host Product Registration
→ Workspace / Repository identity resolve
→ trusted Profile source resolve
→ Profile schema validate
→ mandatory policy merge
→ Goal Definition resolve / digest
→ ProductRegistrationSnapshot
→ BOOTSTRAP / PREFLIGHT
```

mutation途中でProfileやGoalが更新された場合、そのRunで黙って新旧を混在させない。fresh observationとreconcileを要求する。

## 9. Missing profile

Product repository内Profileは必須とは限らない。

Host Registrationから同等情報を供給可能で、安全に一意化できる場合はminimal profileでよい。不足情報を推測して危険mutationへ進まない。

一方、Product Work Issueが存在しないことは新規Goal bootstrapでは正常状態である。Planning capabilityが利用可能ならWork graph生成へ進む。

## 10. Product-specific policy adapter

Product固有のResumeルールやProject field mappingはCoreへ直接埋めず、Host設定 / Profile + policy adapterで表現する。

```text
Generic WorkItem.status
        ↑ mapping
Product GitHub Project Status
```

```text
Generic CanonicalDesignRef
        ↑ mapping
Product Repository design document
```

Productごとに異なるIssue/Project運用を採用できるが、V2安全invariantは緩和できない。

## 11. Operational Store policy

V2 productionの停止復元はtransactional durable Operational Storeを必須とする。

現在の標準adapterはPostgreSQLであり、`required = true`をproduction既定とする。

Product ProfileはOperational Storeをcurrent GitHub state Authorityへ昇格させることはできない。DBはselected Work、TaskPacket、Checkpoint、lease、idempotency、effect attempt等のexecution stateを所有する。

## 12. Hard invariants

- 新規Product bootstrapは既存Mission/Work Issue番号を要求しない
- Goal Definition sourceはHost Registrationが所有する
- Goal変更をrevision/digestなしでsilent適用しない
- PR branch自身にControl Plane policyを自己変更させない
- Profile自身にbootstrap trust anchorを自己変更させない
- profile repositoryとtarget Product repositoryを同一概念にしない
- Workspace pathをHost設定へ明示する
- target repositoryはstable provider identityへresolveする
- secretsの実値をProfile/設定へ保存しない
- Host safety policyをProductから緩和不可
- schema versionを明示する
- Profile snapshotをexact source identityへbindする
- unknown/invalid fieldを黙って安全意味へ推測しない
- V2 production停止復元はtransactional durable Operational Storeを必須とする
