# Loop自律実行機

管理Mission: #33
配置正本: `standalone_package_layout.md`

## 継続実行するホストコマンド

standalone Loop Engineeringの継続実行入口は次とする。

```bash
pipenv run python -m loop_engineering
```

内部の制御遷移は引き続き1回ごとに範囲を限定する。

```text
Preflight
→ Observe
→ Reconcile
→ Resume Gate
→ Select
→ Plan / Execute / Wait / Integrate
→ Readback
→ Checkpoint
```

安全な遷移が1回完了しただけでは処理を終了しない。`COMPLETED`後はGitHubを再取得し、次の限定遷移を開始する。

診断用の1遷移実行:

```bash
pipenv run python -m loop_engineering --once
```

変更を行わない導入確認:

```bash
pipenv run python -m loop_engineering --validate-installation
```

`python -m tools.loop_engine`をstandalone実行入口として使用しない。

## 対象identity

Repository、Project、Mission、Parent、Integration Work、trunk、CI workflow等は選択された設定ファイルから解決する。コードや本設計へProduct固有番号を固定しない。

`loop-engineering`自身を現在の検証対象とする場合のAuthorityは次である。

- Repository: `ktan514/loop-engineering`
- Project: #9
- Mission: #33
- Parent: #9
- 運用Authority: #26
- Integration Work: #27
- Root: 未設定

現在対象はDBの作業記録と安全Checkpointから復元する。PR / branch / HEADなど外部効果に関わる対象は、DBが記録したidentityを用いて必要な遷移の直前にGitHubから再取得する。Issue commentや会話記憶だけから対象を推測しない。

## 接続口と実行境界

決定論的CoreはMission監督、型付きsnapshot、再開・書込み判定、注入された実行器・検証器・Checkpoint接続口を保持する。

standalone実装正本は`src/loop_engineering/**`であり、製品実行系を別packageへ複製しない。現在の配置契約は`standalone_package_layout.md`を正本とする。

Issueの状況報告は候補探索にも再開入力にも使用しない。DBの安全Checkpointに現在対象の識別情報が欠落または不正な場合は安全側停止にする。

ただし、Work統合後から次Work選択前までの正常な中間状態と、破損したCheckpointは区別する必要がある。#27はこの実運転境界を検証する。

## CodexとGit操作の責務境界

Codexは作業fileの編集と必要な検証を担当する。branch作成・切替、基幹統合、`git add`、commit、push、PR作成、Checkpoint更新などGit管理情報とGitHub変更は信頼済みホストが担当する。

Codexの既定実行は`workspace-write`の隔離領域を用い、`.git`へ直接書き込ませない。

```text
codex -a never exec --sandbox workspace-write -c sandbox_workspace_write.network_access=true <instruction>
```

`danger-full-access`でGit操作を成立させる方式は採用しない。

Codex子プロセスへレビューワー認証情報、database認証情報、その他不要な秘密情報を渡さない。OpenAI API keyは`OPENAI_API_KEY`をホスト側の信頼境界で扱い、Codex作業指示や通常ログへ露出させない。

## Codex実行の進捗証明

Codex終了コード0だけでは進捗証拠にならない。

実装、CI不具合修正、統合競合解消の後は信頼済みホストがGitHubの現在状態を再取得し、期待したfile / branch / PR / HEAD / Checkpointが実際に前進したことを確認する。

進捗がなければ`IMPLEMENTER_NO_PROGRESS`として扱う。

Codex実行には経過時間だけを理由にした固定強制終了を設けない。長時間でも生存している子プロセスはheartbeatを出し、明示的なprocess failure、起動失敗、`SIGINT`等の決定論的失敗で安全側停止する。

## Work選択と継続

現在Workが安全かつ実行可能なら継続する。現在WorkがCI / review / Human Verification等の外部待ちだけの場合、依存関係を満たす別Workがあればfresh Resume Gateを通して切り替える。

独立して進められるWorkがなく、残る条件が外部待ちだけなら`YIELD_EXTERNAL`として現在runを終了できる。review待ちや外部提供元復旧を高頻度pollingしない。

## CIと統合

Codex起動、CI判定、Ready化、merge、Issue終了、Checkpointの前に現在Issue、PR、branch、HEADを再取得する。

CI証拠は期待する現在HEADへ厳密に結び付ける。

- current exact HEAD CIがない / queued / in_progress → 外部待機
- current exact HEAD CIがfailure → 同一lineageの修正へ戻る
- current exact HEAD CIがPASS → 他の統合前条件を確認
- 古いHEADのCI → 現在証拠として使用しない

通常統合は期待HEADを固定したmerge commit方式で行い、統合後にtrunkをfresh readbackする。

## Work統合後の次Work計画

Work merge/readback/close完了後は、次の限定遷移でGitHub liveとProjectを再取得し、依存関係を満たした次Workを選択する。

この時点で「前Workは統合済みだが次Workはまだ未選択」という状態は起こり得る。これを即座に破損Checkpointとみなして古いWorkへfallbackしない。

最新Checkpointが、`Mission state: ACTIVE`、完了したWork、統合済みPR、および次Workを最新状態から選択する次の操作（next action）を明示し、`current Work`を持たない場合は、この正常な計画境界として扱う。ホストは現在対象なしとして次Work計画へ遷移する。この判定は完了済み識別子だけでは行わず、上記の明示項目がそろわないCheckpointや、現在Workと矛盾するCheckpointは安全側停止にする。

次Workが選択された後はCheckpointへ少なくとも`current Work`を明示する。有効PRがある場合だけ`current PR`と厳密HEADを記録し、存在しないPR / HEADを捏造しない。

#27では次を検証する。

```text
WORK_MERGED
→ 次Work計画または正当なYIELD_EXTERNAL
```

正常な中間状態で`MISSION_CHECKPOINT_TARGET_UNRESOLVED`へ停止しない一方、本当にcurrent targetが曖昧・競合している場合はfail-closedを維持する。

## 継続実行規則

- `COMPLETED` → 再観測して次の限定遷移へ進む。
- `YIELD_EXTERNAL / CI_PENDING` → 粗い上限付き間隔で再確認してよい。
- その他の外部待機 → 独立Workがなければ安全にrunを終了する。
- `INTERVENTION_REQUIRED` → 型付き理由と秘密情報を含まない診断を残して安全側停止する。
- `MISSION_COMPLETE` → Mission #33の完了契約が現在証拠で満了した場合だけ返す。
- `--once` → dispositionにかかわらず1回の限定遷移で戻る。

## 書込み安全性

変更は次の順で行う。

```text
現在条件の再取得
→ Write Gate
→ 効果の実行
→ 効果の再取得
→ Checkpoint
```

`AGENTS.md`のファイルシステム変更安全規則をホスト・Codexの双方に適用する。作成・上書き・移動・削除で今回対象外の既存データを巻き込まない。

## CLI終了コード

`--once`では次を使用する。

- `0`: 安全な遷移完了
- `2`: `YIELD_EXTERNAL`
- `3`: 安全側停止を要する介入・再調整

既定の継続実行では途中の`COMPLETED`だけで終了しない。自動再開できない外部待機、介入、またはMission完了へ達した場合に最終終了する。
