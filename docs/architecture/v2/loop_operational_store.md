# Loop運用記憶 / PostgreSQL管理基盤

管理Issue: #62
旧管理Issue: #44
関連: #27 / #3 / #31 / source `ktan514/ai-liver-yura#470`
状態: canonical architecture

## 1. 目的

PostgreSQLをLoop Engineeringの実行中作業状態の永続正本として使用する。再開時の作業選択済み状態、作業パケット、Checkpoint、未確定effect、lease、重複抑止はDBから復元する。

GitHub Issueは課題・受入条件・優先度・依存関係・完了判断と人間向け状況報告を所有する。PR、branch、厳密HEAD、CI、reviewは各外部提供元が実効果の正本であり、DB復元後に必要な対象だけを再照合する。詳細な責務境界は`work_state_and_issue_boundary.md`を正本とする。

```text
GitHub Issue / Project
→ 課題と作業の統括

Host Mission Goal / Project Registration
→ 実行対象と安全境界

PostgreSQL Operational Store
→ 実行作業 / 作業パケット / Checkpoint / effect / lease / 重複抑止
```

## 2. ローカル実運転の接続方式

ローカル実運転ではDocker上のPostgreSQLを標準とする。

macOSホストへ`psql`や`pg_isready`を別途導入することを要求しない。Loop Engineeringは既に必須CapabilityであるDocker CLIを通して、設定されたPostgreSQLコンテナ内の`psql` / `pg_isready`を実行する。

接続方式は設定で選択できる。

```ini
[operational_store]
dsn_env = LOOP_POSTGRES_DSN
required = true
driver = docker
docker_container = <PostgreSQL container name>
migration_policy = required
```

`dsn_env`には秘密値ではなく環境変数名だけを書く。DSN実値はGit管理外`.env`またはHost secret sourceから供給する。

`driver`:
- `docker`: Dockerコンテナ内のPostgreSQL clientを使用する。
- `host`: 互換用途としてホスト上の`psql` / `pg_isready`を使用する。

Yuraのローカル実運転では`driver = docker`、`required = true`を使用する。

## 3. 秘密情報の受け渡し

DSNから接続に必要な値を解析しても、passwordをcommand lineへ直接埋め込まない。

Docker方式ではHost subprocess環境へ`PGUSER` / `PGPASSWORD` / `PGDATABASE`等を限定的に設定し、`docker exec -e PGPASSWORD`のように環境変数名だけをargvへ渡す。password実値を通常log、Issue、PR、Checkpoint、command表示へ出さない。

認証情報、token、API key、request header、providerの生応答・生エラー、無制限のIssue/PR本文はOperational Storeへ保存しない。

## 4. required / optional policy

`required = true`の場合、次はbootstrap blockerである。

- Docker/選択driverを利用できない
- PostgreSQL clientを利用できない
- PostgreSQL serverへ到達できない
- 対象databaseへ接続できない
- 必須migrationが未適用

この場合、Product実装CodexやGit mutationを開始しない。

`required = false`の場合は従来どおりWork単位の縮退能力として扱える。ただし排他的な永続予約やDB-backed idempotencyが必須の遷移は個別に`DB_UNAVAILABLE` / `YIELD_EXTERNAL`へ落とす。

DBが使えないことだけを理由に「外部変更は発生しなかった」と推定しない。

## 5. Migration Authority

Migrationの正本はRepository内のversioned SQLとする。

```text
src/loop_engineering/migrations/*.sql
```

Alembicを必須にしない。現在の実装に`alembic.ini`が存在しない状態と事前確認契約を一致させる。

Migration runnerは最初に管理表を用意する。

```text
loop_schema_migrations
- filename primary key
- applied_at
```

その後、ファイル名順で未適用SQLだけを実行し、成功したmigration名を同一運用単位で記録する。既に記録済みのmigrationを再実行しない。

事前確認はRepositoryに存在する全migration filenameが`loop_schema_migrations`に存在する場合だけ`postgresql_migration = true`とする。

Migrationは通常の観測とは分離した明示操作で行い、事前確認がschemaを書き換えてはならない。

## 6. 管理対象

既存表:

| 表 | 目的 |
| --- | --- |
| `review_jobs` | review試行予約と重複抑止 |
| `review_results` | secret-safeなreview結果 |
| `api_usage` | 上限付き利用量・費用証拠 |
| `loop_events` | Loop事象・監査証拠 |

管理基盤として追加する表:

| 表 | 目的 |
| --- | --- |
| `loop_runs` | 1回のHost run identityと開始・終了状態 |
| `loop_transitions` | run内の遷移結果と対象identity |
| `loop_checkpoints` | durable checkpointの最小metadata |
| `loop_blockers` | Runtime blockerと解消状態 |
| `loop_leases` | execution lease / 排他identity |
| `loop_dispatches` | Codex/review等のdispatch重複抑止 |
| `loop_external_waits` | review/CI/Human等の待機identity |

ProductのIssue/PR本文やcanonical design本文をDBへ丸ごとmirrorしない。必要な場合はstable identity、revision、digest、状態値、secret-safe metadataだけを保存する。作業状態、作業パケット、再開Checkpoint、effect試行、Issue報告outboxはDBへ追加する。

## 7. restart / reconcile

再起動時はDBの作業記録と安全Checkpointからcurrent Workを復元する。Issue commentを探索して再開対象を決めない。

```text
Operational State readback
+ Issue / Projectの作業定義同期
+ 対象外部効果の限定再照合
+ Host Mission Goal / Project Registration
→ RECONCILE
→ Resume Gate
```

DBは「どの作業を選択し、前回どこまでeffectを要求・確認したか」「重複送信してよいか」「blocker/leaseが残っているか」を提供する。

Issueの定義変更とDBの未完了作業が不一致なら、同期競合として記録して再調整する。外部効果の不一致は対象提供元の読戻しを優先する。

## 8. idempotency / transaction

すべての永続効果は安定したidentityを持つ。

例:
- run: `run_id`
- transition: `run_id + sequence`
- dispatch: `kind + target exact identity + attempt`
- review: exact HEAD + review attempt
- lease: scope + subject identity

同じidentityの再挿入は二重効果を起こさず、既存状態を読み戻せるようにする。

途中失敗で「成功した」と扱わない。書込み失敗は`DB_UNAVAILABLE`等の型付き結果へ正規化する。

## 9. Preflight capability

事前確認は最低限次を分離して表示する。

- `docker`
- `postgresql_client`
- `postgresql_server`
- `postgresql_database`
- `postgresql_migration`

Docker方式では`postgresql_client`は対象コンテナ内の`psql`利用可否を意味する。

`required = true`ではPostgreSQL関連項目を`blocking_for_loop_bootstrap`へ含める。`required = false`では`work_scoped_unavailable`へ含める。

## 10. 実機検証

Yura Product Workspaceを対象に次を確認する。

1. Hostへ`psql`を導入していない状態でもDocker方式のclient probeがPASSする。
2. Docker PostgreSQLが起動していればserver/database probeがPASSする。
3. migration適用前は`postgresql_migration = false`になる。
4. 明示migration後に`postgresql_migration = true`になる。
5. `required = true`でDB停止時はCodex開始前にbootstrapをBLOCKEDにする。
6. DB復旧後、fresh GitHub stateと再調整して再開できる。
7. DSN/passwordが通常logへ出ない。

## 11. Hard invariants

- PostgreSQLは再開する実行作業状態の正本である
- Issueは課題・受入条件・完了判断の正本である
- Issue commentを再開の機械入力にしない
- Product Workspaceへ管理DB credentialを保存しない
- passwordをcommand argvへ埋め込まない
- Preflightはmigrationを自動適用しない
- required DB不成立時にProduct mutationへ進まない
- migrationの正本をversioned SQLへ一意化する
- restartはDB readbackだけでなくfresh GitHub observationを必ず行う
