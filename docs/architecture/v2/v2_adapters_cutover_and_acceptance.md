# V2接続層・切替・受入設計

管理Issue: #62

状態: 再設計正本

## 1. 適用範囲

本書は`work_recovery_algorithm.md`で未確定だった接続層、Host合成、旧状態からの切替、受入試験を定義する。V2は旧actual-host入口を呼び出さず、旧自然文Checkpointの解析器も使用しない。

## 2. 接続口

| 接続口 | 読取り | 書込み | 入出力 | 禁止事項 |
| --- | --- | --- | --- | --- |
| `WorkDefinitionPort` | Issue / Project | なし | Issue identity、revision、open/closed、受入条件digest、依存関係、優先度 | 本文・commentから現在WorkやPRを推測しない |
| `EffectReadbackPort` | PR / branch / CI / review / Issue | なし | DBのeffect keyとtarget identity | target外の探索、結果の推測 |
| `EffectExecutorPort` | 必要最小限 | あり | `EffectIntent` | lease、DB意図、Write Gateなしの変更 |
| `IssueReportPublisherPort` | Issue | comment投稿 | outbox identityと安全な報告本文 | DB未確定状態の報告、本文への秘密情報混入 |
| `WorkStatePort` | PostgreSQL | PostgreSQL | WorkRecord、packet、Checkpoint、effect、outbox | Issue定義の上書き |

各接続口は`repository`と対象identityを引数に必須で受け取る。JSON本文、Issue自然文、PR説明文は型付き入力へ変換しない。接続層からの通信失敗は、対象と段階を含む型付き結果に正規化する。

## 3. Issue / Project定義同期

同期器は1回の読取り区間で、対象Issueと必要なProject itemだけを取得する。保存する値は次である。

```text
issue identity / issue revision / issue state
acceptance criteria digest
dependency issue identities
priority / Project status / 計画日
```

IssueがclosedでDBのWorkが`COMPLETED`以外なら`WORK_CLOSED_BEFORE_COMPLETION`、dependencyが未完了なら`WAITING(DEPENDENCY_PENDING)`、同一Issue identityへの異なる受入条件digestは`BLOCKED(WORK_DEFINITION_CONFLICT)`とする。

同期は作業パケットの対象、PR、HEADを作らない。これらはDBに既存のpacketがある場合にだけ、effect読戻しの対象として扱う。

## 4. 外部effectの読戻しと実行

`EffectIntent`は次を必須とする。

```text
idempotency key
work identity / packet generation
kind
target identity
expected preconditions
expected effect
```

| kind | target identity | 読戻しで確認する値 |
| --- | --- | --- |
| `PUSH` | branch、期待HEAD | branch HEAD |
| `MERGE` | PR、base、期待HEAD | PR状態、merge commit、base、head |
| `READY` | PR、期待HEAD | draft状態、head |
| `ISSUE_UPDATE` | Issue、期待revision | Issue revisionと変更値 |
| `REPORT` | Issue、outbox identity | 投稿済みreport identity |

実行順序は常に`lease → DB意図確定 → Write Gate再確認 → effect実行 → readback → DB結果確定`である。Write Gate不成立、readback不能、期待外のidentityは`UNCERTAIN`または`BLOCKED`であり、同じkeyを再送しない。

## 5. outbox

outboxは`work identity + checkpoint identity + report kind`を一意キーとする。報告本文は定型化した状態、確認済みeffect、待機理由、次の人間への連絡事項だけを含み、DB内部ID、秘密値、認証情報、無加工診断、作業パケット全体を除外する。

投稿前に同一outbox identityをIssue timelineから検索する。存在すれば`PUBLISHED`に確定し、存在しなければ投稿して読戻す。通信失敗は`PENDING`のままにし、作業effectを再実行しない。

## 6. V2 Host合成

V2 Host入口は明示的な`--v2-once`だけで起動する。旧入口の別名、通常入口、continuous実行から暗黙に到達してはならない。

```text
Preflight
→ DB migration / capability確認
→ Work identityを明示指定
→ V2ResumeCoordinator
→ READY packetだけを1回実行
→ DB Checkpoint
→ outbox投稿
→ 終了
```

`--v2-once`はWork identityを必須とする。DBに復元対象が無い場合、またはpacket generationが一致しない場合は変更せず終了する。複数Workの自動選択、sleep、polling、別Workへの暗黙移動は行わない。

leaseは`repository + work identity`単位で原子的に取得し、holder、取得時刻、期限、packet generationを保存する。期限切れleaseは、前holderの未確定effectを先に読戻した場合だけ引き継げる。

## 7. 移行と切替

1. versioned SQLでV2表を追加する。既存表と旧記録は削除・更新しない。
2. 明示`--migrate-v2-work-state <issue>`だけが、対象Issueの最新状態を一度読み、V2 WorkRecord候補を作る。
3. PR、HEAD、次遷移、effect意図を旧自然文から復元しない。これらが必要ならmigration結果を`BLOCKED(MIGRATION_TARGET_INCOMPLETE)`にする。
4. migration結果を人間がIssue上で確認し、V2 packetを明示発行するまで`--v2-once`は実行不可とする。
5. 切替後も旧Host入口はV2 Workを受け付けない。旧表は監査専用として保持する。

切替失敗時はV2 Workを`BLOCKED`にする。schemaを巻き戻さず、旧actual-host方式へfallbackしない。

## 8. 受入試験

製造前に次を決定論的な試験で満たす。

1. Issue commentに任意のWork、PR、HEADを書いても再開対象は変化しない。
2. DB安全Checkpointだけから同じpacketを復元できる。
3. `INTENT_RECORDED`と`UNCERTAIN`の全effect kindで再送0回を証明する。
4. effectの読戻しが別HEAD、別base、別revisionなら変更0回を証明する。
5. outbox投稿失敗後、effectなしで同じ報告だけを再送できる。
6. lease競合、期限切れ、DB障害、migration不一致で変更0回を証明する。
7. 旧入口がV2 Workを実行できない。
8. `--v2-once`が1 packet・1遷移だけ実行する。
9. 実機では専用DBと明示指定の非破壊Workで、DB復元とoutboxのreadbackを確認する。

## 9. 製造開始条件

次がすべて満たされるまで実装へ進まない。

- 本書、`work_state_and_issue_boundary.md`、`work_recovery_algorithm.md`間の矛盾がない。
- schema、接続口、状態遷移、切替、受入試験が一意に定義されている。
- 旧actual-host方式へ到達しないことが明示されている。
- Issue #62で設計レビュー可能な受入条件が合意されている。
