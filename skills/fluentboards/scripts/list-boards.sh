#!/bin/bash
# List Fluentboards boards (paginated, searchable).
#
# Usage:
#   list-boards.sh [--page=N] [--per-page=N] [--search=TEXT] [--type=to-do|roadmap]
#
# Exit: 0 success, 1 bad usage, 3 HTTP/network error.

set -uo pipefail
LIB="$(cd "$(dirname "$0")/lib" && pwd)"
source "$LIB/auth.sh"
source "$LIB/http.sh"

fb_strip_globals "$@"
set -- ${FB_ARGV[@]+"${FB_ARGV[@]}"}

PAGE=""; PER_PAGE=""; SEARCH=""; TYPE=""
for a in "$@"; do
  case "$a" in
    --page=*)     PAGE="${a#--page=}" ;;
    --per-page=*) PER_PAGE="${a#--per-page=}" ;;
    --search=*)   SEARCH="${a#--search=}" ;;
    --type=*)     TYPE="${a#--type=}" ;;
    *) fb_error "unknown arg: $a"; exit 1 ;;
  esac
done

fb_require_credentials

# URL-encode a string (for ?search=). Plus-encoding is good enough for text.
urlencode() {
  local s="$1" out="" i c
  for (( i=0; i<${#s}; i++ )); do
    c="${s:i:1}"
    case "$c" in
      [A-Za-z0-9._~-]) out+="$c" ;;
      ' ') out+="+" ;;
      *) out+=$(printf '%%%02X' "'$c") ;;
    esac
  done
  printf '%s' "$out"
}

QUERY=""; SEP="?"
if [ -n "$PAGE" ];     then QUERY="${QUERY}${SEP}page=${PAGE}";                  SEP="&"; fi
if [ -n "$PER_PAGE" ]; then QUERY="${QUERY}${SEP}per_page=${PER_PAGE}";          SEP="&"; fi
if [ -n "$SEARCH" ];   then QUERY="${QUERY}${SEP}search=$(urlencode "$SEARCH")"; SEP="&"; fi
if [ -n "$TYPE" ];     then QUERY="${QUERY}${SEP}type=${TYPE}";                  SEP="&"; fi

fb_curl GET "/projects${QUERY}"
