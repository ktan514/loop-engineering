# Project Profile Contract

Owner: Issue #2
Status: Initial canonical architecture draft

## 1. Purpose

Project Profileは、Product固有の開発ルールをLoop Engineering Platformへ注入するための宣言的設定契約である。

Platform CoreへProduct固有Issue番号、Project番号、branch名、CI workflow名、canonical pathをハードコードしない。

## 2. Trust model

Product Profileは「repositoryにあるからtrusted」ではない。

Control PlaneはProfileをtrusted sourceから解決し、snapshot identityを保持する。

```text
ProjectProfileSnapshot
- project_identity
- source_kind
- repository_identity
- trusted_ref
- trusted_commit_sha
- config_path
- config_blob_sha
- schema_version
- normalized_profile_digest
```

feature/PR branch上のProfile変更を、そのPR自身のReview/Write policyへ即時適用しない。

### 2.1 Bootstrap trust anchor

Profile自身に「どのrepository/ref/pathのProfileを信頼するか」を決めさせてはならない。そうするとuntrusted Profileが自分自身のtrust anchorを変更できる循環になる。

最初のtrust anchorはHost側のProject Registrationが所有する。

```text
ProjectRegistration
- project_identity
- repository_identity
- profile_source_kind
- trusted_profile_ref
- trusted_profile_path
- registration_revision
```

解決順序:

```text
Host ProjectRegistration
→ trusted repository/ref/path
→ fetch Project Profile
→ schema/policy validation
→ ProjectProfileSnapshot
```

Project Profile内で`canonical_branch`等を指定することはできるが、それはProductのsource/design canonicalを表す値であり、**Profile自身を取得するtrust anchorとは別物**とする。

Profile変更PRが`trusted_profile_ref`や`trusted_profile_path`を変更しようとしても、その変更を自己適用しない。trust anchor変更はHost registrationの明示更新として扱い、必要に応じてHuman/Policy Gateを要求する。

## 3. Configuration precedence

低い設定が高い安全policyを緩和してはならない。

優先順位:

```text
Host Safety Policy        highest
Platform Mandatory Policy
Project Profile
Run Invocation Override   lowest, bounded only
```

例:

- HostがReviewer credential isolationを必須化している場合、Product Profileから無効化不可
- Hostがforce-push禁止の場合、Run optionで許可不可
- Project Profileでtest commandやcanonical branchを指定することは可能

## 4. Proposed schema

初期案:

```yaml
schema_version: 1

project:
  key: ai-liver-yura

source_control:
  adapter: github
  repository: ktan514/ai-liver-yura
  canonical_branch: rebuild/v2-foundation

planning:
  adapter: github-projects
  project_owner: ktan514
  project_number: 7

work:
  mapping: github-issues

ci:
  adapter: github-actions
  required_checks:
    - deterministic-ci

implementer:
  adapter: codex

reviewer:
  adapter: trusted-reviewer
  exact_target_required: true

canonical_design:
  discovery:
    strategy: issue-references

verification:
  human_statuses:
    - Verification
```

これはillustrative schemaであり、#3/#4の設計でtyped contractを確定する。

## 5. Allowed profile responsibilities

Profileで指定してよいもの:

- provider adapter selection
- repository identity mapping
- canonical branch/ref
- planning provider/project mapping
- WorkItem mapping strategy
- CI workflow/check requirements
- canonical design discovery strategy
- product-specific status/priority mappings
- verification policy mapping
- product-specific command/test descriptors
- safe path scopes

## 6. Forbidden profile responsibilities

Profileへ置かないもの:

- access token
- API key
- private key
- database credential
- reviewer credential
- arbitrary executable shell injected into trusted host without policy validation
- Host policyを弱めるoverride
- live current HEAD / current PRを永続的truthとして固定する値
- Profile自身のbootstrap trust anchorを自己承認する値

secretはhost credential providerからruntime injectionする。

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
ProjectIdentity
→ Host ProjectRegistration resolve
→ trusted repository/ref/path resolve
→ fetch profile blob
→ schema validate
→ mandatory policy merge
→ normalized snapshot
→ digest
→ ObservationEpochへbind
```

mutation途中でProfileが更新された場合、そのRunで黙って新旧を混在させない。必要に応じてfresh observationから再評価する。

## 9. Missing profile

Profileは必須とは限らない。

- explicit CLI/host registrationから同等情報を供給可能
- provider defaultsで安全に一意化できる場合はminimal profile可
- 不足情報を推測して危険mutationへ進まない

必須field不足時はtyped capability/configuration blockerとする。

## 10. Product-specific policy adapter

ゆら固有のResumeルールやProject field mappingはCoreへ直接埋めず、Profile + policy adapterで表現する。

例:

```text
Generic WorkItem.status
        ↑ mapping
Yura GitHub Project Status
```

```text
Generic CanonicalDesignRef
        ↑ mapping
Yura Issue body Canonical section
```

このmappingにより、別Productは異なるIssue/Project運用を採用できる。

## 11. Hard invariants

- PR branch自身にControl Plane policyを自己変更させない
- Profile自身にbootstrap trust anchorを自己変更させない
- secretsをProfileへ保存しない
- Host safety policyをProductから緩和不可
- schema versionを明示する
- Profile snapshotをexact source identityへbindする
- unknown/invalid fieldを黙って安全意味へ推測しない
