#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 7 ]]; then
  echo "usage: $0 ISSUE_URL STATUS PRIORITY AREA ISSUE_LEVEL START_DATE TARGET_DATE [WORK_TYPE]" >&2
  exit 2
fi

ISSUE_URL="$1"
STATUS="$2"
PRIORITY="$3"
AREA="$4"
ISSUE_LEVEL="$5"
START_DATE="$6"
TARGET_DATE="$7"
WORK_TYPE="${8:-実装}"

OWNER="${LOOP_PROJECT_OWNER:-ktan514}"
PROJECT_TITLE="${LOOP_PROJECT_TITLE:-loop-engineering}"

PROJECT_JSON="$(gh project list --owner "$OWNER" --format json)"
PROJECT_NUMBER="$(python -c '
import json,sys
raw=json.load(sys.stdin)
projects=raw if isinstance(raw,list) else raw.get("projects", [])
title=sys.argv[1]
for p in projects:
    if p.get("title")==title:
        print(p.get("number")); break
' "$PROJECT_TITLE" <<<"$PROJECT_JSON")"

if [[ -z "$PROJECT_NUMBER" || "$PROJECT_NUMBER" == "None" ]]; then
  echo "Project not found: $OWNER/$PROJECT_TITLE" >&2
  exit 1
fi

ITEMS="$(gh project item-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 100000 --format json)"
PRESENT="$(ISSUE_URL="$ISSUE_URL" ITEMS="$ITEMS" python - <<'PY'
import json
import os

url = os.environ["ISSUE_URL"]
data = json.loads(os.environ["ITEMS"])
print("yes" if any((item.get("content") or {}).get("url") == url for item in data.get("items", [])) else "no")
PY
)"

if [[ "$PRESENT" != "yes" ]]; then
  gh project item-add "$PROJECT_NUMBER" --owner "$OWNER" --url "$ISSUE_URL" >/dev/null
fi

gh project item-edit "$PROJECT_NUMBER" --owner "$OWNER" --url "$ISSUE_URL" --field "Status" --value "$STATUS" >/dev/null
gh project item-edit "$PROJECT_NUMBER" --owner "$OWNER" --url "$ISSUE_URL" --field "Priority" --value "$PRIORITY" >/dev/null
gh project item-edit "$PROJECT_NUMBER" --owner "$OWNER" --url "$ISSUE_URL" --field "Area" --value "$AREA" >/dev/null
gh project item-edit "$PROJECT_NUMBER" --owner "$OWNER" --url "$ISSUE_URL" --field "Issue level" --value "$ISSUE_LEVEL" >/dev/null
gh project item-edit "$PROJECT_NUMBER" --owner "$OWNER" --url "$ISSUE_URL" --field "作業種別" --value "$WORK_TYPE" >/dev/null
gh project item-edit "$PROJECT_NUMBER" --owner "$OWNER" --url "$ISSUE_URL" --field "Start date" --date "$START_DATE" >/dev/null
gh project item-edit "$PROJECT_NUMBER" --owner "$OWNER" --url "$ISSUE_URL" --field "Target date" --date "$TARGET_DATE" >/dev/null

READBACK="$(gh project item-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 100000 --format json)"
ISSUE_URL="$ISSUE_URL" READBACK="$READBACK" python - <<'PY'
import json
import os

url = os.environ["ISSUE_URL"]
data = json.loads(os.environ["READBACK"])
item = next((x for x in data.get("items", []) if (x.get("content") or {}).get("url") == url), None)
if item is None:
    raise SystemExit("Project readback failed: item missing")
print("Project item readback: PASS")
PY
