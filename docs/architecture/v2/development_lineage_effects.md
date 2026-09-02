# V2 GitHub開発lineage effect仕様

管理Issue: #86
親Issue: #81
依存: #84, #85
上位正本: `autonomous_development_completion_contract.md`

## 1. 目的

#85の`ChangeProposal`を信頼済みHostがProductの1つのactive implementation lineageへ反映し、branch / push / PR等のGitHub開発effectをV2のDB intent・readback契約で冪等に管理する。

不変条件は `1 Work = 1 active implementation lineage` とする。

## 2. 責務分離

```text
ChangeProposal
→ TrustedProposalMaterializer
   → exact baseから隔離worktree
   → raw patch identity検証
   → patch apply/check
   → trusted commit
   → candidate commit SHA
→ GitHubDevelopmentLineageEffects
   → DB intent
   → remote target readback
   → Write Gate相当precondition
   → 最大1回effect
   → fresh readback
   → DB outcome
```

Codexはこの工程へGitHub credentialを持ち込まない。

## 3. Lineage identity

Workごとに次を固定する。

- `work_identity`
- `issue_number`
- `branch_name`
- `base_branch`
- `active_pr_number?`
- `current_head_sha`

branch名はProduct Registration / Project Profileの安全なtemplateから生成する。

同一Workに別branchまたは別open PRが複数見つかった場合は`COMPETING_LINEAGE`としてfail-closedし、historical PRを推測採用しない。

## 4. Proposal materialization

入力:

- Product workspace canonical path
- repository identity
- ChangeProposal
- expected lineage branch
- commit message

手順:

1. source WorkspaceがGit root / repository identity / cleanであることを確認。
2. proposalの`patch_sha256`をraw `patch_text`から再計算。
3. `exact_base_sha`がlocal commit objectとして存在することを確認。
4. exact baseからdetached隔離worktreeを作る。
5. raw patchをHost管理の一時fileへ書く。
6. `git apply --check` → `git apply --index`。
7. staged pathがproposal.changed_pathsと完全一致することを確認。
8. `git diff --cached --check`。
9. trusted Hostがcommitする。
10. candidate commit SHAを取得。
11. 隔離worktreeと一時patchをcleanupし、source Workspace不変を確認。

candidate commitはまだremote effect truthではない。

## 5. Remote branch effect

remote branchのfresh stateで分類する。

### branch不存在

- effect kind: `BRANCH_CREATE`
- expected before: `head=<absent>`
- expected after: `head=<candidate_sha>`
- command: normal `git push origin <candidate_sha>:refs/heads/<branch>`

### branch存在・head == proposal.exact_base_sha

- effect kind: `PUSH`
- expected before: `head=<exact_base_sha>`
- expected after: `head=<candidate_sha>`

### branch存在・headがそれ以外

`STALE_TARGET`。pushしない。

force pushは禁止する。

## 6. PR create effect

branch反映後、open PRを`head branch + base branch`でfresh検索する。

- 0件: `PR_CREATE` intentをDB確定後にdraft PRを1回だけ作成する。
- 1件: head/base/Work identityが一致すれば既存active PRへ収束する。
- 2件以上: `COMPETING_LINEAGE`。

PR create commandが失敗・timeoutしても即再送せず、同じhead/baseをreadbackする。存在を証明できればCONFIRMED、不存在を証明できればNO_EFFECT、判定不能はUNCERTAIN。

historical closed/merged PRはactive PRへ昇格しない。

## 7. READY / REVIEW_REQUEST / MERGE / ISSUE_UPDATE

既存V2 effect契約を維持し、開発lineage identityを必須preconditionへ加える。

- `READY`: exact PR/headを確認してdraft→ready。
- `REVIEW_REQUEST`: exact HEADごとのReviewRequestKeyで重複抑止。詳細判定は#87。
- `MERGE`: expected exact HEAD固定・merge commit方式。
- `ISSUE_UPDATE`: merge readback後のWork完了など、許可fieldだけ。

## 8. Effect state

既存`loop_effect_attempts`を使用する。

各effectは最低限:

- `idempotency_key`
- `work_identity`
- `packet_generation`
- `kind`
- `target_identity`
- `status`
- `expected_preconditions`
- `expected_effect`

statusは既存V2契約の`INTENT_RECORDED / CONFIRMED / NO_EFFECT / UNCERTAIN`を維持する。

## 9. Readback

remote branch:

- ref不存在 → `<absent>`
- ref存在 → exact SHA

PR:

- active検索ではopenのみを対象
- PR number / headRefName / headRefOid / baseRefName / draft / stateをfresh取得
- target限定readbackで対象を証明する

command exit codeだけでCONFIRMEDにしない。

## 10. Crash/restart

各remote effectは必ずintentをDBへ先に記録する。

```text
INTENT_RECORDED
→ crash possible
→ restart
→ target readback
→ already afterならCONFIRMED
→ definitely beforeならeffect候補
→ 判定不能ならUNCERTAIN（再送禁止）
```

local materializationはremote effectではなく、失敗時に隔離worktreeを破棄して再生成可能とする。remote branchへ反映された後はDB/readbackを正とする。

## 11. Safety

- main/trunkへ直接pushしない。
- force pushしない。
- stale branch headへpushしない。
- open competing PRを作らない。
- historical/HOLD PRをcurrentとして推測しない。
- proposal exact baseとremote current headを一致確認する。
- source Workspaceを直接編集・branch切替しない。
- create系effectをcommand失敗だけで再送しない。

## 12. 完了条件

- ChangeProposalをtrusted commitへmaterializeできる。
- raw patch identity mismatchを拒否できる。
- branch不存在をBRANCH_CREATEとして冪等に処理できる。
- existing exact base branchをPUSHとして更新できる。
- stale remote headを拒否できる。
- PR_CREATEをreadback付きで冪等に処理できる。
- competing active lineageを拒否できる。
- crash/restart後にcreate/pushを二重送信しない契約がテストされる。
- trunk direct push / force pushを生成しない。
- exact-head CIがPASSする。
