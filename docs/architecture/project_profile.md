# Project Profile Contract

Owner: Issue #2
Status: canonical architecture

## 1. Purpose

Project Profileは、Product固有の開発ルールをLoop Engineering Platformへ注入するための宣言的設定契約である。

Platform CoreへProduct固有Issue番号、Project番号、branch名、CI workflow名、canonical pathをハードコードしない。

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

Profile自身に「どのrepository/ref/pathのProfileを信頼するか」を決めさせてはならない。そうするとuntrusted Profileが自分自身のtrust anchorを変更できる循環になる。

最初のtrust anchorはHost側のProject Registrationが所有する。現在の実装では`config/loop-engineering.ini`が1 Project分のHost Project Registrationを兼ねる。

```text
ProjectRegistration
- project_identity
- workspace_path
- profile_repository_identity
- profile_source_kind
- trusted_profile_ref
- trusted_profile_path
- target_repository_identity
- planning identity
- registration_revision
```

解決順序:

```text
Host設定ファイル / ProjectRegistration
→ Workspace path / trusted profile repository/ref/path
→ Workspaceとtarget repository identityをfresh検証
→ fetch Project Profile
→ schema/policy validation
→ ProjectProfileSnapshot
```

`profile_repository_identity` はProfileを保管するrepository、Profile内の `source_control.repository` は開発対象Product repositoryであり、同じとは限らない。

典型構成ではProduct repository自身にProfileを置けるが、将来central profile repositoryを使用する構成も許容する。

Project Profile内で`canonical_branch`等を指定することはできるが、それはProductのsource/design canonicalを表す値であり、Profile自身を取得するtrust anchorとは別物とする。

Profile変更PRがtrust anchorを変更しようとしても、その変更を自己適用しない。trust anchor変更はHost設定またはProject Registrationの明示更新として扱い、必要に応じてHuman/Policy Gateを要求する。

### 2.2 Target repository binding

Host設定が指定したtarget repositoryはRun開始時にstable provider identityへ解決する。

表示名/URL文字列だけでexecution targetを確定しない。

設定ファイルの`workspace_path`で観測したremote repository identityと、設定されたtarget repositoryが不一致ならconfiguration conflictとしてfail-closedする。

## 3. Configuration precedence

低い設定が高い安全policyを緩和してはならない。

優先順位:

```text
Host Safety Policy        highest
Platform Mandatory Policy
Host設定 / Project Profile
Run Invocation Override   lowest, bounded only
```

例:

- HostがReviewer credential isolationを必須化している場合、Product Profileから無効化不可
- Hostがforce-push禁止の場合、Run optionで許可不可
- Project Profileでtest commandやcanonical branchを指定することは可能
- Run Invocation Overrideで秘密情報をCLI引数として渡さない

## 4. Host設定schema

現在のHost設定はINI形式を使用する。

```ini
[project]
key = ai-liver-yura
workspace_path = /absolute/path/to/ai-liver-yura
repository = ktan514/ai-liver-yura
trunk_branch = rebuild/v2-foundation
project_owner = ktan514
project_number = 7
mission_issue = 450

[models]
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
```

Workspace path、Repository、Planning、モデル名、API endpoint等は非秘密設定として保持する。

API key、token、database credential等は値を直接保持せず、環境変数名だけを設定へ記録する。実値はGit管理外の`.env`またはHost secret sourceから供給する。

## 5. Allowed profile responsibilities

Profile/Host設定で指定してよいもの:

- provider adapter selection
- target repository identity mapping
- Workspace path
- canonical branch/ref
- planning provider/project mapping
- WorkItem mapping strategy
- CI workflow/check requirements
- implementer/reviewer model
- API endpoint
- 秘密情報を参照する環境変数名
- canonical design discovery strategy
- product-specific status/priority mappings
- verification policy mapping
- product-specific command/test descriptors
- safe path scopes

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

secretは`.env`またはhost credential providerからruntime injectionする。

## 7. Command descriptors

Product固有test/lint等を扱う場合、Profileの文字列をそのままshellへ渡す設計を避ける。

推奨model:

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
Host設定ファイル
→ ProjectIdentity / Workspace path / RepositoryIdentity resolve
→ Workspace Git root / remote / ref / head / dirty state検証
→ trusted profile source resolve
→ fetch profile blob
→ schema validate
→ mandatory policy merge
→ normalized snapshot
→ digest
→ ObservationEpochへbind
```

mutation途中でProfileが更新された場合、そのRunで黙って新旧を混在させない。必要に応じてfresh observationから再評価する。

## 9. Missing profile

Product repository内Profileは必須とは限らない。

Host設定ファイルから同等情報を供給可能で、安全に一意化できる場合はminimal profileでよい。不足情報を推測して危険mutationへ進まない。

必須field不足時はtyped capability/configuration blockerとする。

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
Product Issue body Canonical section
```

このmappingにより、別Productは異なるIssue/Project運用を採用できる。

## 11. Hard invariants

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
