# V2 Codex Implementer proposal mode仕様

管理Issue: #85
親Issue: #81
依存: #84
上位正本: `autonomous_development_completion_contract.md`

## 1. 目的

V2 Supervisorが決定した `DESIGN` / `IMPLEMENT` / `REPAIR` を、Codexへ安全にdispatchし、Product Workspaceへ直接Git管理effectを発生させずに変更提案を生成する。

CodexはProductのファイル編集者であり、branch / commit / push / PR / review request / merge等のGitHub lifecycle ownerではない。それらは#86の信頼済みHostが担当する。

## 2. 実行方式

標準modeはproposal modeとする。

```text
DevelopmentTaskPacket
→ trusted Hostがsource Workspaceをread-only検査
→ exact base SHAから隔離Git worktreeを作成
→ Codexを隔離worktree内で実行
→ local diff / changed paths / checksをreadback
→ ChangeProposalを返す
→ 隔離worktreeを削除
```

Codex自身によるcommit / push / PR作成は許可しない。

## 3. DevelopmentTaskPacket

最低限:

- `packet_identity`
- `work_identity`
- `generation`
- `transition`: `DESIGN | IMPLEMENT | REPAIR`
- `repository_identity`
- `workspace_canonical_path`
- `exact_base_sha`
- `active_lineage_identity?`
- `canonical_design_identities[]`
- `canonical_design_targets[]`
- `scope_paths[]`
- `authority_refs[]`
- `goal_revision`
- `issue_revision`
- `acceptance_checks[]`
- `non_goals[]`
- `safety_constraints[]`

## 4. transition Gate

### DESIGN

- `canonical_design_targets` が1件以上必要。
- 成功proposalでは少なくとも1つのdesign targetが変更される。
- design target外の変更は、TaskPacketのscope内でもDESIGNでは原則拒否する。

### IMPLEMENT

- `canonical_design_identities` が1件以上必要。
- 設計identityが無いIMPLEMENTはdispatch前に拒否する。

### REPAIR

- `active_lineage_identity` が必要。
- `canonical_design_identities` が必要。
- 新lineageを作らず、exact base SHA上の同一lineage修正proposalとして扱う。

## 5. Workspace preflight

source Workspaceについてmutation前に確認する。

- absolute canonical path
- Git repositoryである
- `remote.origin.url` がRegistrationのrepository identityと一致
- source Workspaceがclean
- `exact_base_sha` がlocal Git objectとして解決可能
- exact base SHAが40桁hex

source Workspaceのbranchを切替えない。

## 6. 隔離worktree

trusted Hostがsource repositoryのGit metadataを用いて、Product Workspaceの親配下に一時directoryを作り、`git worktree add --detach <temp> <exact_base_sha>` する。

Codexはこの一時worktreeだけを編集する。

実行終了後は成功・失敗に関係なく `git worktree remove --force` を実行し、fresh readbackで登録が消えていることを確認する。

隔離worktree外の削除は行わない。

## 7. Credential境界

Codex childへ次を渡さない。

- `GH_TOKEN`
- `GITHUB_TOKEN`
- Reviewer API key
- `LOOP_POSTGRES_DSN`
- `LOOP_DATABASE_URL`
- Trusted Reviewer socket/token

Codex自身の認証に必要な非Reviewer credentialはHost設定のallowlistだけを渡す。

## 8. Prompt契約

Codex promptはTaskPacketから構築し、次を固定で含める。

- 対象Work / transition / exact base
- Authority refs
- scope / non-goals / acceptance
- 設計→実装順序
- Git branch / commit / push / PR / merge禁止
- GitHub mutation禁止
- Reviewer credential利用禁止
- Product Workspace外編集禁止
- 人間向け文章は日本語

自由文promptだけを安全境界にせず、実行後readbackでも検証する。

## 9. ChangeProposal

成功結果:

- `packet_identity`
- `work_identity`
- `transition`
- `exact_base_sha`
- `changed_paths[]`
- `patch_sha256`
- `patch_text`
- `design_targets_changed[]`
- `diff_check_passed`

成功条件:

- Codex process success
- HEADがexact baseから変化していない（commit禁止の実証）
- detached状態を維持
- changed pathがscope内
- `git diff --check` PASS
- patchが空でない
- transition固有Gate PASS

## 10. failure / cleanup

Codex failure、timeout、scope逸脱、commit検出、diff-check failure等はproposalを返さない。

隔離worktreeを必ずcleanupし、cleanup自体が証明できない場合は `IMPLEMENTER_CLEANUP_UNPROVEN` としてHuman/Host safety blockerへ昇格する。

source Workspaceは実行前後でHEAD / branch / clean stateが変化していないことを確認する。

## 11. #86との境界

#85はpatchを生成するまで。

#86が:

1. active lineage branchを安全に作成/resolve
2. proposal patchをtrusted Workspaceへ適用
3. commit / push
4. PR create/update
5. fresh GitHub readback

を担当する。

## 12. 完了条件

- DESIGN / IMPLEMENT / REPAIRをtypedに区別できる。
- DESIGN未完了でIMPLEMENTを拒否できる。
- REPAIRはactive lineage必須。
- exact base SHAから隔離worktreeでCodexを実行する。
- CodexがGit commit/push/PRを行っていないことをreadbackできる。
- scope逸脱を拒否できる。
- Reviewer/GitHub/DB credentialをCodexへ渡さない。
- failure時に隔離worktreeを残さない。
- source Workspaceを変更しない。
- tests / exact-head CIがPASSする。
