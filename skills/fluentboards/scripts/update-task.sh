#!/bin/bash
# Update a single property on a Fluentboards task.
#
# Fluentboards' task PUT endpoint takes a {property, value} body rather than a
# patch object. This wrapper hides that quirk and coerces the value into the
# right JSON type.
#
# Usage:
#   update-task.sh <url-or-id> <property> <value>
#
# Common properties: title, description (HTML/TinyMCE — NOT markdown), status,
#                    priority, due_at, started_at, assignees, parent_id,
#                    is_watching, archived_at, board_id, is_template, settings
# See references/endpoints-tasks.md for the full list.
#
# Value coercion:
#   - "true" / "false"     → JSON boolean
#   - "null"               → JSON null
#   - integer              → JSON number
#   - "[1,2]" or "{...}"   → raw JSON (must be valid)
#   - anything else        → JSON string
#
# Exit: 0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

if [ $# -lt 3 ]; then
  cat >&2 <<'USAGE'
Usage: update-task.sh <url-or-id> <property> <value>

Examples:
  update-task.sh 80927 priority high
  update-task.sh 80927 title "New task title"
  update-task.sh 80927 due_at 2026-05-01
  update-task.sh 80927 is_watching true
  update-task.sh 80927 archived_at null
  update-task.sh 80927 assignees '[1,3]'

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

TARGET="$1"
PROPERTY="$2"
VALUE="$3"

fb_require_credentials
fb_resolve "$TARGET"

# Coerce VALUE into a JSON value.
coerce_json_value() {
  local v="$1"
  case "$v" in
    true|false|null) printf '%s' "$v"; return ;;
  esac
  if [[ "$v" =~ ^-?[0-9]+$ ]]; then
    printf '%s' "$v"; return
  fi
  # Raw JSON (array / object) — pass through if it parses.
  case "$v" in
    \[*\]|\{*\})
      if command -v jq >/dev/null 2>&1; then
        if printf '%s' "$v" | jq empty >/dev/null 2>&1; then printf '%s' "$v"; return; fi
      elif command -v python3 >/dev/null 2>&1; then
        if printf '%s' "$v" | python3 -c 'import json,sys; json.load(sys.stdin)' >/dev/null 2>&1; then
          printf '%s' "$v"; return
        fi
      else
        # No validator available — trust the user.
        printf '%s' "$v"; return
      fi
      ;;
  esac
  # Fall through: treat as string.
  fb_json_escape "$v"
}

VALUE_JSON=$(coerce_json_value "$VALUE")
PROPERTY_JSON=$(fb_json_escape "$PROPERTY")
BODY="{\"property\":${PROPERTY_JSON},\"value\":${VALUE_JSON}}"

fb_curl PUT "/projects/${FB_BOARD_ID}/tasks/${FB_TASK_ID}" "$BODY"
