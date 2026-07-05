#!/bin/bash
# One-shot "everything about this card" — fetches task + comments + subtasks +
# labels + activities and emits labelled JSON sections.
#
# Usage:
#   get-card.sh <url-or-id>
#
# Stdout:
#   ## task
#   {…}
#   ## comments
#   {…}
#   ## subtasks
#   {…}
#   ## labels
#   [...]
#   ## activities
#   {…}
#
# Each section's body is the raw API response JSON. Sections where the call
# fails are still emitted with a one-line error note so downstream tooling can
# tell they were attempted.
#
# Exit:
#   0 = task fetch succeeded (comments/subtasks/labels/activities may still have failed individually)
#   1 = bad usage / missing credentials
#   2 = resolve failure
#   3 = the task fetch itself failed (HTTP/network)

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

if [ $# -lt 1 ]; then
  cat >&2 <<'USAGE'
Usage: get-card.sh <url-or-id>

Emits a labelled dump of everything about a task: task, comments, subtasks,
labels, activities. Each section header is "## <name>" on its own line,
followed by the raw JSON response.

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

fb_require_credentials
fb_resolve "$1"

B="$FB_BOARD_ID"
T="$FB_TASK_ID"

emit_section() {
  local name="$1" path="$2" body rc
  printf '## %s\n' "$name"
  body=$(fb_curl GET "$path" 2>/dev/null)
  rc=$?
  if [ $rc -eq 0 ]; then
    printf '%s\n\n' "$body"
  else
    printf '{"_skill_error": "HTTP %s on %s"}\n\n' "${FB_HTTP_STATUS:-?}" "$path"
    fb_warn "section '$name' failed (HTTP ${FB_HTTP_STATUS:-?}); continuing"
  fi
}

# Task is the primary payload — if it fails, the whole command fails.
printf '## task\n'
if ! fb_curl GET "/projects/${B}/tasks/${T}"; then
  printf '\n'
  exit 3
fi
printf '\n'

emit_section "comments"   "/projects/${B}/tasks/${T}/comments?per_page=50"
emit_section "subtasks"   "/projects/${B}/tasks/${T}/subtasks"
emit_section "labels"     "/projects/${B}/tasks/${T}/labels"
emit_section "activities" "/projects/${B}/tasks/${T}/activities?per_page=50"
emit_section "attachments" "/projects/${B}/tasks/${T}/attachment"
