#!/bin/bash
# Fetch a single Fluentboards board as JSON (includes stages, labels, members).
#
# Usage:
#   get-board.sh <board_id>
#
# Exit: 0 success, 1 bad usage, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

if [ $# -lt 1 ]; then
  cat >&2 <<'USAGE'
Usage: get-board.sh <board_id>

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

BOARD_ID="$1"
[[ "$BOARD_ID" =~ ^[0-9]+$ ]] || { fb_error "board_id must be integer"; exit 1; }

fb_require_credentials
fb_curl GET "/projects/${BOARD_ID}"
