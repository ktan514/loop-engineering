# 取り込み範囲

本同期は`ai-liver-yura`の製品コード全体をコピーしない。

対象:
- Loop Engineering正本設計
- `tools/loop_engine/**`
- `tests/tools/loop_engine/**`
- Loop運用文書
- `AGENTS.md`の文章言語・Mission継続規則
- GitHub Project / branch / commit運用
- source Loop Issue / PR履歴
- 日本語化と実運用修正履歴

非対象:
- AI Liver製品`app/**`
- Character / Brain / Body / Streaming / Game等の製品機能
- Yura固有の外部サービス設定

Yura固有のIssue番号、Project番号、Repository名が実装内に残る部分は抽出元snapshotの由来として保持し、専用化の変更では設定化または専用Authorityへ置換する。
