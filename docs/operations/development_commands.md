# Local Development Verification Commands

Owner: Issue #15
Status: Bootstrap operations baseline

## Environment

Python 3.10以上を使用する。

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Verification

```bash
python -m pytest
python -m ruff check src tests
python -m mypy --strict src tests
python -m compileall -q src tests
git diff --check
```

各実装Workはtargeted testを先に実行し、その後必要に応じて上記full local gatesを実行する。

## Boundary

この文書はlocal deterministic verificationのbaselineであり、GitHub Actions等のCI provider contractではない。exact-target CIは後続Phaseで設計・実装する。
