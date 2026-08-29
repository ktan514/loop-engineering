# standalone package配置正本

管理Issue: #32
Mission: #33
状態: standalone配置正本

## 1. 目的

専用Repository `ktan514/loop-engineering`における実装、実行入口、試験、履歴記録の配置を一意にする。

AI Liverゆらでは製品runtime package `app/**`から開発制御系を分離する目的で`tools/loop_engine/**`へ配置していた。standalone RepositoryではLoop Engineering自身が製品であるため、この理由で`tools/`を維持しない。

## 2. 実装正本

唯一の実装packageは次とする。

```text
src/loop_engineering/**
```

通常のmodule実行入口は次へ統一する。

```text
python -m loop_engineering
python -m loop_engineering --once
python -m loop_engineering --validate-installation
python -m loop_engineering.preflight
```

Pipenvを使用する通常運用では`pipenv run`を先頭へ付ける。

`tools.loop_engine` namespace、`python -m tools.loop_engine`、`tools/loop_engine/**`はstandaloneの互換契約として維持しない。

## 3. script境界

Repository内の補助scriptは`loop_engineering.*`だけをimportする。

source checkoutから直接起動するscriptがpackageをimportする必要がある場合は、Repositoryの`src/`を明示的なPython探索pathとして使用する。`tools/`をpackage探索の代替経路にしない。

## 4. 試験配置

standalone packageを直接検証する試験は次へ配置する。

```text
tests/loop_engineering/**
```

試験は`loop_engineering.*`を直接importする。旧`tools.loop_engine` shimの存在を前提にした互換試験は削除し、代わりに`tools/loop_engine`が存在せず、正本packageが旧namespaceへ依存しないことを境界試験で確認する。

## 5. 履歴と移行記録

次に含まれる`tools/loop_engine`表記は、AI Liverゆらにおける当時の配置を示す履歴証拠として保持してよい。

- `docs/history/**`
- `docs/migration/**`
- source snapshot / import manifest
- 統合履歴でsource pathを説明する文書

これらの履歴表記を「現在利用」と誤解してはならない。

## 6. Yura由来V2文書との優先順位

`docs/architecture/v2/**`の一部には、移行元Yuraで`tools/loop_engine/**`を正規配置としていた時点の配置記述が残る。

standalone Repositoryにおける**配置・module invocation・試験配置**については本書を上位正本とする。Yura由来文書の制御動作、状態遷移、安全契約等は引き続き有効だが、本書と矛盾するpath/module配置記述はstandaloneでは置換済みとして扱う。

## 7. 除去条件

`tools/loop_engine/**`を削除できる条件:

1. current runtime/scriptが`tools.loop_engine`をimportしない。
2. current operation docsが`python -m tools.loop_engine`を起動方法として案内しない。
3. standalone試験が`tests/loop_engineering/**`へ配置される。
4. `src/loop_engineering/**`が`tools.loop_engine`へ依存しない。
5. 履歴・移行元説明以外のcurrent referenceがない。
6. 除去後にpytest / Ruff / strict Mypy / compileall / `git diff --check` / exact-head CIがPASSする。

## 8. 非目標

- `src/loop_engineering/**`を別packageへ再実装しない。
- 移行元Yuraの歴史的pathを消さない。
- `ai-liver-yura`を変更しない。
- 互換利用者が存在するという根拠なしに互換shimを維持しない。
