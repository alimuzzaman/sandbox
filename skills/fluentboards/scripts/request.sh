#!/bin/bash
# Raw authenticated HTTP request to Fluentboards.
# Catch-all for any endpoint in references/endpoints-*.md.
#
# Usage:
#   request.sh METHOD PATH [BODY_JSON | @file]
#
# Examples:
#   request.sh GET /projects
#   request.sh GET '/projects/35/tasks?per_page=5'
#   request.sh POST /projects/35/labels '{"label":"bug","color":"#fff","bg_color":"#c00"}'
#   request.sh PUT /projects/35/tasks/80927 @/tmp/body.json
#
# Exit codes: 0 success, 1 bad usage, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
# shellcheck source=lib/auth.sh
source "$LIB/auth.sh"
# shellcheck source=lib/http.sh
source "$LIB/http.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

usage() {
  cat >&2 <<'USAGE'
Usage: request.sh METHOD PATH [BODY_JSON | @file]

  METHOD      GET | POST | PUT | DELETE | PATCH
  PATH        Endpoint path — absolute ("/projects") or with query ("/projects?per_page=5")
  BODY_JSON   Raw JSON body as a string. Prefix with @ to read from a file.

Examples:
  request.sh GET /projects
  request.sh POST /projects/35/tasks '{"task":{"title":"x","board_id":35,"stage_id":100}}'
  request.sh PUT /projects/35/tasks/80927 @/tmp/body.json

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
}

[ $# -ge 2 ] || usage

METHOD="$1"
PATH_ARG="$2"
BODY="${3:-}"

case "$METHOD" in
  GET|POST|PUT|DELETE|PATCH) ;;
  *) fb_error "unknown HTTP method: $METHOD"; usage ;;
esac

fb_require_credentials
fb_curl "$METHOD" "$PATH_ARG" "$BODY"
