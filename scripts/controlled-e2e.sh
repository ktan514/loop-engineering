#!/bin/bash
# Loop Engineering V2 controlled real-Production E2E Gate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

die() {
  echo "error: $*" >&2
  exit 2
}

command -v gh >/dev/null 2>&1 || die "ghが見つかりません"
command -v git >/dev/null 2>&1 || die "gitが見つかりません"
command -v pyenv >/dev/null 2>&1 || die "pyenvが見つかりません"
command -v pipenv >/dev/null 2>&1 || die "pipenvが見つかりません"

OWNER="${LOOP_E2E_OWNER:-$(gh api user --jq .login)}"
NAME="${LOOP_E2E_NAME:-loop-engineering-controlled-e2e}"
FULL="$OWNER/$NAME"
PROJECT_TITLE="${LOOP_E2E_PROJECT_TITLE:-Loop Engineering controlled E2E - $NAME}"
TEMPLATE_PROJECT_OWNER="${LOOP_E2E_TEMPLATE_PROJECT_OWNER:-ktan514}"
TEMPLATE_PROJECT_NUMBER="${LOOP_E2E_TEMPLATE_PROJECT_NUMBER:-10}"
LOCAL_ROOT="${LOOP_E2E_LOCAL_LLM_CODER_ROOT:-$HOME/workspace/ollama/local-llm-coder}"
PROFILE="${LOOP_E2E_MODEL_PROFILE:-local-main}"
INITIAL_STATUS="${LOOP_E2E_INITIAL_STATUS:-Backlog}"
DONE_STATUS="${LOOP_E2E_DONE_STATUS:-Done}"
CI_WORKFLOW="${LOOP_E2E_CI_WORKFLOW:-E2E CI}"
LOW_REVIEW_MODEL="${LOOP_E2E_LOW_REVIEW_MODEL:-}"
HIGH_REVIEW_MODEL="${LOOP_E2E_HIGH_REVIEW_MODEL:-}"
REVIEW_API_BASE="${LOOP_E2E_REVIEW_API_BASE:-https://api.openai.com/v1}"
POSTGRES_DRIVER="${LOOP_E2E_POSTGRES_DRIVER:-${LOOP_POSTGRES_DRIVER:-host}}"
POSTGRES_CONTAINER="${LOOP_E2E_POSTGRES_CONTAINER:-${LOOP_POSTGRES_CONTAINER:-}}"
MODE="${1:-run}"
MIN_LOCAL_SHA="${LOOP_E2E_MIN_LOCAL_LLM_CODER_SHA:-40f14742c6034d909c47c123aa8a661afa724163}"

[[ "$OWNER" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "LOOP_E2E_OWNERが不正です"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "LOOP_E2E_NAMEが不正です"
[[ "$TEMPLATE_PROJECT_NUMBER" =~ ^[1-9][0-9]*$ ]] || die "template project番号が不正です"
[[ -d "$LOCAL_ROOT" ]] || die "local-llm-coder rootがありません: $LOCAL_ROOT"
[[ -x "$LOCAL_ROOT/scripts/run-worker.sh" ]] || die "local-llm-coder Workerがありません"
[[ -f "$LOCAL_ROOT/config/local-profiles.json" ]] || die "local-profiles.jsonがありません"
git -C "$LOCAL_ROOT" cat-file -e "$MIN_LOCAL_SHA^{commit}" 2>/dev/null || {
  die "local-llm-coder current Worker基準SHAを取得できません: $MIN_LOCAL_SHA"
}
git -C "$LOCAL_ROOT" merge-base --is-ancestor "$MIN_LOCAL_SHA" HEAD || {
  die "local-llm-coder HEADにcurrent Worker #7が含まれていません"
}
[[ -n "${LOOP_POSTGRES_DSN:-}" ]] || die "LOOP_POSTGRES_DSNを設定してください"
[[ -n "${OPENAI_API_KEY:-}" ]] || die "OPENAI_API_KEYを設定してください"

if [[ -z "$LOW_REVIEW_MODEL" || -z "$HIGH_REVIEW_MODEL" ]]; then
  SOURCE_CONFIG="${LOOP_E2E_SOURCE_CONFIG:-$ROOT/config/loop-engineering.ini}"
  if [[ -f "$SOURCE_CONFIG" ]]; then
    SOURCE_MODEL="$(
      awk '
        /^\[models\]$/ { in_models=1; next }
        /^\[/ { in_models=0 }
        in_models && /^[[:space:]]*reviewer_model[[:space:]]*=/ {
          sub(/^[^=]*=[[:space:]]*/, "")
          gsub(/[[:space:]]+$/, "")
          print
          exit
        }
      ' "$SOURCE_CONFIG"
    )"
    LOW_REVIEW_MODEL="${LOW_REVIEW_MODEL:-$SOURCE_MODEL}"
    HIGH_REVIEW_MODEL="${HIGH_REVIEW_MODEL:-$SOURCE_MODEL}"
  fi
fi
[[ -n "$LOW_REVIEW_MODEL" ]] || die "LOOP_E2E_LOW_REVIEW_MODELを設定してください"
[[ -n "$HIGH_REVIEW_MODEL" ]] || die "LOOP_E2E_HIGH_REVIEW_MODELを設定してください"

if [[ "$POSTGRES_DRIVER" == "docker" && -z "$POSTGRES_CONTAINER" ]]; then
  die "docker PostgreSQLではLOOP_E2E_POSTGRES_CONTAINERが必要です"
fi

if [[ "$MODE" != "audit" && "${LOOP_E2E_SKIP_LOCAL_GATES:-0}" != "1" ]]; then
  (
    cd "$LOCAL_ROOT"
    bash tests/worker/run.sh
    bash tests/opencode-boundaries/run.sh
    bash tests/worker/live-smoke.sh
  )
  echo "LOCAL_LLM_CODER_CURRENT_GATE=PASS"
fi

RUN_ROOT="${LOOP_E2E_RUN_ROOT:-$HOME/.local/share/loop-engineering/controlled-e2e/$NAME}"
PRODUCTION="$LOCAL_ROOT/productions/$NAME"
CONFIG="$RUN_ROOT/loop-engineering.ini"
GOAL="$RUN_ROOT/goal.md"
mkdir -p "$RUN_ROOT"

if gh repo view "$FULL" >/dev/null 2>&1; then
  MARKER="$(gh api "repos/$FULL/contents/.loop-controlled-e2e" --jq .name 2>/dev/null || true)"
  [[ "$MARKER" == ".loop-controlled-e2e" ]] || {
    die "既存Repository $FULL はcontrolled E2E markerを持ちません。操作しません"
  }
else
  gh repo create "$FULL" --private --description "Loop Engineering controlled E2E fixture"
fi

if [[ -d "$PRODUCTION/.git" ]]; then
  REMOTE="$(git -C "$PRODUCTION" remote get-url origin)"
  case "$REMOTE" in
    *"$FULL"*) ;;
    *) die "既存Production directoryのoriginが対象Repositoryと一致しません: $PRODUCTION" ;;
  esac
else
  [[ ! -e "$PRODUCTION" ]] || die "Production pathがGit repositoryではありません: $PRODUCTION"
  mkdir -p "$(dirname "$PRODUCTION")"
  gh repo clone "$FULL" "$PRODUCTION"
fi

if ! git -C "$PRODUCTION" rev-parse HEAD >/dev/null 2>&1; then
  git -C "$PRODUCTION" switch --orphan main
  mkdir -p "$PRODUCTION/src" "$PRODUCTION/tests" "$PRODUCTION/docs" "$PRODUCTION/.github/workflows"

  cat > "$PRODUCTION/.loop-controlled-e2e" <<'EOF'
Loop Engineering controlled E2E fixture.
This marker authorizes the E2E harness to reuse this dedicated repository.
EOF

  cat > "$PRODUCTION/src/e2e_product.py" <<'EOF'
"""Controlled E2E target product."""


def slugify(value: str) -> str:
    """Goal実装前のstub。"""
    raise NotImplementedError("Loop Engineeringが実装する")
EOF

  cat > "$PRODUCTION/tests/test_e2e_product.py" <<'EOF'
import unittest

from src.e2e_product import slugify


class SlugifyTests(unittest.TestCase):
    def test_normalizes_words(self) -> None:
        self.assertEqual(slugify("  Hello,   LOOP Engineering!  "), "hello-loop-engineering")

    def test_collapses_symbols(self) -> None:
        self.assertEqual(slugify("A___B---C"), "a-b-c")

    def test_strips_edges(self) -> None:
        self.assertEqual(slugify("***OpenAI***"), "openai")


if __name__ == "__main__":
    unittest.main()
EOF

  cat > "$PRODUCTION/README.md" <<'EOF'
# Loop Engineering controlled E2E product

このRepositoryはLoop Engineering V2の製造Completion Gate専用fixtureです。
EOF

  cat > "$PRODUCTION/.github/workflows/ci.yml" <<EOF
name: $CI_WORKFLOW

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - run: python -m unittest discover -s tests -v
      - run: git diff --check
EOF

  git -C "$PRODUCTION" add -A
  git -C "$PRODUCTION" commit -m "chore: controlled E2E fixtureを初期化する"
  git -C "$PRODUCTION" push -u origin main
fi

HOLD_BRANCH="historical/hold"
HOLD_PR_NUMBER="$(
  gh pr list --repo "$FULL" --state open --head "$HOLD_BRANCH"     --json number --jq '.[0].number // empty'
)"
if [[ -z "$HOLD_PR_NUMBER" && "${LOOP_E2E_CREATE_HOLD_PR:-1}" == "1" ]]; then
  git -C "$PRODUCTION" fetch origin main
  git -C "$PRODUCTION" switch main
  git -C "$PRODUCTION" reset --hard origin/main
  git -C "$PRODUCTION" switch -C "$HOLD_BRANCH"
  printf '%s\n' "historical HOLD lineage; must never be adopted by Loop Engineering."     > "$PRODUCTION/HOLD.md"
  git -C "$PRODUCTION" add HOLD.md
  git -C "$PRODUCTION" commit -m "chore: historical HOLD lineageを用意する"
  git -C "$PRODUCTION" push -u origin "$HOLD_BRANCH"
  gh pr create --repo "$FULL" --base main --head "$HOLD_BRANCH" --draft     --title "HOLD: historical lineage"     --body "Controlled E2E: this PR must remain unrelated to current Work lineage."     >/dev/null
  git -C "$PRODUCTION" switch main
  git -C "$PRODUCTION" reset --hard origin/main
fi

PROJECT_NUMBER="$(
  gh project list --owner "$OWNER" --format json \
    --jq ".projects[] | select(.title == \"$PROJECT_TITLE\") | .number" \
    | head -n 1
)"
if [[ -z "$PROJECT_NUMBER" ]]; then
  PROJECT_NUMBER="$(
    gh project copy "$TEMPLATE_PROJECT_NUMBER" \
      --source-owner "$TEMPLATE_PROJECT_OWNER" \
      --target-owner "$OWNER" \
      --title "$PROJECT_TITLE" \
      --format json \
      --jq .number
  )"
  gh project link "$PROJECT_NUMBER" --owner "$OWNER" --repo "$NAME"
fi
[[ "$PROJECT_NUMBER" =~ ^[1-9][0-9]*$ ]] || die "Project番号を取得できません"

STATUS_OPTIONS="$(
  gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --format json     --jq '.fields[] | select(.name == "Status") | .options[].name'
)"
ACCEPTANCE_FIELD="$(
  gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --format json     --jq '.fields[] | select(.name == "Acceptance criteria digest") | .name'
)"
[[ -n "$STATUS_OPTIONS" ]] || die "ProjectにStatus fieldがありません"
if [[ "$ACCEPTANCE_FIELD" != "Acceptance criteria digest" ]]; then
  gh project field-create "$PROJECT_NUMBER"     --owner "$OWNER"     --name "Acceptance criteria digest"     --data-type TEXT     >/dev/null
  ACCEPTANCE_FIELD="$(
    gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --format json       --jq '.fields[] | select(.name == "Acceptance criteria digest") | .name'
  )"
fi
[[ "$ACCEPTANCE_FIELD" == "Acceptance criteria digest" ]] || {
  die "Acceptance criteria digest fieldを作成できません"
}
printf '%s\n' "$STATUS_OPTIONS" | grep -Fxq "$INITIAL_STATUS" || {
  die "Statusに初期option '$INITIAL_STATUS' がありません"
}
printf '%s\n' "$STATUS_OPTIONS" | grep -Fxq "$DONE_STATUS" || {
  die "Statusに完了option '$DONE_STATUS' がありません"
}

cat > "$GOAL" <<'EOF'
version: controlled-e2e-v1
generation: 1

# Goal

既存testsを満たす汎用的なslugify関数を完成させる。
実装前にdocs/design.mdへ設計を記録し、その設計と実装を一致させる。

## 完了条件

- [ ] src/e2e_product.py の slugify(value: str) -> str を実装する
- [ ] 前後の空白・記号を除去し、英字を小文字化する
- [ ] 英数字以外の連続部分を単一の - に正規化する
- [ ] python3 -m unittest discover -s tests -v が全PASSする
- [ ] docs/design.md に実装設計が存在する
EOF

cat > "$CONFIG" <<EOF
[project]
key = controlled-e2e-$NAME
workspace_path = $PRODUCTION
repository = $FULL
trunk_branch = main
work_branch_template = loop/work-{issue}
initial_project_status = $INITIAL_STATUS
done_project_status = $DONE_STATUS
project_owner = $OWNER
project_number = $PROJECT_NUMBER
label = loop-engineering-e2e
authority_refs =
ci_workflow_name = $CI_WORKFLOW
improvement_area = Runtime / Infrastructure
issue_level = Work

[self_improvement]
enabled = false

[models]
implementer_provider = local-llm-coder
implementer_model = local
implementer_profile = $PROFILE
reviewer_provider = openai
reviewer_model = $HIGH_REVIEW_MODEL
reviewer_api_base = $REVIEW_API_BASE
reviewer_api_key_env = OPENAI_API_KEY

[local_llm_coder]
root = $LOCAL_ROOT
production_name = $NAME
model_profile = $PROFILE

[review.level.1]
provider = openai
model = $LOW_REVIEW_MODEL
api_base = $REVIEW_API_BASE
credential_env = OPENAI_API_KEY
required = true
timeout_seconds = 1200
context_policy = default
escalation_policy = BLOCK
passes_required = 2

[review.level.2]
provider = openai
model = $HIGH_REVIEW_MODEL
api_base = $REVIEW_API_BASE
credential_env = OPENAI_API_KEY
required = true
timeout_seconds = 1200
context_policy = default
escalation_policy = HUMAN
passes_required = 2

[verification.command.1]
identity = unit-tests
argv_json = ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
working_directory = .
timeout_seconds = 300
required = true

[verification.command.2]
identity = diff-check
argv_json = ["git", "diff", "--check", "HEAD"]
working_directory = .
timeout_seconds = 120
required = true

[credentials]
github_token_env = GH_TOKEN

[operational_store]
dsn_env = LOOP_POSTGRES_DSN
required = true
driver = $POSTGRES_DRIVER
docker_container = $POSTGRES_CONTAINER
migration_policy = required

[runtime]
trusted_reviewer_socket_env = LOOP_TRUSTED_REVIEWER_SOCKET
EOF

export PIPENV_VENV_IN_PROJECT=1
pyenv local "$(tr -d '[:space:]' < "$ROOT/.python-version")"
pipenv sync --dev

pipenv run python -m loop_engineering \
  --config "$CONFIG" \
  --migrate-operational-store

export LOOP_MISSION_GOAL_PATH="$GOAL"

case "$MODE" in
  prepare)
    echo "CONTROLLED_E2E_PREPARED=PASS"
    echo "repository=$FULL"
    echo "project_number=$PROJECT_NUMBER"
    echo "workspace=$PRODUCTION"
    echo "config=$CONFIG"
    echo "goal=$GOAL"
    exit 0
    ;;
  once)
    pipenv run python -m loop_engineering \
      --config "$CONFIG" \
      --v2-autonomous-once \
      --v2-max-iterations 1
    ;;
  restart)
    COMPLETED=0
    for _attempt in $(seq 1 100); do
      set +e
      OUTPUT="$(
        pipenv run python -m loop_engineering \
          --config "$CONFIG" \
          --v2-autonomous-once \
          --v2-max-iterations 1
      )"
      STATUS=$?
      set -e
      printf '%s\n' "$OUTPUT"
      if printf '%s' "$OUTPUT" | grep -q '"status": "GOAL_COMPLETED"'; then
        COMPLETED=1
        break
      fi
      if [[ "$STATUS" -eq 3 ]]; then
        die "restart E2EがINTERVENTION_REQUIREDで停止しました"
      fi
      sleep 10
    done
    [[ "$COMPLETED" == "1" ]] || die "100 process restart内にGoal完了へ収束しませんでした"
    ;;
  run)
    pipenv run python -m loop_engineering \
      --config "$CONFIG" \
      --v2-autonomous \
      --v2-max-iterations 100
    ;;
  audit)
    ;;
  *)
    die "modeはprepare / once / restart / run / auditのいずれかです"
    ;;
esac

GOAL_COUNT="$(
  gh issue list --repo "$FULL" --state all --limit 1000 --json body \
    --jq '[.[] | select(.body | contains("<!-- loop-engineering-goal:"))] | length'
)"
WORK_COUNT="$(
  gh issue list --repo "$FULL" --state all --limit 1000 --json body \
    --jq '[.[] | select(.body | contains("<!-- loop-engineering-work:"))] | length'
)"
OPEN_WORK_COUNT="$(
  gh issue list --repo "$FULL" --state open --limit 1000 --json body \
    --jq '[.[] | select(.body | contains("<!-- loop-engineering-work:"))] | length'
)"
ACTIVE_OPEN_PR_COUNT="$(
  gh pr list --repo "$FULL" --state open --json headRefName \
    --jq '[.[] | select(.headRefName != "historical/hold")] | length'
)"
HOLD_DRAFT_COUNT="$(
  gh pr list --repo "$FULL" --state open --head "$HOLD_BRANCH" \
    --json isDraft --jq '[.[] | select(.isDraft == true)] | length'
)"
MERGED_PR_COUNT="$(gh pr list --repo "$FULL" --state merged --json number --jq 'length')"
MAIN_HEAD="$(gh api "repos/$FULL/commits/main" --jq .sha)"

[[ "$GOAL_COUNT" == "1" ]] || die "Goal Issueが一意ではありません: $GOAL_COUNT"
[[ "$WORK_COUNT" -ge 1 ]] || die "Work Issueがありません"
[[ "$OPEN_WORK_COUNT" == "0" ]] || die "未完Work Issueがあります: $OPEN_WORK_COUNT"
[[ "$ACTIVE_OPEN_PR_COUNT" == "0" ]] || die "current Workのopen PRが残っています"
if [[ "${LOOP_E2E_CREATE_HOLD_PR:-1}" == "1" ]]; then
  [[ "$HOLD_DRAFT_COUNT" == "1" ]] || die "historical HOLD PRがdraftのまま保持されていません"
fi
[[ "$MERGED_PR_COUNT" -ge 1 ]] || die "merge済みPRがありません"
[[ "$MAIN_HEAD" =~ ^[0-9a-f]{40}$ ]] || die "main HEADを確認できません"

git -C "$PRODUCTION" fetch origin main
git -C "$PRODUCTION" switch main
git -C "$PRODUCTION" reset --hard origin/main
(
  cd "$PRODUCTION"
  python3 -m unittest discover -s tests -v
)

echo "CONTROLLED_E2E_AUDIT=PASS"
echo "repository=$FULL"
echo "project_number=$PROJECT_NUMBER"
echo "main_head=$MAIN_HEAD"
echo "goal_issues=$GOAL_COUNT"
echo "work_issues=$WORK_COUNT"
echo "merged_prs=$MERGED_PR_COUNT"
echo "historical_hold_draft_prs=$HOLD_DRAFT_COUNT"
