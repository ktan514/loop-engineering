# standalone Loop Engineering 自律完遂Mission

version: 5
generation: 1

## Mission

Mission #33とParent #9を通じて、`ktan514/loop-engineering`をstandalone Loop Engineering Platformとして完成させる。GitHub上の現在Issue、PR、branch、厳密HEAD、CI、Project #9状態を現在状態の正本とする。このGoalへ固定PR番号や固定HEADを書き込まず、最新Mission/Work CheckpointとGitHub liveの再取得から現在対象を解決する。

## 正本と安全境界

- Project `loop-engineering` / Project #9をstandalone計画の正本とする。
- Mission管理Issueは#33、Parent Issueは#9、運用Authorityは#26とする。
- `root_issue`は現時点で独立Rootを定義していないため未設定とする。
- Repository正本設計と選択中Work Issueが設計意図を定義する。
- Codexは実装者である。独立レビューは診断用途であり、GitHub書込認証情報を受け取らず、実装branchを書き換えない。
- 信頼済みレビューワー境界は`docs/architecture/v2/trusted_host_reviewer_boundary.md`で定義する。レビューワー認証情報をCodexやレビュー対象作業領域へ渡さない。
- 秘密情報、token、要求header、データベースURL、提供元の生失敗内容をRepositoryファイル、Issue、PR、Checkpoint、通常ログへ含めない。
- `AGENTS.md`のファイルシステム変更安全規則を常に適用し、今回対象外の既存file/directoryを作成・上書き・移動・削除へ巻き込まない。
- source `ktan514/ai-liver-yura`は読取専用とし、standalone側の都合でsourceのfile / Issue / PR / branch / Projectを変更しない。

## 現在の完成順序

Mission #33の現在重点は次の順序とする。

1. #31: standalone Mission / Project / 設定Authorityを確定する。
2. #32: current runtime / script / operation docs / testsを`src/loop_engineering`正本へ統一し、不要な`tools/loop_engine`旧入口を除去する。
3. #27: 実ホストCLIを実運転し、Work統合後に次Work選択または正当な`YIELD_EXTERNAL`へ継続できることを確認する。`MISSION_CHECKPOINT_TARGET_UNRESOLVED`が正常な中間状態で再発する場合は同一Workで修正する。

この並びはMission完了条件そのものではなく、現在dependency-readyな作業順序である。GitHub live dependencyが変化した場合はfresh Resume Gateで再評価する。

## 人間向け文章の言語

人間が読む文章は日本語を基本言語とする。対象にはGitHub上のIssue本文・コメント・Checkpoint、PR本文・コメント・レビュー説明、Mission Checkpoint、再開証明（Resume Certificate）だけでなく、RepositoryのMarkdown/README/設計書/運用書、commit message、コードコメント、docstring、人間向けログ・warning・error説明を含む。

英語の概念語を日本語文章の名詞としてそのまま置くことを避け、日本語で意味を表現したうえで、必要な場合だけ原語を括弧内へ併記する。

機械識別子、状態値（`ACTIVE`、`PASS`、`NOT_RUN`、`REQUEST_CHANGES`など）、branch名、command、file path、SHA、API/class/function/field名、機械可読JSONのkey/value、外部API固定値、固有名詞は必要に応じて英語表記を維持してよい。ただし、それらを含む説明文章自体は日本語にする。

## 設定Authority

対象Product Workspaceのpath、Repository、Project、Mission、Parent、Integration Work、CI、モデル、API endpoint等の非秘密設定は、選択された`config/loop-engineering.ini`を実行時Authorityとする。

`loop-engineering`自身を実運転対象とする場合の現在値:

```text
repository       = ktan514/loop-engineering
project_number   = 9
mission_issue    = 33
parent_issue     = 9
root_issue       = 未設定
integration_work = 27
```

Workspace pathは各ホストの実際の絶対pathを設定する。Project / Mission / Parent / Integration Workを便宜的に同一番号へ潰さない。

OpenAI API keyの標準環境変数名は`OPENAI_API_KEY`とする。`OPENAI_API_KEY_REVIEWER`は使用しない。

`LOOP_POSTGRES_DSN`と`LOOP_TRUSTED_REVIEWER_SOCKET`は利用契約が未確定のため、空値や自己参照値を設定済みとして扱わない。必要性と値が確定するまではローカル`.env`へ定義しない。

## 再開判定と作業パケット

branch作成、実装、push、merge、新規PR作成の前に、GitHub現在状態、最新Mission/Work Checkpoint、正本設計、現在の作業系列を読み、Issue、設計、branch、基点/HEAD SHA、検証、次作業、競合を含む再開証明（Resume Certificate）を生成する。会話記憶から現在状態を推測しない。競合、未知の作業系列、説明できないSHA変更がある場合は、実装前に再調整する。

選択した作業パケット（Task Packet）は、正本、対象範囲・非対象、厳密対象、依存関係、受け入れ確認、危険境界、唯一の能動作業系列を明示する。

## Loop

観測（OBSERVE）→ 再調整（RECONCILE）→ 再開判定（RESUME GATE）→ 選択（SELECT）→ 計画（PLAN）→ 設計（DESIGN）→ 実装（IMPLEMENT）→ 検証（VERIFY）→ レビュー・診断（REVIEW/DIAGNOSE）→ 修正・統合（FIX/INTEGRATE）→ Checkpoint → 反復・外部待機・介入（REPEAT/YIELD/ESCALATE）。

すべての外部確認は上限付きで実行し、秘密情報を含まない型付き診断を返す。事前確認（Preflight）は起動停止要因とWork単位の利用不可を区別する。Project書込能力の証拠は読取専用で取得し、通常の事前確認ではProjectを変更しない。

## レビューと機能修正方針

独立レビューは診断用途である。実行する場合はレビューを厳密HEADへ結び付け、同一`ReviewAttempt` identityの重複試行を要求せず、HEAD変更後の古い結果を拒否する。ただし`REQUEST_CHANGES`または`NOT_RUN`だけではMissionや統合を停止しない。

決定論的テスト、厳密HEAD CI、現在状態の再取得、または再現可能な実行経路によって、Loopが起動・前進できない、誤った厳密対象を操作する、必須効果を失う、その他必須の実行時挙動を満たさないことが証明された場合だけ修正を必須とする。非機能的な強化、追加監査性、仮説的な競合防御、レビューワー提供元の可用性問題は記録して後回しにしてよい。よりきれいな判定を得ることだけを目的にレビューワー実行系を強化しない。

レビュー待ちはMission停止条件ではない。レビューだけを待つ高頻度監視や反復sleepを行わない。別の依存関係を満たしたWorkへ進む場合も、新しい再開判定を通して選択し、レビュー待ち作業系列へ変更を混在させない。

## Mission状態とCheckpoint

有用な独立作業が存在する間、Missionは`ACTIVE`である。外部結果だけが未確定の場合、`YIELD_EXTERNAL`は安全な実行結果であり、人間介入ではない。`PAUSED_FOR_INTERVENTION`は、安全に推測できない実際の利用者判断または正本判断が必要な場合だけ使用する。

重要な遷移ごとにMission/Work、branch/PR、厳密HEAD、完了作業、検証、停止要因、最初の再開作業をGitHubへ記録する。個別Work完了だけでMission #33を完了にしない。

Mission #33を完了できるのは、少なくとも次が現在証拠で満了した場合だけである。

- standalone設定Authorityが一意に定義されている。
- 実装正本が`src/loop_engineering/**`へ一意に統一されている。
- 実ホストCLIが設定済みWorkspaceで起動・継続し、次Work選択または正当な外部待機まで前進する。
- 曖昧な現在対象では安全側停止する。
- exact-head CIを含むRepository GateがPASSする。
- 既知P0 blockerが解消されている。

## 復元と識別確認

このファイルをCodex `/Goal`のRepository正本とする。起動器は内容をそのまま読み、秘密情報を含まない`CODEX_MISSION_GOAL_VERSION`、`CODEX_MISSION_GOAL_GENERATION`、このUTF-8ファイルそのもののSHA-256を`CODEX_MISSION_GOAL_SHA256`へ注入する。事前確認は版、世代、内容identityを検証する。Codex画面状態を直接読むことはできないため、起動器のhashが証明するのは読み込んだ正本ファイルであり、検証不能な画面転記ではない。

`/Goal`を失った場合は、古い要約から再構成せず、このファイルを起動器からそのまま読み込み、版・世代・hashを再注入する。

移行元AI Liverゆら用Goalの旧内容はGit履歴に保持し、active standalone Goalとして再利用しない。
