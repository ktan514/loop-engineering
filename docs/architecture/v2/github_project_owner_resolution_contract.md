# GitHub Project Owner Resolution Contract

Owner Issue: #53
Parent: #9
運用Authority: #26
Related: #48, #49

## 1. 目的

GitHub Projects v2 のownerがUserまたはOrganizationのどちらであっても、Preflightが1回の小さなGraphQL requestでProject read/write capabilityを正しく判定する。

## 2. 問題

`user(login: ...)` と `organization(login: ...)` を同一queryで同時照会すると、存在しないowner種別がGraphQL errorとなり、もう一方に有効なProject dataが返っていても `gh api graphql` 全体がnon-zeroになる可能性がある。

その結果、実際にはProjectへアクセス可能でも `GITHUB_PROJECT_READ` / `GITHUB_PROJECT_WRITE` がfalse BLOCKEDへ誤分類される。

## 3. 正本query

GitHub GraphQLの `repositoryOwner(login:)` を使用する。これはUserまたはOrganizationを1つの入口で解決する。

返却されたownerは `ProjectV2Owner` interfaceとして `projectV2(number:)` を参照する。

```graphql
query {
  repositoryOwner(login: "OWNER") {
    ... on ProjectV2Owner {
      projectV2(number: NUMBER) {
        viewerCanUpdate
      }
    }
  }
}
```

## 4. Capability判定

- `repositoryOwner.projectV2` がobject → `github_project_read = true`
- かつ `viewerCanUpdate == true` → `github_project_write = true`
- ownerなし / Projectなし / auth・permission failure → read/write false
- rate limit → #48のtyped `GITHUB_PROJECT_RATE_LIMITED` を維持

## 5. Request budget

- PreflightのProject capability確認は1 requestのみ
- `gh project view` / `field-list` / `item-list` は能力確認に使用しない
- full Project snapshotはWork planning等、必要な遷移でのみ取得する

## 6. Safety

- Project capability probeはread-only
- Project stateのcache/DB代替を行わない
- GitHub liveをcurrent-state Authorityとして維持する
- error本文を人間向け出力へそのまま露出しない

## 7. Verification

- User owner Project
- Organization owner Project
- owner不在
- Project不在
- viewerCanUpdate=false
- primary/secondary rate limit
- GraphQL requestが1回のみ
- pytest / Ruff / strict Mypy / compileall / diff-check PASS
- actual-host `--preflight` でProject read/write=true
