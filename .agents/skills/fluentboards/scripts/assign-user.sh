#!/bin/bash
# Add or remove a user from a task's assignees.
#
# ⚠ Fluentboards' PUT /projects/{B}/tasks/{T} with property="assignees"
# and value=[user_id] is NOT "replace with this list". It is a **per-ID
# toggle**: every id in the array is flipped against the current assignee
# set (present → removed, absent → added). Sending [A, B, C] does not set
# the assignees to {A, B, C}; it toggles A, then B, then C in order.
#
# This wrapper hides that quirk:
#   - Fetches current assignees
#   - Adds mode (default):  no-op if already assigned, else PUT value=[uid]
#   - Remove mode (--remove): no-op if not assigned, else PUT value=[uid]
#
# Usage:
#   assign-user.sh <url-or-id> <user_id> [--remove]
#
# Exit: 0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

MODE="add"
POSITIONAL=()
for a in "$@"; do
  case "$a" in
    --remove) MODE="remove" ;;
    *) POSITIONAL+=("$a") ;;
  esac
done
set -- ${POSITIONAL[@]+"${POSITIONAL[@]}"}

if [ $# -lt 2 ]; then
  cat >&2 <<'USAGE'
Usage: assign-user.sh <url-or-id> <user_id> [--remove]

Adds (or removes with --remove) a user from a task's assignees. Idempotent:
already-assigned users aren't re-added, already-absent users aren't an error
on removal.

To see assignable user IDs on the task's board: list-assignees.sh BOARD_ID

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

TARGET="$1"
USER_ID="$2"
[[ "$USER_ID" =~ ^[0-9]+$ ]] || { fb_error "user_id must be integer"; exit 1; }

fb_require_credentials
fb_resolve "$TARGET"

B="$FB_BOARD_ID"
T="$FB_TASK_ID"

TASK=$(fb_curl GET "/projects/${B}/tasks/${T}") || exit 3

CURRENT=""
if command -v jq >/dev/null 2>&1; then
  CURRENT=$(printf '%s' "$TASK" | jq -r '
    (.task.assignees // .assignees // [])
    | map(.ID // .id // empty)
    | map(tostring)
    | join(" ")
  ' 2>/dev/null)
elif command -v python3 >/dev/null 2>&1; then
  CURRENT=$(printf '%s' "$TASK" | python3 -c '
import json, sys
try: d=json.load(sys.stdin)
except: sys.exit(0)
t = d.get("task") if isinstance(d.get("task"), dict) else d
arr = t.get("assignees") or []
ids = [str(a.get("ID") or a.get("id")) for a in arr if isinstance(a, dict) and (a.get("ID") or a.get("id"))]
print(" ".join(ids))
')
fi

ALREADY_PRESENT=0
for id in $CURRENT; do
  [ "$id" = "$USER_ID" ] && ALREADY_PRESENT=1
done

# Short-circuit: nothing to toggle.
if { [ "$MODE" = "add" ]    && [ $ALREADY_PRESENT -eq 1 ]; } ||
   { [ "$MODE" = "remove" ] && [ $ALREADY_PRESENT -eq 0 ]; }; then
  fb_vlog "no change needed (user $USER_ID already $( [ "$MODE" = "add" ] && echo "assigned" || echo "not assigned" ))"
  printf '{"unchanged":true,"mode":"%s","user_id":%s,"current_assignees":[%s]}\n' \
    "$MODE" "$USER_ID" "$(echo "$CURRENT" | tr ' ' ',')"
  exit 0
fi

# Single-element toggle. Works identically for add and remove.
BODY="{\"property\":\"assignees\",\"value\":[${USER_ID}]}"
fb_curl PUT "/projects/${B}/tasks/${T}" "$BODY"
