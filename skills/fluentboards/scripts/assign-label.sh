#!/bin/bash
# Attach a label to a Fluentboards task.
#
# Usage:
#   assign-label.sh <url-or-id> <label_id>
#
# Exit: 0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

if [ $# -lt 2 ]; then
  cat >&2 <<'USAGE'
Usage: assign-label.sh <url-or-id> <label_id>

Attaches an existing label to a task. To see available labels on a board,
call: request.sh GET /projects/BOARD/labels

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

TARGET="$1"
LABEL_ID="$2"
[[ "$LABEL_ID" =~ ^[0-9]+$ ]] || { fb_error "label_id must be integer"; exit 1; }

fb_require_credentials
fb_resolve "$TARGET"

BODY="{\"taskId\":${FB_TASK_ID},\"labelId\":${LABEL_ID}}"
fb_curl POST "/projects/${FB_BOARD_ID}/labels/task" "$BODY"
