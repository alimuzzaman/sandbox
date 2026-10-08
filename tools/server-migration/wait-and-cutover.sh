#!/usr/bin/env bash
# Wait for a deploy job to succeed, then run the data cutover. Never earlier.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$HERE/lib.sh"

usage() {
  cat <<'USAGE'
Usage: wait-and-cutover.sh --job-id JOB [--poll-timeout S] [--poll-interval S]
                           -- CUTOVER-ARGS...

Polls `sb job-status JOB --json` with a bound. Only when the deploy job's lifecycle is
`succeeded` does it run cutover-compose-data.sh with CUTOVER-ARGS. Any other ending
(failed, timed_out, cancelled, interrupted) or an exhausted poll budget exits without
touching data.

Why wait for `succeeded` and not for "the site answers": `sb host apply` finishes by
verifying the public URL on the new server. Stopping the web during that window fails
the verification and apply rolls DNS (and the nginx include) back to the old state.

The old server keeps serving while the new one builds, so starting this right after
deploy-project.sh (or right after `sb job-start`) keeps downtime to the cutover itself.
Start `caffeinate -i -t SECONDS` alongside (done here unless --no-caffeinate).

Options:
  --job-id JOB             the durable deploy job from sb job-start
  --poll-timeout SECONDS   give up after this long (default 7200)
  --poll-interval SECONDS  seconds between polls (default 30)
  --no-caffeinate          do not start caffeinate (macOS)
  --dry-run                print the commands, run nothing (passed on to the cutover)
  --confirm, --yes         passed on to the cutover
  -h, --help               this help

Exit status: cutover's status after success; 1 if the job ended otherwise; 3 if the poll
budget ran out (nothing was changed).
USAGE
}

JOB_ID="" POLL_TIMEOUT=7200 POLL_INTERVAL=30 CAFFEINATE=1
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --job-id) require_value "$1" "${2:-}"; JOB_ID=$2; shift 2 ;;
    --poll-timeout) require_value "$1" "${2:-}"; POLL_TIMEOUT=$2; shift 2 ;;
    --poll-interval) require_value "$1" "${2:-}"; POLL_INTERVAL=$2; shift 2 ;;
    --no-caffeinate) CAFFEINATE=0; shift ;;
    --) shift; break ;;
    *) usage_error "unknown argument: $1 (cutover arguments go after --)" ;;
  esac
done
require_set --job-id "$JOB_ID"
for n in "$POLL_TIMEOUT" "$POLL_INTERVAL"; do
  is_positive_int "$n" || usage_error "timeouts must be positive integers"
done
[ $# -gt 0 ] || usage_error "give the cutover-compose-data.sh arguments after --"

CUTOVER=("$HERE/cutover-compose-data.sh")
[ "$DRY_RUN" = 0 ] || CUTOVER+=(--dry-run)
[ "$CONFIRM" = 0 ] || CUTOVER+=(--confirm)
[ "$ASSUME_YES" = 0 ] || CUTOVER+=(--yes)
CUTOVER+=("$@")

if [ "$DRY_RUN" = 1 ]; then
  run "$SB" job-status "$JOB_ID" --json
  printf '#   polled every %ss for up to %ss; the cutover runs only on lifecycle=succeeded\n' "$POLL_INTERVAL" "$POLL_TIMEOUT"
  exec "${CUTOVER[@]}"
fi

# Fail fast on missing --confirm before waiting possibly hours.
[ "$CONFIRM" = 1 ] || die "refusing: the cutover is destructive; re-run with --confirm (or --dry-run to review)"

if [ "$CAFFEINATE" = 1 ] && command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -t "$POLL_TIMEOUT" &
  CAFF_PID=$!
  trap 'kill "$CAFF_PID" 2>/dev/null || true' EXIT
fi

deadline=$((SECONDS + POLL_TIMEOUT))
lifecycle="" health=""
while :; do
  status_json=$("$SB" job-status "$JOB_ID" --json 2>/dev/null || true)
  lifecycle=$(printf '%s' "$status_json" | json_field lifecycle)
  health=$(printf '%s' "$status_json" | json_field health)
  log "job $JOB_ID: ${lifecycle:-unknown} (${health:-?})"
  case $lifecycle in succeeded | failed | timed_out | cancelled | interrupted) break ;; esac
  [ $((SECONDS + POLL_INTERVAL)) -le "$deadline" ] || break
  sleep "$POLL_INTERVAL"
done

case $lifecycle in
  succeeded)
    log "deploy job succeeded; starting the data cutover"
    "${CUTOVER[@]}" ;;
  failed | timed_out | cancelled | interrupted)
    die "job $JOB_ID ended $lifecycle; no cutover. Read: $SB job-output $JOB_ID --lines 200" ;;
  *)
    printf 'job %s still %s after %ss; no cutover was run.\n' "$JOB_ID" "${lifecycle:-unknown}" "$POLL_TIMEOUT" >&2
    exit 3 ;;
esac
