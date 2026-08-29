# source Issue #384 移行要点

`ai-liver-yura` Issue #384「V2管理: Git履歴・日本語文章・コミット運用を統一する」は、次を一つの管理責務として統合した。

- RepositoryおよびGitHub上の人間向け文章を日本語へ統一
- 日本語の意味表現を先に書き、必要な英語原語だけ括弧内へ併記
- commit messageの日本語運用
- branch lifecycleと通常merge commit方針
- placeholder / no-op / trigger-only commitの禁止
- 既存英語文章の段階的移行
- 翻訳と機能修正の分離
- 既存英語commit履歴を最終的に日本語系列へ再構成する計画

専用Repositoryでは、英語履歴の大規模な書換えをこの取り込み作業で行わない。新規・編集可能な人間向け文章は日本語へ統一し、既存共有履歴は影響範囲を列挙した専用reconciliationなしにforce rewriteしない。
