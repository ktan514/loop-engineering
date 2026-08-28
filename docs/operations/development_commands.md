# Local Development Verification Commands

Owner: Issue #15
Status: Bootstrap operations baseline

## Environment

ローカルPython依存環境はPipenvで管理する。

初回または依存変更時:

```bash
pipenv install --dev
```

`Pipfile.lock` が存在する通常の環境再現:

```bash
pipenv sync --dev
```

依存追加・更新時は `Pipfile` と `Pipfile.lock` を同じ変更で更新する。

## Verification

```bash
pipenv run pytest
pipenv run ruff check src tests
pipenv run mypy --strict src tests
pipenv run python -m compileall -q src tests
git diff --check
```

各実装Workはtargeted testを先に実行し、その後必要に応じて上記full local gatesを実行する。

## Dependency authority

- `pyproject.toml`: package/build metadata、pytest/Ruff/Mypy等のtool configuration
- `Pipfile`: direct runtime/dev dependency declaration
- `Pipfile.lock`: resolved dependency versions / reproducible local environment

`pyproject.toml` のoptional dev dependenciesと`Pipfile`を二重管理しない。

## Boundary

この文書はlocal deterministic verificationのbaselineであり、GitHub Actions等のCI provider contractではない。exact-target CIは後続Phaseで設計・実装する。
