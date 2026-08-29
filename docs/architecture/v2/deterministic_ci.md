# 厳密HEADへ結び付ける決定論的CI

## 1. 目的

機械検証を外部AIから分離し、再現可能なPASS / FAILをPRの厳密HEADへ結び付ける。

CIは設計妥当性そのものを判断せず、pytest、Ruff、厳格Mypy、Python compileall、差分確認などの機械検査を所有する。

## 2. 厳密HEAD契約

PRを対象にするCIは、GitHub eventのmerge refを検証対象とみなさず、現在PRのhead SHAを明示的に解決する。

checkout後の`git rev-parse HEAD`と、解決済みの期待HEAD SHAが一致しなければ失敗させる。

これにより「PR画面で現在レビューしているsource」と「CIが実行したsource」を同一SHAへ固定する。

## 3. 供給系境界

外部Actionや依存の変更は通常の依存更新として明示的に扱う。CI専用の別依存Authorityを作らず、Repositoryで採用している依存管理方式を使用する。

専用`loop-engineering` Repositoryではローカル依存管理の正規方式をPipenvとし、`Pipfile` / `Pipfile.lock`を使用する。抽出元`ai-liver-yura`の`requirements*.txt`方式は履歴由来であり、専用Repositoryへ依存Authorityとして移植しない。

## 4. 機械Gate

専用Repositoryでは少なくとも次を厳密HEADで実行する。

```text
pipenv sync --dev
pipenv run pytest
pipenv run ruff check src tools tests
pipenv run mypy --strict src tools tests
pipenv run python -m compileall -q src tools tests
git diff --check
```

どの検査にも「失敗しても続行」を付けず、必須検査の失敗は全体をFAILとする。

## 5. 並行実行

同じPRの古いrunは新HEAD更新後の現在PASSとして扱わない。古いrunを必要に応じてcancelしてよいが、重要なのは現在HEADとtested HEADのidentityを一致させることである。

## 6. 非責務

CIは次を行わない。

- AI code review
- 自動コード修正
- 自動merge
- secret/API keyを使うlive Provider test
- 人間Verification
- production deployment

## 7. 由来

本規則は`ai-liver-yura` Issue #406およびV2 Deterministic CIで確立した厳密HEAD identityの考え方を、専用RepositoryのPipenv運用へ合わせて移植したものである。
