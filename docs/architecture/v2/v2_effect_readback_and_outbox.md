# V2外部効果読戻し・Issue報告outbox設計

管理Issue: #66

上位設計: `v2_adapters_cutover_and_acceptance.md`

状態: 製造仕様

## 1. 目的

V2でDBへ確定済みの外部効果意図だけを、記録済み対象identityへ限定して読戻す。また、DB確定後に生成されたIssue報告outboxだけを重複なく投稿する。

本仕様は外部効果の再実行を扱わない。`INTENT_RECORDED`または`UNCERTAIN`の効果を読戻しても、同じidempotency keyを自動再送してはならない。

## 2. DBに保持する効果契約

`EffectAttempt`は次を保持する。

- `idempotency_key`
- `work_identity`
- `packet_generation`
- `kind`
- `target_identity`
- `status`
- `request_identity`
- `expected_preconditions`
- `expected_effect`

`packet_generation`は効果意図を発行した`TaskPacket.generation`と同一でなければならない。再開時はDBから復元した最新task packetのgenerationとpending effectの`packet_generation`を照合し、不一致・欠落なら外部提供元を読まず`RECONCILE_REQUIRED(EFFECT_PACKET_MISMATCH)`へ安全側停止する。

`expected_preconditions`と`expected_effect`は秘密情報を含まない文字列key/valueだけを保存する。既存の`loop_effect_attempts`へversioned migrationで`packet_generation`とJSONB列を追加し、旧行はgeneration欠落・空objectとして読み込めるようにする。旧行や不完全行は自動補完しない。

新規効果意図では、正の`packet_generation`と読戻しに必要な期待値を空のまま発行してはならない。旧行や不完全行を読戻せない場合は`UNKNOWN`またはgeneration不一致として停止し、推測で`CONFIRMED`または`NO_EFFECT`へ進めない。

## 3. 読戻し状態遷移

読戻し入力はDBから復元した`INTENT_RECORDED`または`UNCERTAIN`だけである。

- 現在値が`expected_effect`と一致する: `CONFIRMED`
- 現在値が`expected_preconditions`と一致し、効果未発生を証明できる: `NO_EFFECT`
- 対象identityが移動した、必要値が欠落した、提供元読取りに失敗した、現在値がどちらとも一致しない: `UNKNOWN`

`UNCERTAIN`は「再送可能」を意味しない。対象限定の読戻しによって結果を証明できた場合に限り`CONFIRMED`または`NO_EFFECT`へ確定できる。`UNKNOWN`のままならDB状態を変更しない。

このためDBの効果結果更新は、`INTENT_RECORDED`と`UNCERTAIN`の双方から`CONFIRMED`または`NO_EFFECT`への一方向確定を許可する。`CONFIRMED`、`NO_EFFECT`から別状態へ戻してはならない。

`NO_EFFECT`は同じpacket・同じidempotency keyの再送許可ではない。新しい効果が必要な場合は、上位の再調整処理で異なるgenerationの新規TaskPacketを明示発行しなければならない。#66のreadback adapterは新規packetを生成しない。

## 4. GitHub読戻しadapter

adapterは対象Repositoryを生成時に固定し、`EffectAttempt.target_identity`以外のIssue、PR、branchを探索しない。

### 4.1 PUSH

`target_identity`は`branch:<branch-name>`とする。

- `expected_preconditions["head"]`: 実行前HEAD
- `expected_effect["head"]`: 実行後に期待するHEAD

対象branchだけを読み、現在HEADが実行後HEADなら`CONFIRMED`、実行前HEADなら`NO_EFFECT`、それ以外は`UNKNOWN`とする。

### 4.2 READY

`target_identity`は`pr:<number>`とする。

必須値:

- `expected_preconditions["head"]`
- `expected_preconditions["draft"]`
- `expected_effect["draft"]`

対象PRだけを読み、HEAD一致を先に確認する。HEADが異なる場合は`UNKNOWN`とする。HEAD一致後、draft状態が期待効果なら`CONFIRMED`、実行前状態なら`NO_EFFECT`、それ以外は`UNKNOWN`とする。

### 4.3 MERGE

`target_identity`は`pr:<number>`とする。

必須値:

- `expected_preconditions["head"]`
- `expected_preconditions["base"]`
- `expected_preconditions["state"]`
- `expected_effect["state"]`

対象PRだけを読み、head/baseが期待identityと一致することを先に確認する。現在stateが期待効果なら`CONFIRMED`、実行前stateなら`NO_EFFECT`、それ以外は`UNKNOWN`とする。merge済み判定で別PRやbranchを探索しない。

### 4.4 ISSUE_UPDATE

`target_identity`は`issue:<number>`とする。

`expected_preconditions`と`expected_effect`は、今回変更するGitHub Issueの型付きscalar fieldだけを同じkey集合で持つ。初期対応fieldは`state`と`title`に限定する。

対象Issueだけを読み、全fieldが期待効果なら`CONFIRMED`、全fieldが実行前値なら`NO_EFFECT`、混在・未対応field・欠落は`UNKNOWN`とする。

### 4.5 REPORT

Issue報告は通常の作業effectと分離し、5節のoutbox publisherで扱う。`EffectReadbackPort`へ`REPORT`が渡された場合は`UNKNOWN`とし、通常effectの再実行経路へ流さない。

## 5. Issue報告outbox publisher

### 5.1 DB取得と一意性

`WorkStatePort`は指定`work_identity`の`PENDING`報告だけを`recorded_at ASC`で返す。返却型は少なくとも次を持つ。

- outbox identity
- work identity
- report kind
- checkpoint identity
- body

outboxの論理一意キーは上位正本どおり`work identity + checkpoint identity + report kind`とし、DB unique indexで強制する。`checkpoint_identity`がNULLの場合も同じWork・report kindで複数行を許可しない。`enqueue`はidentity主キーだけでなくこの論理一意制約の競合でも既存行を維持し、新しい重複行を作らない。

`body`は既存契約どおり4000文字以下とし、秘密値、認証情報、無加工診断、作業パケット全体を含めない。publisher側でも空本文・上限超過を再検証し、不正本文をGitHubへ送らない。

### 5.2 重複識別子

GitHubへ投稿する本文には、outbox identityそのものではなく、そのSHA-256 digestを使った機械識別commentを付加する。

```text
<!-- loop-engineering:v2-report:<sha256> -->
```

これによりDB内部identityを人間向け本文へ公開せず、同じoutboxの投稿済み判定を行う。

### 5.3 投稿手順

1. DBから`PENDING`報告を取得する。
2. 対象WorkのRepositoryとIssue番号だけを使用する。
3. 対象Issueのcommentを全ページ読み、同じdigest markerを探す。
4. markerが既に存在する場合、投稿せずDBを`PUBLISHED`へ確定する。
5. markerが無い場合だけ1回投稿する。
6. 投稿後に同じ対象Issueを再読戻しし、markerを確認できた場合だけDBを`PUBLISHED`へ確定する。
7. 投稿または読戻しに失敗した場合は`PENDING`のまま終了する。

publisherは作業effectを呼び出さない。報告再試行が発生してもPUSH、MERGE、READY、ISSUE_UPDATEを再実行しない。

GitHub commentの「marker確認→投稿」は提供元側で原子的にできないため、production Hostでは同一Work leaseを保持した実行者だけがpublisherを呼ぶ。#66のpublisherはleaseを取得せず、#67のHost合成でこの呼出し前提を固定する。

## 6. 失敗時契約

- pending effectのpacket generation欠落・不一致: `RECONCILE_REQUIRED(EFFECT_PACKET_MISMATCH)`、GitHub読取り0回
- GitHub読取り失敗: `UNKNOWN`
- target identity不正: `UNKNOWN`
- 期待値不足: `UNKNOWN`
- target HEAD/base不一致: `UNKNOWN`
- outbox一覧DB読取り失敗: fail-closed
- outbox本文不正: GitHub投稿0回、`PENDING`維持
- Issue comment読取り失敗: 報告は`PENDING`のまま
- Issue comment投稿失敗: 報告は`PENDING`のまま
- 投稿後readback失敗: 報告は`PENDING`のまま

いずれも同じ外部効果を再送する理由にしてはならない。

## 7. 試験

最低限、次を決定論的なfake runner / fake DBで固定する。

1. `PUSH`の期待後HEAD一致、期待前HEAD一致、別HEAD。
2. `READY`のexact HEAD一致とdraft前後状態、別HEAD。
3. `MERGE`のexact head/base一致とstate前後、別head/base。
4. `ISSUE_UPDATE`のstate/title前後、一部混在、未対応field。
5. `UNCERTAIN`を読戻しだけで`CONFIRMED`または`NO_EFFECT`へ確定でき、再送処理が存在しない。
6. pending effectのpacket generationが最新TaskPacketと不一致・欠落ならGitHub読取り0回で停止する。
7. `UNKNOWN`ではDB結果を変更しない。
8. outboxの論理一意キーがDB制約で固定され、enqueueが別identityによる重複行を作らない。
9. outbox marker既存時は投稿0回で`PUBLISHED`。
10. marker無し時は投稿1回、readback成功後だけ`PUBLISHED`。
11. 不正本文、投稿失敗、投稿後readback失敗は`PENDING`維持。
12. pending outboxの取得順序とwork境界を固定する。
13. Ruff、strict Mypy、全pytest、compileall、diff-checkを通す。

## 8. #67への引継ぎ

#66では読戻しadapter、outbox publisher、DB契約と試験までを実装する。`--v2-once`への合成、明示Work選択、旧入口拒否、Work lease保持下でのoutbox publisher呼出し、`CONFIRMED`/`NO_EFFECT`後のTaskPacket・Checkpoint遷移、実機受入は#67で行う。
