#!/bin/bash
# List users assignable to tasks on a board.
#
# Distinct from list-members.sh:
#   list-members.sh   → formal board members (GET /projects/B/users)
#   list-assignees.sh → everyone who can be picked as a task assignee
#                       (GET /projects/B/assignees)
#
# Pick this one when you want "can I assign @someone to a task on this board?";
# pick list-members.sh when you want "who belongs to the board?"
#
# Usage:
#   list-assignees.sh <board_id>
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
Usage: list-assignees.sh <board_id>

Lists users who can be assigned to tasks on the board. Response shape:
  { data: [ { ID, display_name, user_email, ... }, ... ] }

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

BOARD_ID="$1"
[[ "$BOARD_ID" =~ ^[0-9]+$ ]] || { fb_error "board_id must be integer"; exit 1; }

fb_require_credentials
fb_curl GET "/projects/${BOARD_ID}/assignees"
