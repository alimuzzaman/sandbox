#!/bin/bash
# List labels defined on a Fluentboards board.
#
# Use this (not get-board.sh) when you need label titles. The board-GET
# endpoint embeds labels but returns `title: null` for each entry on some
# installs; `/projects/{B}/labels` always has the full title/color/bg_color.
#
# Usage:
#   list-labels.sh <board_id>
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
Usage: list-labels.sh <board_id>

Lists the full label catalogue for a board (id, title, color, bg_color).

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

BOARD_ID="$1"
[[ "$BOARD_ID" =~ ^[0-9]+$ ]] || { fb_error "board_id must be integer"; exit 1; }

fb_require_credentials
fb_curl GET "/projects/${BOARD_ID}/labels"
