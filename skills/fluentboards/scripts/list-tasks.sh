#!/bin/bash
# List tasks on a Fluentboards board (paginated).
#
# Usage:
#   list-tasks.sh <board_id> [--page=N] [--per-page=N]
#
# Exit: 0 success, 1 bad usage, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

PAGE=""
PER_PAGE=""
POSITIONAL=()
for a in "$@"; do
  case "$a" in
    --page=*)     PAGE="${a#--page=}" ;;
    --per-page=*) PER_PAGE="${a#--per-page=}" ;;
    *) POSITIONAL+=("$a") ;;
  esac
done
set -- ${POSITIONAL[@]+"${POSITIONAL[@]}"}

if [ $# -lt 1 ]; then
  cat >&2 <<'USAGE'
Usage: list-tasks.sh <board_id> [--page=N] [--per-page=N]

Lists tasks on a board. Default per_page=20.
Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

BOARD_ID="$1"
[[ "$BOARD_ID" =~ ^[0-9]+$ ]] || { fb_error "board_id must be integer"; exit 1; }

fb_require_credentials

QUERY=""
SEP="?"
if [ -n "$PAGE" ];     then QUERY="${QUERY}${SEP}page=${PAGE}";         SEP="&"; fi
if [ -n "$PER_PAGE" ]; then QUERY="${QUERY}${SEP}per_page=${PER_PAGE}"; SEP="&"; fi

fb_curl GET "/projects/${BOARD_ID}/tasks${QUERY}"
