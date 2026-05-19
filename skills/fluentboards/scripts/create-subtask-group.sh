#!/bin/bash
# Create a subtask group on a task.
#
# Fluentboards' POST /subtask-group response doesn't always include the new
# group's id directly — this wrapper issues the create, then re-fetches the
# subtask list and extracts the newest group so the caller always gets an id.
#
# Usage:
#   create-subtask-group.sh <url-or-id> <title>
#
# Stdout: JSON: {"group_id": N, "title": "...", "raw_create": {...}}
# Exit:   0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

if [ $# -lt 2 ]; then
  cat >&2 <<'USAGE'
Usage: create-subtask-group.sh <url-or-id> <title>

Creates a subtask group (checklist section) on the task and emits the new
group's id + title. Use create-subtask.sh afterwards to add subtasks to it.

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

TARGET="$1"
TITLE="$2"

fb_require_credentials
fb_resolve "$TARGET"

B="$FB_BOARD_ID"
T="$FB_TASK_ID"

TITLE_JSON=$(fb_json_escape "$TITLE")
CREATE=$(fb_curl POST "/projects/${B}/tasks/${T}/subtask-group" "{\"title\":${TITLE_JSON}}") || exit 3

# Try to pull the id from the create response first — some installs return it.
GROUP_ID=""
if command -v jq >/dev/null 2>&1; then
  GROUP_ID=$(printf '%s' "$CREATE" | jq -r '.subtaskGroup.id // .group.id // .id // empty' 2>/dev/null)
fi

# If the create didn't expose an id, re-fetch the full subtask tree and pick
# the group whose title matches (falling back to the highest-id entry).
if [ -z "$GROUP_ID" ] || [ "$GROUP_ID" = "null" ]; then
  fb_vlog "create response lacked an id — re-fetching subtask groups"
  LIST=$(fb_curl GET "/projects/${B}/tasks/${T}/subtasks") || exit 3
  if command -v jq >/dev/null 2>&1; then
    GROUP_ID=$(printf '%s' "$LIST" | jq -r --arg t "$TITLE" '
      (.subtaskGroups // .data // []) as $g
      | ($g | map(select(.title == $t)) | last | .id?) // ($g | max_by(.id) | .id?)
      // empty
    ' 2>/dev/null)
  elif command -v python3 >/dev/null 2>&1; then
    GROUP_ID=$(printf '%s' "$LIST" | python3 -c '
import json, sys
try: d=json.load(sys.stdin)
except: sys.exit(0)
groups = d.get("subtaskGroups") or d.get("data") or []
title = sys.argv[1]
matches = [g for g in groups if isinstance(g, dict) and g.get("title") == title]
if matches:
    print(matches[-1].get("id",""))
elif groups:
    print(max((g.get("id",0) for g in groups if isinstance(g, dict)), default=""))
' "$TITLE")
  fi
fi

if [ -z "$GROUP_ID" ] || [ "$GROUP_ID" = "null" ]; then
  fb_error "could not determine new group id; raw create response follows:"
  fb_error "$CREATE"
  exit 3
fi

printf '{"group_id":%s,"title":%s,"raw_create":%s}\n' "$GROUP_ID" "$TITLE_JSON" "$CREATE"
