#!/bin/bash
# Fetch a single Fluentboards task as JSON.
#
# Usage:
#   get-task.sh <url-or-id>
#
# Stdout: JSON response (task with assignees, labels, board, stage, settings).
# Exit:   0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

if [ $# -lt 1 ]; then
  cat >&2 <<'USAGE'
Usage: get-task.sh <url-or-id>

Fetches a single task as JSON. The argument can be a full wp-admin URL,
a short URL (fbs-ID), a "board/task" form, or a bare task ID.

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

fb_require_credentials
fb_resolve "$1"
fb_curl GET "/projects/${FB_BOARD_ID}/tasks/${FB_TASK_ID}"
