#!/bin/bash
# Upload one or more files as attachments to a Fluentboards task.
#
# Usage:
#   upload-attachment.sh <url-or-id> <file> [<file2> ...]
#
# Exit: 0 success, 1 bad usage, 2 resolve failure, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"
source "$LIB/resolve.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

if [ $# -lt 2 ]; then
  cat >&2 <<'USAGE'
Usage: upload-attachment.sh <url-or-id> <file> [<file2> ...]

Uploads one or more files as task attachments. Accepts any file type the
Fluentboards server allows. Files are sent as multipart form data in the
file[] field.

Global flags: --verbose, --site=URL, --user=NAME
USAGE
  exit 1
fi

TARGET="$1"; shift

missing=()
for f in "$@"; do
  [ -f "$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -gt 0 ]; then
  fb_error "these files do not exist: ${missing[*]}"
  exit 1
fi

fb_require_credentials
fb_resolve "$TARGET"

fb_curl_multipart POST "/projects/${FB_BOARD_ID}/tasks/${FB_TASK_ID}/add-task-attachment-file" "file[]" "$@"
