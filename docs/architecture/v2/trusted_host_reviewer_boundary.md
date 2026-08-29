# 信頼済みホストのレビューワー境界

状態: Issue #463 正本追補

## 正本と権限

独立レビューワーは、すべてのレビュー対象作業領域の外側で動く信頼済みホスト制御系サービスとする。`OPENAI_API_KEY_REVIEWER`はCodex起動前にホスト環境からそのサービスだけへ読み込む。Codex、Repository、対象作業領域、LLM入力、ログ、Issue、PRはこの認証情報を受け取らず、読み取らない。Repositoryは`.env`を読み込まず、`.env`はGit管理対象外のままホスト環境だけが読み込む。

## 最小境界

対象作業領域から送信してよいのは、`repository`、`pr_number`、期待するHEAD SHAだけとする。送信先は秘密情報を含まない`YURA_TRUSTED_REVIEWER_SOCKET`で選択するUnixドメインソケットである。対象側はOpenAI SDKにもレビューワー認証情報にも依存しない。ソケットがない場合は`NOT_RUN / REVIEW_BROKER_UNAVAILABLE`とする。

ホストサービスは現在PRのHEAD/基点と差分を独立して読み取り、厳密HEADへ結び付けたうえで提供元を呼び出し、`review_status`、`verdict`、`echoed_head_sha`、上限付き指摘を検証してから、安全化した結果だけを返す。差分はレビュー対象データにすぎず、サービスは対象作業領域のPython、script、package、設定をレビューワー正本として取り込んだり実行したりしない。GitHub書込み認証情報もデータベース認証情報も使用しない。

ホストサービスは提供元呼出前と結果返却前の両方で現在PR HEADを確認する。HEADが変化していれば`NOT_RUN / STALE_TARGET`とする。対象作業領域は、返された`echoed_head_sha`が要求SHAと一致し、さらにGitHubの現在状態を再取得して一致した場合だけ、その結果を現在のものとして扱う。

## 現在の起動時利用

#463はこの信頼済みホスト制御系を通じてのみレビューを要求できる。仲介器はこのRepositoryの外側で導入・運用し、認証情報の取得元、APIクライアント、信頼済み検証器は意図的にRepositoryコードへ含めない。これにより、将来の自律LoopやCodexプロセスからレビューワー認証情報を取得できない境界を維持する。

## CIとRepository権限の証拠

CIの識別解決では、PR番号と期待する厳密HEAD SHAを一組として扱う。`workflow_dispatch`では現在PRを取得し、不一致を拒否する。`github.sha`をPR HEADの証拠にはしない。`pull_request`では、同じ現在identityに対してeventのHEAD/基点も追加確認する。checkoutと品質検査の入力には、解決済みの現在SHAだけを使用する。

Repository書込み能力も読取専用の証拠として確認する。事前確認（Preflight）は固定Repository `ktan514/ai-liver-yura`の`permissions.push`を照会する。ローカルremote、upstream、fork、`git push --dry-run`はこの能力の正本にしない。これらの証拠確認はレビューワー認証情報へアクセスせず、Project #6もRepository資源も変更しない。
