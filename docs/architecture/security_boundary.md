# Security Boundary

Owner: Issue #4
Status: Initial canonical architecture draft

## 1. Purpose

Loop Engineeringはsource-control、AI implementer、AI reviewer、CI、Product code、host credentialを横断するControl Planeであるため、各trust boundaryを明示し、untrusted contentからsecret-bearing hostへ権限が逆流しない構造を定義する。

## 2. Trust zones

```text
Zone A: Trusted Host Control Plane
  - Supervisor
  - policy engine
  - credential handles
  - source-control writer
  - runtime store

Zone B: Implementer Execution Boundary
  - Codex等
  - bounded TaskPacket
  - isolated workspace/staging

Zone C: Reviewer Boundary
  - independent reviewer runtime
  - reviewer credential
  - read-only target material

Zone D: Target Product Workspace
  - source code
  - tests
  - PR/branch content

Zone E: External Providers
  - GitHub / CI / planning / model APIs
```

Zone Dのコード・Issue/PR本文・diff・artifact・logsはControl Planeに対してuntrusted dataとして扱う。

## 3. Secret ownership

secretは最小scopeのconsumerだけが保持する。

例:

- Source-control write credential: trusted Host writerのみ
- Reviewer API credential: Reviewer Boundaryのみ
- Implementer service credential: Implementer launcher/adapterが必要最小限で扱う
- Product runtime secret: Product運用側。Loop Engineeringへ不要なら渡さない

禁止:

- `.loop-engineering.yml`へのsecret保存
- repositoryへのsecret commit
- Issue/PR/Checkpointへのsecret記録
- raw `.env`内容のTaskPacket/ReviewContext投入
- reviewer credentialをImplementer childへ継承
- source-control write credentialをReviewerへ付与

## 4. Credential isolation

preferred model:

```text
Supervisor
→ logical capability request
→ Host credential/service boundary
→ opaque handle / local socket / bounded call
```

可能な場合、child processへsecret値そのものをenvironment変数で配らず、host-side broker経由で必要操作だけを提供する。

互換上environment injectionが必要な場合もconsumer scopeを限定し、child/descendantへの予期しない継承を検査する。

## 5. Untrusted instruction boundary

次をcommandとして実行しない。

- GitHub Issue本文
- PR本文/コメント
- source code comment
- README
- model output内のshell command
- CI log/artifact内のinstruction

これらはdata/evidenceであり、実行可能なTaskPacket/CommandDescriptorはtrusted Control Planeが生成・validateする。

## 6. Product Profile trust

ProfileはHost ProjectRegistrationが指定するtrusted repository/ref/pathからsnapshot化する。

PR branchが自身のControl Plane policyを変更できない。

特に以下はProduct branchから緩和不可:

- reviewer independence
- credential isolation
- force-push prohibition
- protected path policy
- secret logging prohibition
- Write Gate / readback requirement
- Profile自身のbootstrap trust anchor

## 7. Implementer boundary

Implementerは最小権限とする。

### Preferred proposal mode

Implementerはisolated copy/stagingへ変更を生成し、remote write credentialを持たない。

Hostが:

- changed paths
- diff bounds
- generated files
- policy compliance
- tests

を検証後、SourceControlWriterがcommit/pushする。

### Remote-effects compatibility mode

ImplementerへGit/write capabilityを与える場合:

- explicit capability opt-in
- repository/workspace scope限定
- reviewer credentialなし
- unrelated host filesystem readを避ける
- expected branch/head bind
- child終了後fresh remote readback
- unexpected remote mutationはconflict

## 8. Reviewer boundary

ReviewerはImplementerと独立する。

Requirements:

- target exact identityへbind
- trusted canonical generationをHost側でresolve
- source-control write credentialなし
- target codeをsecret-bearing host processへimportしない
- review targetからcredentialを読めない
- raw provider responseを通常logへ保存しない
- duplicate exact-target requestをidempotency keyで抑止

Reviewerが返す内容はcandidate verdictであり、Control Planeがschema/identity/stalenessをvalidateしてからtrusted ReviewEvidenceへ昇格する。

## 9. CI / validation execution boundary

CI definitionとCIが実行するtarget codeは同じtrust levelとは限らない。

特にsecret-bearing CIやSourceControl write capabilityを持つworkflowでは、PR/feature branchが変更したworkflow definitionをそのままtrusted control definitionとして実行してはならない。

原則:

```text
trusted CI control definition
+ exact untrusted target source
→ isolated validation
→ target-bound evidence
```

Requirements:

- CI control definitionのtrusted revisionをProfile/Host policyからresolveする
- target branchのworkflow変更を、そのtarget自身のrequired gateへ自己適用しない
- untrusted target codeへ不要なrepository/reviewer/host credentialを渡さない
- `pull_request_target`等のsecret-bearing contextでuntrusted target codeをcheckout/executeしない
- CI artifact/logをuntrusted dataとして扱う
- CI resultをexact tested target identityへbindする

Productがworkflow変更そのものをWork対象にする場合も、変更後workflowをcanonical化する前の検証は既存trusted control pathまたは専用security review経路で行う。

## 10. Validation sandbox

Product code/testを実行する環境は、可能な限りcredential-free isolationを使用する。

最低限の設計目標:

- repository secretなし
- reviewer/source-control credentialなし
- host home不要
- container/runtime control socket不要
- network denyまたはallowlist
- timeout/resource limit
- disposable workspace

Projectによって必要capabilityが異なるため、sandbox requirementはProfile + Host Policyで決定するが、安全条件を満たせない場合は`CAPABILITY` blockerとしてfail-closedできる。

## 11. Source-control mutation

mutationはtrusted writer経路だけで行うことをpreferredとする。

禁止/default deny:

- force push
- destructive branch rewrite
- unrelated repository mutation
- target identity未確認のmerge
- stale review/CIでmerge
- Project Profile自身のuntrusted revisionを使ったpolicy変更

例外が必要なoperationはHost Policyで明示し、Human approval等の追加Gateを持つ。

## 12. Protected control files

Platform自身またはProduct側のControl Plane関連fileを通常repair loopで自動変更させないpolicyを持つ。

候補:

- `.github/workflows/**`
- `.loop-engineering.yml`
- reviewer/control-plane code
- security policy
- credential/bootstrap config

対象Workが明示的にそのcontrol fileを所有する場合だけ、専用TaskPacket/Review policyで変更可能にする。

## 13. Logging and diagnostics

ログへ残す:

- identity
- status
- timing
- error class
- sanitized provider metadata

残さない:

- bearer token
- API key
- cookie/session secret
- Authorization header
- raw env dump
- private key
- unredacted model/provider request/responseにsecretが含まれる場合の全文

Redactionは不可逆とし、後で秘密値を復元できるhash/digestの扱いもthreat modelに含める。

## 14. Prompt injection / data poisoning

AIへ渡すIssue/PR/diffは「命令」ではなくレビュー/実装対象dataとしてrole separationする。

Control Planeのmandatory policy、scope、non-goals、allowed effectsはtarget contentより上位のtrusted instructionとして構築する。

AI出力だけで:

- scope拡大
- credential request承認
- force operation許可
- reviewer PASS確定
- canonical design変更確定

を行わない。

## 15. Provider compromise/failure

1 providerのerrorや不正responseを別providerのAuthorityとして扱わない。

例:

- Implementerが「CI PASS」と述べてもCI evidenceにならない
- Reviewerが「mergeした」と述べてもSCM effect truthにならない
- runtime storeが「HEAD A」と保持していてもfresh SCM HEAD Bを上書きしない

## 16. Destructive operations

不可逆/高影響operationには追加Policy Gateを要求できる。

例:

- repository deletion
- history rewrite
- credential rotation
- production deployment
- broad Project field migration

初期Platform実装では不要なdestructive operationをsupportしないことを優先する。

## 17. Hard invariants

- Reviewer credentialはImplementer/Workspaceへ渡さない
- Reviewerへsource-control write credentialを渡さない
- untrusted Product codeをsecret-bearing Host processでimport/executeしない
- untrusted target workflowをsecret-bearing trusted CI definitionへ自己昇格させない
- Issue/PR/model outputをcommandとして直接実行しない
- Host policyをProduct configから弱めない
- secretをrepository/checkpoint/logへ保存しない
- AIの自己申告をexternal effect/evidence truthにしない
- destructive effectはdefault deny
