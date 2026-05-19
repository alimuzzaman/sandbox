#!/bin/bash
# Move a Fluentboards task to a different stage (and optionally another board).
# Defaults to --index=0 (top of the target stage) when no index is given.
#
# Usage:
#   move-task.sh <url-or-id> <stage_id> [--index=N] [--board=N]
#
# Exit: 0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

NEW_INDEX=""
NEW_BOARD=""
POSITIONAL=()
for a in "$@"; do
  case "$a" in
    --index=*) NEW_INDEX="${a#--index=}" ;;
    --board=*) NEW_BOARD="${a#--board=}" ;;
    *) POSITIONAL+=("$a") ;;
  esac
done
set -- ${POSITIONAL[@]+"${POSITIONAL[@]}"}

if [ $# -lt 2 ]; then
  cat >&2 <<'USAGE'
Usage: move-task.sh <url-or-id> <stage_id> [--index=N] [--board=N]

Moves a task to a new stage. Defaults to the top of the stage (--index=0).
--index overrides the 0-based position. --board moves to a stage on another board.

Examples:
  move-task.sh 80927 204
  move-task.sh 80927 204 --index=2
  move-task.sh 80927 512 --board=41

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

TARGET="$1"
STAGE_ID="$2"

for v in "$STAGE_ID" "$NEW_INDEX" "$NEW_BOARD"; do
  if [ -n "$v" ] && ! [[ "$v" =~ ^[0-9]+$ ]]; then
    fb_error "ids and index must be integers (got: '$v')"
    exit 1
  fi
done

fb_require_credentials
fb_resolve "$TARGET"

# Default to top of stage when no index requested.
[ -z "$NEW_INDEX" ] && NEW_INDEX=0

BODY="{\"newStageId\":${STAGE_ID},\"newIndex\":${NEW_INDEX}"
[ -n "$NEW_BOARD" ] && BODY="$BODY,\"newBoardId\":${NEW_BOARD}"
BODY="$BODY}"

fb_curl PUT "/projects/${FB_BOARD_ID}/tasks/${FB_TASK_ID}/move-task" "$BODY"
