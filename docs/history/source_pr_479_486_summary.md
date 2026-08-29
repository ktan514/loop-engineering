# source PR #479〜#486 移行要点

- #479: 実製品試験対象から#462/#471自身とLoop基盤Issueを除外。
- #480: Codex CLI 0.150系に合わせ、`codex -a never exec --sandbox workspace-write -c sandbox_workspace_write.network_access=true`を既定化。
- #481: runtime進捗、persistent log、失敗診断を追加。
- #482: `mergeable_state=dirty`を人間停止ではなくCodex再調整へ戻す。
- #483: 既定consoleを簡潔化し、Codexの生存通知（heartbeat）を追加。
- #484: 既定を継続実行へ変更し、`--once`を診断モードとして維持。Codexの固定wall-clock timeoutを廃止し、進捗停止検知を追加。
- #485 / #486: Mission Checkpointの固定項目`current Work`、必要時`current PR` / `exact HEAD`を契約化。

これらは実環境で確認された問題への修正であり、専用RepositoryのLoop実行契約として維持する。
