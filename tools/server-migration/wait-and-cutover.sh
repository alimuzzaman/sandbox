#!/usr/bin/env bash
# Wait for a deploy job to succeed, then run the data cutover. Never earlier.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$HERE/lib.sh"

usage() {
  cat <<'USAGE'
Usage: wait-and-cutover.sh --job-id JOB [--poll-timeout S] [--poll-interval S]
                           -- CUTOVER-ARGS...

Polls `sb job-status JOB --json` every --poll-interval seconds (default 5) and reads
`lifecycle` and `exit_code`. While the job is running, queued, accepted or cancelling it
keeps waiting. The cutover (cutover-compose-data.sh with CUTOVER-ARGS) runs only when
lifecycle is `succeeded` AND exit_code is 0. failed, cancelled, timed_out, interrupted,
a non-zero exit code, or a wait that runs past its bound exits without touching data.
The wait is bounded by the job's own deadline (deadline_seconds from started_at) plus
JOB_GRACE_SECONDS (default 120), unless --poll-timeout is given.

Why wait for `succeeded` and not for "the site answers": `sb host apply` finishes by
verifying the public URL on the new server. Stopping the web during that window fails
the verification and apply rolls DNS (and the nginx include) back to the old state.

The old server keeps serving while the new one builds, so starting this right after
deploy-project.sh (or right after `sb job-start`) keeps downtime to the cutover itself.
Start `caffeinate -i -t SECONDS` alongside (done here unless --no-caffeinate).

Options:
  --job-id JOB             the durable deploy job from sb job-start
  --poll-timeout SECONDS   give up after this long (default 0: the job deadline + grace;
                           JOB_FALLBACK_SECONDS=7200 when the job reports none)
  --poll-interval SECONDS  seconds between polls (default 5)
  --no-caffeinate          do not start caffeinate (macOS)
  --dry-run                print the commands, run nothing (passed on to the cutover)
  --confirm, --yes         passed on to the cutover
  -h, --help               this help

Exit status: cutover's status after success; 1 if the job ended otherwise; 3 if the poll
budget ran out (nothing was changed).
USAGE
}

JOB_ID="" POLL_TIMEOUT=0 POLL_INTERVAL=5 CAFFEINATE=1
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
is_positive_int "$POLL_INTERVAL" || usage_error "--poll-interval must be a positive integer"
case $POLL_TIMEOUT in '' | *[!0-9]*) usage_error "--poll-timeout must be a whole number of seconds" ;; esac
[ $# -gt 0 ] || usage_error "give the cutover-compose-data.sh arguments after --"

CUTOVER=("$HERE/cutover-compose-data.sh")
[ "$DRY_RUN" = 0 ] || CUTOVER+=(--dry-run)
[ "$CONFIRM" = 0 ] || CUTOVER+=(--confirm)
[ "$ASSUME_YES" = 0 ] || CUTOVER+=(--yes)
CUTOVER+=("$@")

if [ "$DRY_RUN" = 1 ]; then
  run "$SB" job-status "$JOB_ID" --json
  printf '#   polled every %ss, bounded by %s; the cutover runs only on lifecycle=succeeded with exit_code 0\n' \
    "$POLL_INTERVAL" "$([ "$POLL_TIMEOUT" -gt 0 ] && echo "${POLL_TIMEOUT}s" || echo "the job deadline + ${JOB_GRACE_SECONDS}s")"
  exec "${CUTOVER[@]}"
fi

# Fail fast on missing --confirm before waiting possibly hours.
[ "$CONFIRM" = 1 ] || die "refusing: the cutover is destructive; re-run with --confirm (or --dry-run to review)"

if [ "$CAFFEINATE" = 1 ] && command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -t "$([ "$POLL_TIMEOUT" -gt 0 ] && echo "$POLL_TIMEOUT" || echo "$JOB_FALLBACK_SECONDS")" &
  CAFF_PID=$!
  trap 'kill "$CAFF_PID" 2>/dev/null || true' EXIT
fi

status=0
poll_job "$JOB_ID" "$POLL_INTERVAL" "$POLL_TIMEOUT" || status=$?
if [ "$status" = 3 ]; then
  printf 'job %s still %s when the wait bound ran out; no cutover was run.\n' "$JOB_ID" "${JOB_LIFECYCLE:-unknown}" >&2
  exit 3
fi
if job_succeeded; then
  log "deploy job succeeded (exit_code 0); starting the data cutover"
  status=0
  "${CUTOVER[@]}" || status=$?
  exit "$status"
fi
die "job $JOB_ID ended ${JOB_LIFECYCLE:-unknown} with exit_code ${JOB_EXIT:-none}; no cutover. Read: $SB job-output $JOB_ID --lines 200"
