#!/bin/bash
# Post a comment (or threaded reply) to a Fluentboards task.
#
# Usage:
#   post-comment.sh <url-or-id> <text> [--parent=COMMENT_ID]
#
# The comment body is passed as the second positional argument. Anything
# starting with "--" is treated as a flag, not body text. To post text that
# starts with "--", read from stdin: post-comment.sh TASK "$(cat)"
#
# Stdout: JSON response (created comment).
# Exit:   0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

PARENT_ID=""
POSITIONAL=()
for a in "$@"; do
  case "$a" in
    --parent=*) PARENT_ID="${a#--parent=}" ;;
    *) POSITIONAL+=("$a") ;;
  esac
done
set -- ${POSITIONAL[@]+"${POSITIONAL[@]}"}

if [ $# -lt 2 ]; then
  cat >&2 <<'USAGE'
Usage: post-comment.sh <url-or-id> <text> [--parent=COMMENT_ID]

Posts a comment on a task. With --parent=N, posts as a threaded reply to
comment N. The text is JSON-escaped automatically.

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

TARGET="$1"
TEXT="$2"

fb_require_credentials
fb_resolve "$TARGET"

TEXT_JSON=$(fb_json_escape "$TEXT")
COMMENT_TYPE="comment"
PARENT_FIELD=""
if [ -n "$PARENT_ID" ]; then
  if ! [[ "$PARENT_ID" =~ ^[0-9]+$ ]]; then
    fb_error "--parent must be a positive integer (got: $PARENT_ID)"
    exit 1
  fi
  COMMENT_TYPE="reply"
  PARENT_FIELD=",\"parent_id\":${PARENT_ID}"
fi

BODY="{\"comment\":${TEXT_JSON},\"comment_type\":\"${COMMENT_TYPE}\"${PARENT_FIELD}}"

fb_curl POST "/projects/${FB_BOARD_ID}/tasks/${FB_TASK_ID}/comments" "$BODY"
