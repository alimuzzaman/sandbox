#!/bin/bash
# Create a subtask under a given subtask group.
#
# Usage:
#   create-subtask.sh <url-or-id> <group_id> <title> [--due=YYYY-MM-DD] [--top]
#
# --top inserts the new subtask at the top of the group instead of the bottom.
#
# Need a group_id? Run create-subtask-group.sh or list-subtasks via request.sh.
#
# Exit: 0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

DUE=""
ADD_TO_TOP="false"
POSITIONAL=()
for a in "$@"; do
  case "$a" in
    --due=*) DUE="${a#--due=}" ;;
    --top)   ADD_TO_TOP="true" ;;
    *) POSITIONAL+=("$a") ;;
  esac
done
set -- ${POSITIONAL[@]+"${POSITIONAL[@]}"}

if [ $# -lt 3 ]; then
  cat >&2 <<'USAGE'
Usage: create-subtask.sh <url-or-id> <group_id> <title> [--due=YYYY-MM-DD] [--top]

Example:
  create-subtask.sh 80927 18 "Draft release notes" --due=2026-05-01

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

TARGET="$1"
GROUP_ID="$2"
TITLE="$3"

[[ "$GROUP_ID" =~ ^[0-9]+$ ]] || { fb_error "group_id must be integer"; exit 1; }

fb_require_credentials
fb_resolve "$TARGET"

TITLE_JSON=$(fb_json_escape "$TITLE")
BODY="{\"title\":${TITLE_JSON},\"group_id\":${GROUP_ID},\"add_to_top\":${ADD_TO_TOP}"
if [ -n "$DUE" ]; then
  DUE_JSON=$(fb_json_escape "$DUE")
  BODY="$BODY,\"due_at\":${DUE_JSON}"
fi
BODY="$BODY}"

fb_curl POST "/projects/${FB_BOARD_ID}/tasks/${FB_TASK_ID}/subtasks" "$BODY"
