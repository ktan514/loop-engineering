# V2 コミットメッセージ日本語運用

状態: 統合正本
適用日: 2026-08-29
関連正本:
- `AGENTS.md`
- `docs/architecture/v2/project_v2_management_spec.md`
- `docs/architecture/v2/branch_lifecycle_and_commit_hygiene.md`

## 1. 原則

コミットメッセージの**変更説明は日本語**を主要言語とする。

一方、変更種別を表す短いprefixは運用識別子なので英語のまま使用してよい。

推奨例:

- `feat: Workspace設定の読み込みを追加する`
- `fix: Mission Checkpointの対象解決を修正する`
- `docs: 設定手順を更新する`
- `test: 回帰試験を追加する`
- `refactor: 実装正本をsrc packageへ統一する`
- `chore: 開発補助設定を整理する`

機能追加のcommit prefixは **`feat:`** に統一する。`feature:` はcommit prefixとして使用しない。

## 2. 件名

コミット件名は次を満たす。

1. prefix以降の変更説明に日本語文字（ひらがな、カタカナ、漢字）のいずれかを含む。
2. 変更目的を日本語で説明する。
3. prefixだけ、英単語だけ、placeholderだけの件名を使用しない。
4. `x` / `noop` / `trigger` 等、意味のない履歴生成用件名を使用しない。
5. 対象Issueを件名へ付ける場合は `(#番号)` 等、追跡可能な形式を使用してよい。

## 3. 許可する識別prefix

通常使用するcommit prefix:

- `feat:` 機能追加
- `fix:` 不具合修正
- `docs:` 文書変更
- `test:` 試験変更
- `refactor:` 動作目的を変えない構造整理
- `chore:` 開発・運用補助
- `perf:` 性能改善
- `build:` build/package変更
- `ci:` CI変更
- `revert:` 変更の取り消し

必要に応じてscopeを付けた `feat(core): ...` のような形式も識別子として許可する。ただし説明本文は日本語にする。

breaking changeを示す `!` も機械識別として使用できるが、説明は日本語で記述する。

例:

- `feat(core)!: 設定契約を新形式へ変更する`

## 4. branch prefix

`ai-liver-yura`のbranch運用に準拠し、機能追加系branchは **`feature/`** を使用する。

例:

- `feature/workspace-config`
- `fix/checkpoint-resolution`
- `docs/runtime-guide`
- `test/host-runtime-regression`
- `refactor/package-boundary`

`feat/` はbranch prefixとして使用しない。

したがって機能追加では次の対応になる。

```text
branch: feature/workspace-config
commit: feat: Workspace設定の読み込みを追加する
```

## 5. 本文

コミット本文を付ける場合も説明文は日本語を主要言語とする。

ただし、次はそのまま記載してよい。

- ファイルパス
- コマンド
- API名
- 型名・クラス名・関数名
- SHA
- テストツール名
- GitHub Status名などの技術識別子
- `feat:` / `fix:` 等の運用識別prefix
- `feature/` 等のbranch prefix

## 6. Merge commit

GitHubでmerge commitを作成する場合も説明部分は日本語を主要言語とする。

PRタイトルが適切なら、その日本語説明をmerge commitへ使用できる。

## 7. 機械検査

Commit Hygiene Guardを実装する場合は少なくとも次を拒否する。

- prefix以降の説明が英語だけのcommit
- placeholder / no-op / trigger-onlyコミット
- commit prefixとしての `feature:`
- branch prefixとしての `feat/`

`feat:` / `fix:` 等のASCII commit prefix自体は拒否しない。

## 8. 既存履歴

既存branch名、既存の英語prefix、共有済みSHAは、名称統一だけを理由にforce rewriteしない。

説明本文まで英語のみで、かつ安全に修正可能な未共有履歴を整理する場合は日本語化してよい。共有済み履歴を書き換える場合は、影響するbranch、PR、Checkpoint、SHA参照を列挙した専用の再調整手順を必要とする。
