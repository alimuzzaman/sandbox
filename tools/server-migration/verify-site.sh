#!/usr/bin/env bash
# Check a migrated site answers with the expected status (and revision header).
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
  cat <<'USAGE'
Usage: verify-site.sh --url URL [options]

Read-only. Requests URL with curl until it answers with the expected status (and, if
asked, the expected revision header) or the retry budget runs out.

Options:
  --expect-status CODE      expected HTTP status (default 200)
  --revision-header NAME    response header that carries the deployed revision
  --expect-revision VALUE   required value (prefix match, so a short SHA works)
  --resolve-ip IP           connect to IP instead of resolving the hostname. Use it to
                            test the new server before DNS moves, or when the local
                            resolver (macOS mDNSResponder) still caches the old IP.
  --basic-auth-env VAR      read "user:password" for Basic Auth from environment VAR
                            (the value is never printed)
  --attempts N              tries before giving up (default 10)
  --interval SECONDS        wait between tries (default 15)
  --timeout SECONDS         per-request timeout (default 20)
  --dry-run                 print the command, run nothing
  -h, --help                this help
USAGE
}

URL="" STATUS=200 HEADER="" REVISION="" RESOLVE_IP="" AUTH_ENV="" ATTEMPTS=10 INTERVAL=15 TIMEOUT=20
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --url) require_value "$1" "${2:-}"; URL=$2; shift 2 ;;
    --expect-status) require_value "$1" "${2:-}"; STATUS=$2; shift 2 ;;
    --revision-header) require_value "$1" "${2:-}"; HEADER=$2; shift 2 ;;
    --expect-revision) require_value "$1" "${2:-}"; REVISION=$2; shift 2 ;;
    --resolve-ip) require_value "$1" "${2:-}"; RESOLVE_IP=$2; shift 2 ;;
    --basic-auth-env) require_value "$1" "${2:-}"; AUTH_ENV=$2; shift 2 ;;
    --attempts) require_value "$1" "${2:-}"; ATTEMPTS=$2; shift 2 ;;
    --interval) require_value "$1" "${2:-}"; INTERVAL=$2; shift 2 ;;
    --timeout) require_value "$1" "${2:-}"; TIMEOUT=$2; shift 2 ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
require_set --url "$URL"
case $URL in http://* | https://*) ;; *) usage_error "--url must start with http:// or https://" ;; esac
[ -z "$REVISION" ] || require_set --revision-header "$HEADER"
for n in "$ATTEMPTS" "$INTERVAL" "$TIMEOUT"; do is_positive_int "$n" || usage_error "numbers must be positive integers"; done

CURL=(curl -sS -o /dev/null -D - --max-time "$TIMEOUT" -w '%{http_code}\n')
if [ -n "$RESOLVE_IP" ]; then
  hostport=${URL#*://}; hostport=${hostport%%/*}
  host=${hostport%%:*}
  port=${hostport#"$host"}; port=${port#:}
  [ -n "$port" ] || { case $URL in https://*) port=443 ;; *) port=80 ;; esac; }
  CURL+=(--resolve "$host:$port:$RESOLVE_IP")
fi
if [ -n "$AUTH_ENV" ]; then
  check_name --basic-auth-env "$AUTH_ENV"
  [ "$DRY_RUN" = 1 ] || [ -n "${!AUTH_ENV:-}" ] || die "environment variable $AUTH_ENV is empty"
fi

if [ "$DRY_RUN" = 1 ]; then
  if [ -n "$AUTH_ENV" ]; then
    printf '+ %s --user "$%s" %s\n' "$(quote_argv "${CURL[@]}")" "$AUTH_ENV" "$(shq "$URL")"
  else
    run "${CURL[@]}" "$URL"
  fi
  printf '#   up to %s attempts, %ss apart; expect status %s%s\n' "$ATTEMPTS" "$INTERVAL" "$STATUS" \
    "${REVISION:+ and $HEADER: $REVISION*}"
  exit 0
fi

check_once() {
  local out code got
  if [ -n "$AUTH_ENV" ]; then
    # --user via a config on stdin keeps the credential out of the process list.
    local cred
    cred=$(printf '%s' "${!AUTH_ENV}" | sed 's/[\\"]/\\&/g')
    out=$(printf 'user = "%s"\n' "$cred" | "${CURL[@]}" -K - "$URL" 2>&1) || true
  else
    out=$("${CURL[@]}" "$URL" 2>&1) || true
  fi
  code=$(printf '%s\n' "$out" | tail -n 1 | tr -d '\r')
  if [ "$code" != "$STATUS" ]; then
    log "$URL -> ${code:-no response} (want $STATUS)"
    return 1
  fi
  if [ -n "$HEADER" ]; then
    got=$(printf '%s\n' "$out" | tr -d '\r' | awk -v h="$HEADER" 'BEGIN{h=tolower(h)":"} tolower($1)==h {print $2}' | tail -n 1)
    log "$URL -> $code, $HEADER: ${got:-<missing>}"
    if [ -n "$REVISION" ]; then
      case $got in "$REVISION"*) ;; *) return 1 ;; esac
    fi
  else
    log "$URL -> $code"
  fi
}

wait_until $(((ATTEMPTS - 1) * INTERVAL)) "$INTERVAL" "verify $URL" check_once \
  || die "$URL did not pass verification"
