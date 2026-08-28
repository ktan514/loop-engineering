# Yura Loop Engineering Extraction

Owner: Issue #20
Status: implementation baseline

## Source Authority

- `ktan514/ai-liver-yura` Issue #207
- `ktan514/ai-liver-yura` Parent #462
- `ktan514/ai-liver-yura` Work #465
- `ktan514/ai-liver-yura` PR #466
- Source exact HEAD: `b77ce9b06901814e272e9e0c6d46885327a451c7`

## Extraction rule

新規にLoop Engineeringを再設計しない。`ai-liver-yura/tools/loop_engine/` の実装を基準にstandalone packageへ移し、Yura固有値だけを設定化する。

## Source modules

- `models.py`
- `reconciliation.py`
- `scheduler.py`
- `supervisor.py`
- `write_gate.py`
- `health.py`
- `health_state.py`
- `maintenance.py`
- `github_issues.py`

## Destination

`src/loop_engineering/` 配下へ配置する。

## Minimal genericization

`LoopEngineConfig` に次を集約する。

- repository
- owner
- project_number
- label
- trunk_branch
- authority_refs
- improvement_area
- issue_level

Yura固有の `#207/#317/#450/#462`、Project #7、`rebuild/v2-foundation`、`ktan514/ai-liver-yura` はCore logicへ直接埋め込まない。

## Preserve behavior

- live Authorityをsnapshot入力として扱う
- global/work conflict reconciliation
- dependency-ready / actionable selection
- Resume Certificate / Task Packet
- `CONTINUE / YIELD_EXTERNAL / INTERVENTION_REQUIRED / MISSION_COMPLETE`
- duplicate scheduling suppression
- fresh Write Gate + readback effect validation
- health accumulation
- bounded self-improvement candidate generation
- durable secret-safe health identity
- improvement Issue publication
- GitHub Project field live resolution before mutation

## Non-goals

- PostgreSQL operational store
- OpenAI Reviewer transport本体
- Codex execution runner本体
- product runtimeへの組込み
- provider-neutral化のための大規模抽象化

## Verification

- Pipenv
- pytest
- Ruff
- strict Mypy
- compileall
- `git diff --check`
- Yura固有ID残存検索
