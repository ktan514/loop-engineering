#!/usr/bin/env bash
set -euo pipefail

OWNER="${LOOP_PROJECT_OWNER:-ktan514}"
PROJECT_TITLE="${LOOP_PROJECT_TITLE:-loop-engineering}"
REPO="${LOOP_REPOSITORY:-ktan514/loop-engineering}"
LABEL="${LOOP_LABEL:-loop-engineering}"

command -v gh >/dev/null || { echo "gh is required" >&2; exit 1; }

gh auth status >/dev/null

gh label create "$LABEL" --repo "$REPO" --color ededed --force >/dev/null

PROJECT_JSON="$(gh project list --owner "$OWNER" --format json)"
PROJECT_NUMBER="$(python -c '
import json,sys
raw=json.load(sys.stdin)
projects=raw.get("projects", raw if isinstance(raw,list) else [])
title=sys.argv[1]
for p in projects:
    if p.get("title")==title:
        print(p.get("number")); break
' "$PROJECT_TITLE" <<<"$PROJECT_JSON")"

if [[ -z "$PROJECT_NUMBER" || "$PROJECT_NUMBER" == "None" ]]; then
  CREATED="$(gh project create --owner "$OWNER" --title "$PROJECT_TITLE" --format json)"
  PROJECT_NUMBER="$(python -c 'import json,sys; print(json.load(sys.stdin)["number"])' <<<"$CREATED")"
fi

echo "Project: $OWNER/$PROJECT_TITLE (#$PROJECT_NUMBER)"

gh project link "$PROJECT_NUMBER" --owner "$OWNER" --repo "${REPO#*/}" >/dev/null 2>&1 || true

field_names() {
  gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 100 --format json \
    | python -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(x.get("name","") for x in d.get("fields",[])))'
}

ensure_select() {
  local name="$1" options="$2"
  if ! field_names | grep -Fxq "$name"; then
    gh project field-create "$PROJECT_NUMBER" --owner "$OWNER" \
      --name "$name" --data-type SINGLE_SELECT --single-select-options "$options" >/dev/null
  fi
}

ensure_date() {
  local name="$1"
  if ! field_names | grep -Fxq "$name"; then
    gh project field-create "$PROJECT_NUMBER" --owner "$OWNER" \
      --name "$name" --data-type DATE >/dev/null
  fi
}

ensure_select "Priority" "P0,P1,P2,P3"
ensure_select "Area" "Core,GitHub / Planning,Implementer,Reviewer,CI / Verification,Runtime / Infrastructure,Self Improvement,Documentation / Management"
ensure_select "Issue level" "Parent,Work,Integration,Management"
ensure_select "作業種別" "設計,実装,検証,調査,不具合,ドキュメント"
ensure_date "Start date"
ensure_date "Target date"

FIELDS_JSON="$(gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 100 --format json)"
FIELDS_JSON="$FIELDS_JSON" PROJECT_NUMBER="$PROJECT_NUMBER" python - <<'PY'
import json
import os

data = json.loads(os.environ["FIELDS_JSON"])
number = os.environ["PROJECT_NUMBER"]
fields = {field.get("name"): field for field in data.get("fields", [])}
required = ["Status", "Priority", "Area", "Issue level", "作業種別", "Start date", "Target date"]
missing = [name for name in required if name not in fields]
if missing:
    raise SystemExit("Missing project fields: " + ", ".join(missing))
print(f"Project #{number} fields: PASS")
status = fields["Status"]
options = [option.get("name") for option in status.get("options", [])]
required_status = ["Backlog", "Ready", "In progress", "Review", "Verification", "Blocked", "Done"]
missing_status = [name for name in required_status if name not in options]
if missing_status:
    print("Status options need one-time GitHub UI alignment: " + ", ".join(missing_status))
else:
    print("Status options: PASS")
PY

echo "Repository link + label + fields setup complete."
