# local-llm-coder統合設計

Owner: Issue #98  
Parent: Issue #81  
関連: #85 / #87 / #88  
Status: canonical design candidate

## 1. 目的

Loop Engineering V2へ`local-llm-coder`をローカルLLM実行基盤として統合し、Loop Engineeringを唯一の上位Orchestratorとして維持する。

統合後は、人間がImplementer / Self Reviewer / Fixerを手動で切り替えることを通常経路にしない。Loop EngineeringがWork、exact target、状態遷移、反復、外部レビュー段階、停止復元を所有し、`local-llm-coder`は要求された1回のWorker実行を行って機械可読な結果を返す。

標準品質経路は次とする。

```text
DESIGN / IMPLEMENT
→ deterministic verification
→ LOCAL_REVIEW
   ├─ LOCAL_PASS → EXTERNAL_REVIEW_L1
   ├─ REQUEST_CHANGES → LOCAL_REPAIR
   ├─ INCOMPLETE → same stage resume
   └─ BLOCKED / FAILED → typed recovery
LOCAL_REPAIR
→ deterministic verification
→ fresh LOCAL_REVIEW
→ ...

LOCAL_PASS
→ EXTERNAL_REVIEW_L1
→ EXTERNAL_REVIEW_L2
→ ...
→ EXTERNAL_REVIEW_LN
→ HUMAN_VERIFY?
→ INTEGRATE
```

外部レビューで修正が必要になった場合は同一lineageのLocal Repairへ戻す。修正でexact targetが変わった場合、旧HEADへ結び付いたLocal/External Review PASSは新HEADへ昇格させず、Local Reviewから取り直す。

## 2. 責務境界

### 2.1 Loop Engineeringが所有する責務

Loop Engineeringは次を所有する。

- Goal / Work / dependency / acceptance criteria
- current Work選択
- active implementation lineage
- DESIGN / IMPLEMENT / REPAIR / VERIFY / LOCAL_REVIEW / EXTERNAL_REVIEW / HUMAN_VERIFY / INTEGRATEの遷移
- TaskPacketとexact target identity
- Local Quality Loopの反復条件
- `LOCAL_PASS`の成立判定
- External Review Level 1..Nの順序と要求policy
- review findingから同一lineage REPAIRへの復帰
- Completion Contractの充足判定
- PostgreSQLによるdurable execution state / Checkpoint / resume
- exact-head evidenceの有効性判定
- Human Gate / Integration Gate
- provider/model/profileの工程割当

Loop Engineering CoreはOpenCode、Ollama、特定モデル名、OpenAIのSDK型へ依存しない。

### 2.2 local-llm-coderが所有する責務

`local-llm-coder`は次を所有する。

- OpenCode managed runtimeの準備
- ローカルLLM providerへの接続
- 実行profileからprovider / model / endpointを解決すること
- Implementer / Self Reviewer / Fixer Workerの実行
- Reviewerをfresh sessionとして実行すること
- Worker単位のruntime isolation
- Worker tool outputをCompletion Contractへ正規化すること
- Worker processのtimeout / interruption / tool failureを型付き結果に変換すること
- Product WorkspaceとControl Planeの既存境界を維持すること

`local-llm-coder`はWork選択、Mission継続、外部Review Level順序、merge可否、Goal完了を判断しない。

ただし、Repository access capabilityとしては`loop-engineering`と`local-llm-coder`を一方向Read-only関係にはしない。正式なTaskPacket / Work scopeで対象Repositoryが指定された場合、両Repositoryはいずれも開発対象としてRead / Write可能である。

### 2.3 Product Workspaceが所有するもの

Product WorkspaceはProduct code、Product canonical、Product固有設定・試験・実行結果を所有する。

Loop Engineeringと`local-llm-coder`はProduct固有の意味Authorityを複製しない。

### 2.4 Repository間のRead / Write

`loop-engineering`と`local-llm-coder`は、どちらも信頼済み開発RepositoryとしてRead / Write可能とする。

意味するところは次の通り。

- Loop Engineeringは、`local-llm-coder`を現在Workの対象Repositoryとして選択した場合、そのRepositoryの設計・code・test・branch・PRを通常のWrite Gate / lineage規則に従って変更できる。
- `local-llm-coder`は、Loop Engineeringから渡されたTaskPacketで`loop-engineering`がActive Product / target Workspaceとして指定された場合、Implementer / FixerとしてそのRepositoryをRead / Writeできる。
- Self Reviewerは対象Repositoryがどちらであってもread-only roleを維持する。
- どちらのRepositoryも、現在TaskPacketのtarget外である相手Repositoryを暗黙に変更しない。
- Repository間Writeはbranch / PR / exact target / readback / Write Gateを迂回しない。
- 「相互にRead / Write可能」と「相互Repositoryを常時同時に変更する」は別である。1つのtransitionではtarget Repositoryとscopeを明示する。

したがって境界は「Repository AはRepository Bへ書けない」ではなく、**現在のWork / TaskPacketで選択されたtarget Repositoryだけを安全にRead / Writeする**ことである。

## 3. local-llm-coder Backend契約

Loop Engineeringは`LocalLlmCoderAdapter`相当のAdapterから`local-llm-coder`を呼び出す。Core Portはprovider固有名称へ依存しない。

概念契約:

```text
LocalWorkerRequest
- request_identity
- task_packet_identity
- role
- transition
- repository_identity
- workspace_canonical_path
- exact_base_sha
- exact_target_sha?
- active_lineage_identity
- authority_refs[]
- scope_paths[]
- canonical_refs[]
- acceptance_checks[]
- non_goals[]
- safety_constraints[]
- model_profile

LocalWorkerResult
- request_identity
- task_packet_identity
- role
- exact_target_identity
- status
- completion
- findings[]
- changed_paths[]
- verification_evidence[]
- diagnostics[]
- artifact_refs[]
```

`role`:

- `IMPLEMENTER`
- `SELF_REVIEWER`
- `FIXER`

`status`:

- `PASS`
- `FINDINGS`
- `INCOMPLETE`
- `BLOCKED`
- `FAILED`

process終了コード0だけで`PASS`へ昇格しない。

## 4. Completion Contract

Workerは自由文を返しただけでは完了にならない。roleごとに必須fieldを満たした構造化結果が必要である。

### 4.1 共通完了条件

- request identity一致
- TaskPacket identity一致
- exact target echo一致
- role一致
- terminal statusが存在
- 未処理の「次に確認する」actionが残っていない
- diagnosticsと未検証事項が明示されている
- secret値を含まない

### 4.2 Self Reviewer完了条件

最低限:

```text
completion.review_scope_checked = true
completion.canonical_checked = true
completion.target_identity_checked = true
completion.blocking_findings_finalized = true
completion.non_blocking_findings_finalized = true
completion.unverified_finalized = true
completion.final_verdict_present = true
```

確認可能な事項を残したまま`INCOMPLETE`をterminal successとして扱わない。

Self Reviewerがcontext上限、tool failure、process interruption等で終了した場合、Loop Engineeringは`LOCAL_REVIEW`完了を記録せず、同じReviewRequest identityを用いて安全に継続する。provider session identityは実装詳細であり、再開に必須としない。

### 4.3 終了してよい未確認

次のように現在のcapabilityでは解消できない事由だけをblocked/unverifiedとして終了可能とする。

- required credential不足
- provider unavailable
- authority data不存在
- destructive actionにHuman承認が必要
- competing Authorityを自動決定不能
- Workspace/target identity不整合

単に「まだ読んでいない」「次に確認する必要がある」は終了理由ではない。

## 5. Local Quality Loop

Local Quality LoopはLoop Engineeringの状態機械として所有する。

```text
IMPLEMENT
→ READBACK
→ VERIFY_LOCAL
→ LOCAL_REVIEW
   ├─ PASS
   │   → LOCAL_PASS
   ├─ FINDINGS
   │   → VALIDATE_LOCAL_FINDINGS
   │   → REPAIR
   │   → READBACK
   │   → VERIFY_LOCAL
   │   → fresh LOCAL_REVIEW
   ├─ INCOMPLETE
   │   → RESUME_LOCAL_REVIEW
   └─ BLOCKED / FAILED
       → recovery policy
```

Local findingは無条件にFixerへ渡さない。finding schema、対象path、exact target、根拠、scopeをHost側で検証してからREPAIRへ渡す。

`LOCAL_PASS`は次をすべて満たしたexact targetにだけ成立する。

- required local deterministic verification PASS
- fresh Local Self Review PASS
- blocking finding 0
- policy上許容しない未検証事項 0
- Completion Contract充足
- target identityがreview前後で不変

## 6. External Review Level Pipeline

External Reviewは`LOCAL_PASS`後に開始する。

```text
LOCAL_PASS
→ EXTERNAL_REVIEW_L1
→ EXTERNAL_REVIEW_L2
→ ...
→ EXTERNAL_REVIEW_LN
→ EXTERNAL_PASS
```

各LevelはReviewerPortの別policy instanceとして扱い、provider/modelをCoreへ埋め込まない。

各Level設定は最低限次を持つ。

```text
ReviewLevelPolicy
- level
- provider
- model
- api_base
- credential_reference
- required
- timeout
- context_policy
- escalation_policy
```

review resultは既存`ReviewResult`契約へ正規化する。

- `PASS`
- `REQUEST_CHANGES`
- `ESCALATE`
- `NOT_RUN`

### 6.1 REQUEST_CHANGES

外部review findingがvalidなら:

```text
EXTERNAL_REVIEW_Ln
→ VALIDATE_EXTERNAL_FINDINGS
→ REPAIR
→ VERIFY_LOCAL
→ fresh LOCAL_REVIEW
→ LOCAL_PASS
→ EXTERNAL_REVIEW_L1
```

修正でexact targetが変わったため、旧HEADに対するL1..LnのPASSは全てstaleである。新HEADはLevel 1から取り直す。

target identityが変化せず、provider resultの再取得やinvalid finding除外だけを行った場合は、同じLevelのrequest identityをreconcileできる。

### 6.2 ESCALATE

`ESCALATE`はpolicyで次LevelまたはHumanへ移す。Implementer自身の自己承認へfallbackしない。

### 6.3 NOT_RUN

credential不足、provider障害等による`NOT_RUN`をPASSへ読み替えない。required Levelなら外部待機またはintervention分類を行う。

## 7. モデル設定Authority

モデル名をLoop Engineering Coreまたは`local-llm-coder`の固定OpenCode templateへ直接埋め込まない。

Authorityを2層へ分離する。

### 7.1 Loop Engineering: 工程からBackend/Profileへの割当

例:

```ini
[models]
implementer_provider = local-llm-coder
implementer_profile = local-main
local_reviewer_provider = local-llm-coder
local_reviewer_profile = local-main

[review.level.1]
provider = openai
model = <level-1-model>
required = true

[review.level.2]
provider = openai
model = <level-2-model>
required = true
```

Loop Engineeringは`local-main`の実Ollama model名を知らない。

### 7.2 local-llm-coder: Profileから実行環境への解決

環境固有のlocal profileをGit管理外設定から解決する。

概念例:

```json
{
  "profiles": {
    "local-main": {
      "provider": "ollama",
      "model": "qwen3.5:35b-a3b-coding-nvfp4",
      "base_url": "http://127.0.0.1:11434/v1",
      "reasoning": true
    }
  }
}
```

Repositoryにはexample/schemaだけを保存し、実環境profileはlocal設定とする。

`config/opencode.json`は固定modelの正本ではなくruntime templateとし、selected profileからmanaged runtime `opencode.json`を生成する。

provider/model/endpoint変更だけでLoop Engineering Core state machineを変更しない。

## 8. 非対話実行境界

Loop Engineeringからの通常呼出しではTUI操作を前提にしない。

`local-llm-coder`は次の2経路を分離する。

- Human interactive: 現行managed TUI
- Automation backend: typed requestを受け、Workerを実行し、structured resultを返して終了

Automation backendは標準出力の自然文解析をControl Plane契約にしない。結果はJSON等のversioned schemaで返す。

OpenCode固有command lineはAdapter内部へ閉じ込める。

## 9. 状態永続化とResume

Local worker process/sessionをdurable Authorityにしない。

Loop Engineering PostgreSQLに最低限次を保存する。

- current quality stage
- local worker request identity
- exact target identity
- completion state
- validated finding identities
- required external review levels
- current external review level
- ReviewRequestKey / result identity
- pending retry/reconcile state

restart時:

```text
DB recovery
→ exact target fresh readback
→ pending worker/review identity reconcile
→ stale evidence invalidation
→ Resume Gate
→ unfinished stage継続
```

Workerの途中停止をWork completionへ読み替えない。

## 10. Security境界

- Local Self ReviewerはProduct write authorityを持たない。
- External ReviewerへProduct write credentialを渡さない。
- reviewer API keyをImplementer / local worker requestへ含めない。
- PostgreSQL credentialをlocal modelへ渡さない。
- local profileのsecret値はschema/result/logへ出力しない。
- Productの`.env`等secret-bearing fileをreview contextへ自動収集しない。
- Local/External Reviewの自由文をHost commandとして実行しない。

## 11. #85 / #87との関係

#85で完成したCodex Implementerは既存Adapterとして維持する。`local-llm-coder`はImplementerPortへ追加する交換可能Adapterであり、#85を無効化しない。

#87で完成したexact-head CI / review / repair evidence契約を維持する。#98は次を拡張する。

- canonical external review前のLocal Self Review Gate
- `LOCAL_PASS` evidence
- Local worker Completion Contract
- Review Level 1..N
- local-llm-coder Backend
- model profile設定

## 12. #88 Runnerへの統合

#88のRunnerは次の状態を識別できるよう拡張する。

```text
IMPLEMENT / REPAIR
→ VERIFY_LOCAL
→ LOCAL_REVIEW
→ LOCAL_PASS
→ EXTERNAL_REVIEW(level)
→ EXTERNAL_PASS
→ HUMAN_VERIFY?
→ INTEGRATE
```

最低限の追加evidence:

- `LocalReviewEvidence`
- `LocalPassEvidence`
- `ReviewLevelEvidence`
- `WorkerCompletionEvidence`

既存のexact target / generation / ScheduleKey / stale rejectionを維持する。

## 13. 後続実装Workの分割方針

#98の設計採用後に、次の実装Workを依存順で登録する。

1. `local-llm-coder`: model profileとruntime config生成
2. `local-llm-coder`: 非対話Worker API/CLIとCompletion Contract
3. Loop Engineering: local-llm-coder Implementer/Reviewer Adapter
4. Loop Engineering: Local Quality Loop / `LOCAL_PASS` evidence
5. Loop Engineering: External Review Level pipelineと設定schema
6. #88: Runner / recoveryへの統合
7. controlled E2E

実装Issue番号は設計採用後にlive状態を確認して割り当てる。

## 14. Hard invariants

- OrchestratorはLoop Engineeringに一つだけ置く。
- local-llm-coderはWorker BackendでありMission/Work Authorityを持たない。
- model/provider/endpointをCoreへハードコードしない。
- Local Self Review PASSなしにExternal Reviewへ進まない。
- Worker自由文だけでCompletion Contract充足とみなさない。
- exact target変更後に旧Local/External Review PASSを流用しない。
- finding修正は同一lineageを優先する。
- required Review LevelをskipしてExternal PASSへ進まない。
- process/session終了をWork終了とみなさない。
- Human GateをAIの自己承認へ置換しない。

## 15. GitHub Repository / Project管理

統合後もGit Repositoryは責務単位で分離する。

```text
ktan514/loop-engineering
- Orchestrator / Workflow / durable state / integration

ktan514/local-llm-coder
- OpenCode / local LLM Worker Backend / runtime profile
```

通常の統合作業では、両RepositoryのIssueをGitHub Project `loop-engineering` / Project #9へ登録し、Project #9を横断ロードマップと依存関係の計画Authorityとして使用する。

したがって基本管理単位は **2 Repository / 1統合Project** とする。

Issueは変更を実装するRepositoryへ作成する。

- `local-llm-coder`自身のmodel profile、runtime生成、Worker API/CLI、OpenCode統合変更は`ktan514/local-llm-coder`へ作成する。
- Local Quality Loop、External Review Level、Adapter、PostgreSQL resume、Runner、Integration Gateは`ktan514/loop-engineering`へ作成する。
- Repositoryをまたぐ依存はProject #9のWork graphとして追跡する。
- 両Repositoryは必要に応じて互いを開発対象Repositoryとして扱えるが、変更は必ず対象Repository自身のbranch / PR / exact-head Gateで管理する。

現段階では`local-llm-coder`専用GitHub Projectを追加しない。二つのProjectで同じStatus / Priority / dependencyを二重管理しない。

将来`local-llm-coder`固有の独立ロードマップが大きくなり専用Projectを設ける場合も、Project #9を統合Authorityとして維持し、専用ProjectはBackend内部計画に限定する。統合Workの状態を二つのProjectで別々のAuthorityとして管理しない。

Repository間の結合はsource copy、subdirectory化、submodule化ではなく、versioned Adapter / CLI / structured request-result contractで行う。

