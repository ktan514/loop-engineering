# V2受入条件digest Authority契約

管理Issue: #67

上位正本:

- `v2_adapters_cutover_and_acceptance.md`
- `work_state_and_issue_boundary.md`

関連製造仕様:

- `v2_host_cutover_and_packet_execution.md`

状態: 製造仕様追補

## 1. 目的

V2 Work Definitionが参照する`Acceptance criteria digest`について、Project fieldのschema、値の生成元、更新条件、欠落時の停止契約を一意に定義する。

このdigestはIssue本文やcommentを実行時に解析する代替経路ではない。人間が承認したcanonical acceptance sourceの版を、Projectの型付きfieldへ明示的に固定するためのAuthorityである。

## 2. Project field

V2 Workを管理するGitHub Projectは、次のfieldを持つ。

```text
name: Acceptance criteria digest
type: Text
```

V2 Hostはfield名を完全一致で読む。field自体が存在しない、対象itemに値が設定されていない、空文字である場合は、受入条件が未確定として変更を開始しない。

欠落時の型付き結果は`ACCEPTANCE_CRITERIA_DIGEST_MISSING`とする。GitHub / GraphQL等の提供元読取不能は`WORK_DEFINITION_UNAVAILABLE`系として扱い、field欠落と通信失敗を同じ理由へ潰さない。

## 3. canonical acceptance source

各V2 Workは、V2移行前に1つのRepository正本文書をcanonical acceptance sourceとして定める。

- sourceはGit管理されたUTF-8 text fileとする。
- Issue本文、Issue comment、PR本文、会話履歴をsourceにしない。
- source pathはWorkの設計・管理記録から人間が一意に確認できるものとする。
- sourceを変更した場合は、古いdigestのままpacketを発行してはならない。

Issue #67のcanonical acceptance sourceは次とする。

```text
docs/architecture/v2/v2_host_cutover_and_packet_execution.md
```

同文書の「受入試験」を含む製造仕様全体を、#67の受入Authorityとして扱う。

## 4. digest形式

Project fieldへ保存する値は次の形式とする。

```text
sha256:<64 lowercase hex>
```

`<64 lowercase hex>`は、canonical acceptance sourceとしてGitにcommitされたファイルbytes全体のSHA-256とする。

例:

```bash
DIGEST="sha256:$(shasum -a 256 <canonical-source> | awk '{print $1}')"
```

V2 Hostはdigestを自動生成しない。Hostの責務は、Project fieldに人間が明示したAuthorityが存在することと、その値をWork Definition revisionへ取り込むことだけである。

## 5. 更新契約

canonical acceptance sourceを変更した場合は、次の順序を守る。

1. source変更を設計レビューする。
2. source変更をcommitする。
3. 新しいsource bytesからdigestを生成する。
4. Project itemの`Acceptance criteria digest`を新値へ更新する。
5. V2 Work Definitionを再同期する。
6. 旧revisionを前提にしたpacketがある場合は自動上書きせず、definition conflictとして停止する。

Project fieldの値だけを理由なく変更してはならない。source変更なしのdigest変更はAuthority不整合として扱う。

## 6. Project初期整備

V2を利用するProjectでは、最初のV2 Work移行前に`Acceptance criteria digest` Text fieldを作成する。

field作成はProject schemaの正式整備であり、個別WorkのHuman Verification専用設定ではない。以後のV2 Workも同じfieldを使用する。

Project itemごとに、そのWorkで承認済みのcanonical acceptance sourceに対応するdigestを設定する。

## 7. fail-closed条件

次の場合、V2 Workは外部effectを開始しない。

- `Acceptance criteria digest` fieldが存在しない。
- 対象Project itemにdigestがない。
- digestが空文字である。
- Work Definition提供元を読めない。
- Project itemを一意に解決できない。
- canonical acceptance sourceを変更したのにProject digestが旧revisionのままである。

field欠落と提供元読取不能は別の型付き結果として報告する。

## 8. #67 Human Verification

#67では、Project #9 `loop-engineering` に正式な`Acceptance criteria digest` Text fieldを追加し、`v2_host_cutover_and_packet_execution.md`のcommit済みbytesから生成したdigestを#67 itemへ設定する。

その後、専用PostgreSQL DBで次を確認する。

1. `--migrate-v2-work-state 67`がtyped Work Definitionを受け入れる。
2. packet発行前の再同期で同じdefinition revisionを確認できる。
3. Project field欠落時は`ACCEPTANCE_CRITERIA_DIGEST_MISSING`で停止する。
4. provider command失敗時はtracebackを露出せず、typed unavailableとして停止する。
