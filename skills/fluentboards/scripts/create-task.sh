#!/bin/bash
# Create a new task on a Fluentboards board/stage.
#
# Usage:
#   create-task.sh <board_id> <stage_id> <title> [--priority=low|medium|high]
#                                               [--desc=TEXT]
#                                               [--crm-contact=N]
#
# Exit: 0 success, 1 bad usage, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

PRIORITY=""
DESC=""
CRM_CONTACT=""
POSITIONAL=()
for a in "$@"; do
  case "$a" in
    --priority=*)    PRIORITY="${a#--priority=}" ;;
    --desc=*)        DESC="${a#--desc=}" ;;
    --crm-contact=*) CRM_CONTACT="${a#--crm-contact=}" ;;
    *) POSITIONAL+=("$a") ;;
  esac
done
set -- ${POSITIONAL[@]+"${POSITIONAL[@]}"}

if [ $# -lt 3 ]; then
  cat >&2 <<'USAGE'
Usage: create-task.sh <board_id> <stage_id> <title> [options]

Options:
  --priority=low|medium|high
  --desc=TEXT
  --crm-contact=N

Example:
  create-task.sh 35 100 "Investigate cache stampede" --priority=high --desc="Observed on 2026-04-16"

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

BOARD_ID="$1"
STAGE_ID="$2"
TITLE="$3"

if ! [[ "$BOARD_ID" =~ ^[0-9]+$ ]]; then fb_error "board_id must be integer"; exit 1; fi
if ! [[ "$STAGE_ID" =~ ^[0-9]+$ ]]; then fb_error "stage_id must be integer"; exit 1; fi
if [ -n "$PRIORITY" ]; then
  case "$PRIORITY" in low|medium|high) ;; *) fb_error "priority must be low|medium|high"; exit 1 ;; esac
fi
if [ -n "$CRM_CONTACT" ] && ! [[ "$CRM_CONTACT" =~ ^[0-9]+$ ]]; then
  fb_error "--crm-contact must be integer"; exit 1
fi

fb_require_credentials

TITLE_JSON=$(fb_json_escape "$TITLE")
BODY="{\"task\":{\"title\":${TITLE_JSON},\"board_id\":${BOARD_ID},\"stage_id\":${STAGE_ID}"
if [ -n "$PRIORITY" ]; then
  BODY="$BODY,\"priority\":\"${PRIORITY}\""
fi
if [ -n "$DESC" ]; then
  DESC_JSON=$(fb_json_escape "$DESC")
  BODY="$BODY,\"description\":${DESC_JSON}"
fi
if [ -n "$CRM_CONTACT" ]; then
  BODY="$BODY,\"crm_contact_id\":${CRM_CONTACT}"
fi
BODY="$BODY}}"

RESPONSE=$(fb_curl POST "/projects/${BOARD_ID}/tasks" "$BODY") || exit $?
printf '%s\n' "$RESPONSE"

# Move the new card to position 0 (top of stage) since the API always appends to the bottom.
TASK_ID=$(fb_json_get "$RESPONSE" "task.id")
[ -z "$TASK_ID" ] && TASK_ID=$(fb_json_get "$RESPONSE" "id")
if [ -n "$TASK_ID" ] && [[ "$TASK_ID" =~ ^[0-9]+$ ]]; then
  fb_curl PUT "/projects/${BOARD_ID}/tasks/${TASK_ID}/move-task" \
    "{\"newStageId\":${STAGE_ID},\"newIndex\":0}" >/dev/null
fi
