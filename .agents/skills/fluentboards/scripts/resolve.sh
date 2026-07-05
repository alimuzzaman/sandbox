#!/bin/bash
# Resolve a Fluentboards URL or task ID to "board_id<TAB>task_id".
#
# Accepts:
#   - Full wp-admin URL with fragment (#/boards/B/tasks/T-slug)
#   - Short URL (.../fbs-T, with optional double slash)
#   - "B/T"
#   - Bare task ID
#
# Usage:
#   resolve.sh <url-or-id>
#
# Stdout: "BOARD_ID\tTASK_ID\n"
# Exit:   0 success, 1 bad usage, 2 resolve failure, 3 network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
# shellcheck source=lib/auth.sh
source "$LIB/auth.sh"
# shellcheck source=lib/http.sh
source "$LIB/http.sh"
# shellcheck source=lib/resolve.sh
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

if [ $# -lt 1 ]; then
  cat >&2 <<'USAGE'
Usage: resolve.sh <url-or-id>

Prints "BOARD_ID<TAB>TASK_ID" on success.

Accepted inputs:
  - Full URL:    https://site/wp-admin/admin.php?page=fluent-boards#/boards/35/tasks/80927-slug
  - Short URL:   https://site/fbs-80927       (double slash //fbs-80927 also works)
  - B/T form:    35/80927
  - Bare task:   80927

Env: FB_RESOLVE_NO_ITERATE=1 disables the slow "scan all boards" fallback.
Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

fb_require_credentials
fb_resolve "$1"
printf '%s\t%s\n' "$FB_BOARD_ID" "$FB_TASK_ID"
