#!/usr/bin/env bash
# Phase 4: deploy one project environment to the new remote through a durable job.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
  cat <<'EOF'
Usage: deploy-project.sh --project-dir DIR --environment ENV --remote NAME [options]

Runs `sb host plan`, then `sb host apply --confirm`. When apply refuses with
recovery_context_required it prints a `sb job-start ... -- sb host apply ...`
command; this script runs that command (with --timeout raised to --job-timeout),
keeps the Mac awake with caffeinate, and polls the job with a bounded loop.
On failure it prints the retire-failed.sh command that clears the failed delivery.

Required:
  --project-dir DIR      clean checkout (a worktree) of the branch the environment allows
  --environment ENV      environment name from the project's hosting manifest
  --remote NAME          the NEW Sandbox remote

Options:
  --job-timeout SECONDS  --timeout given to sb job-start (default 900; Lenzora needed 5400)
  --poll-timeout SECONDS stop polling after this long (default: job-timeout + 600)
  --poll-interval SECONDS seconds between job-status polls (default 30)
  --fix-runtime          on remote_runtime_revision_mismatch run `sb remote up NAME --confirm`
                         (waiting out remote_registration_busy) and retry apply once
  --busy-timeout SECONDS how long --fix-runtime waits for a registry lock holder (default 1800)
  --no-caffeinate        do not start caffeinate (macOS)
  --plan-only            stop after `sb host plan`
  --dry-run              print the commands, run nothing
  --confirm              required to run apply
  --yes                  skip the interactive prompt
  -h, --help             this help

Exit status: 0 job succeeded, 1 apply or job failed, 3 poll budget ran out while the
job was still running (it keeps running; poll it with `sb job-status ID`).
EOF
}

PROJECT_DIR="" ENVIRONMENT="" REMOTE="" JOB_TIMEOUT=900 POLL_TIMEOUT="" POLL_INTERVAL=30
FIX_RUNTIME=0 BUSY_TIMEOUT=1800 CAFFEINATE=1 PLAN_ONLY=0
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --project-dir) require_value "$1" "${2:-}"; PROJECT_DIR=$2; shift 2 ;;
    --environment) require_value "$1" "${2:-}"; ENVIRONMENT=$2; shift 2 ;;
    --remote) require_value "$1" "${2:-}"; REMOTE=$2; shift 2 ;;
    --job-timeout) require_value "$1" "${2:-}"; JOB_TIMEOUT=$2; shift 2 ;;
    --poll-timeout) require_value "$1" "${2:-}"; POLL_TIMEOUT=$2; shift 2 ;;
    --poll-interval) require_value "$1" "${2:-}"; POLL_INTERVAL=$2; shift 2 ;;
    --busy-timeout) require_value "$1" "${2:-}"; BUSY_TIMEOUT=$2; shift 2 ;;
    --fix-runtime) FIX_RUNTIME=1; shift ;;
    --no-caffeinate) CAFFEINATE=0; shift ;;
    --plan-only) PLAN_ONLY=1; shift ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
require_set --project-dir "$PROJECT_DIR"
require_set --environment "$ENVIRONMENT"
require_set --remote "$REMOTE"
check_name --environment "$ENVIRONMENT"
check_name --remote "$REMOTE"
for n in "$JOB_TIMEOUT" "$POLL_INTERVAL" "$BUSY_TIMEOUT"; do
  is_positive_int "$n" || usage_error "timeouts and intervals must be positive integers"
done
[ -n "$POLL_TIMEOUT" ] || POLL_TIMEOUT=$((JOB_TIMEOUT + 600))
is_positive_int "$POLL_TIMEOUT" || usage_error "--poll-timeout must be a positive integer"

if [ "$DRY_RUN" = 0 ]; then
  [ -d "$PROJECT_DIR" ] || die "--project-dir does not exist: $PROJECT_DIR"
  PROJECT_DIR=$(cd "$PROJECT_DIR" && pwd)
  if [ -n "$(git -C "$PROJECT_DIR" status --porcelain 2>/dev/null)" ]; then
    die "$PROJECT_DIR has uncommitted changes; host apply needs a clean checkout (use a fresh worktree)"
  fi
fi

retire_hint() { # request-id
  printf '\nThe failed delivery blocks the next apply (required_evidence_missing / operation_busy).\n'
  printf 'Review the job output, then clear it with:\n  %s\n' "$(quote_argv \
    "$MIGRATION_LIB_DIR/retire-failed.sh" --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" \
    --remote "$REMOTE" --original-request-id "${1:-<request-id from: sb job-status JOB --json>}" --confirm)"
}

run "$SB" host plan --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" --remote "$REMOTE"
[ "$PLAN_ONLY" = 0 ] || exit 0

confirm_or_die "apply $PROJECT_DIR ($ENVIRONMENT) to remote $REMOTE (builds, switches DNS to $REMOTE)"

APPLY=("$SB" host apply --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" --remote "$REMOTE" --confirm)

if [ "$DRY_RUN" = 1 ]; then
  run "${APPLY[@]}"
  printf '# apply refuses with recovery_context_required and prints a job-start command; it is run as:\n'
  run "$SB" job-start --json --local --project-dir "$PROJECT_DIR" --request-id '<request-id>' \
    --source-commit '<clean-HEAD>' --timeout "$JOB_TIMEOUT" -- "$SB" host apply --project-dir "$PROJECT_DIR" \
    --remote "$REMOTE" --environment "$ENVIRONMENT" --confirm
  [ "$CAFFEINATE" = 0 ] || run caffeinate -i -t "$POLL_TIMEOUT"
  run "$SB" job-status '<job-id>' --json
  printf '#   polled every %ss for up to %ss; on failure: sb job-output <job-id> --lines 200\n' "$POLL_INTERVAL" "$POLL_TIMEOUT"
  retire_hint
  exit 0
fi

apply_once() { # sets APPLY_OUT, returns apply's exit status
  local status=0
  log "+ $(quote_argv "${APPLY[@]}")"
  APPLY_OUT=$("${APPLY[@]}" 2>&1) || status=$?
  printf '%s\n' "$APPLY_OUT" >&2
  return "$status"
}

# shellcheck disable=SC2329 # invoked through wait_until
remote_up_once() {
  local out status=0
  out=$("$SB" remote up "$REMOTE" --confirm 2>&1) || status=$?
  printf '%s\n' "$out" >&2
  if [ "$status" != 0 ] && printf '%s' "$out" | grep -q remote_registration_busy; then
    log "remote_registration_busy: another host apply holds ~/sandbox/runtime/remote-registration/registry.lock; waiting (never kill it)"
  fi
  return "$status"
}

status=0
apply_once || status=$?
if [ "$status" != 0 ] && printf '%s' "$APPLY_OUT" | grep -q remote_runtime_revision_mismatch; then
  if [ "$FIX_RUNTIME" = 0 ]; then
    die "remote_runtime_revision_mismatch: run \`$SB remote up $REMOTE --confirm\` (positional name, not --remote) or re-run with --fix-runtime"
  fi
  wait_until "$BUSY_TIMEOUT" 60 "sb remote up $REMOTE" remote_up_once \
    || die "sb remote up $REMOTE did not succeed within ${BUSY_TIMEOUT}s"
  status=0
  apply_once || status=$?
fi
if [ "$status" = 0 ]; then
  log "host apply finished directly (no durable job was needed)"
  exit 0
fi
case $APPLY_OUT in
  *recovery_context_required*) ;;
  *required_evidence_missing* | *operation_busy*)
    printf '%s\n' "A previous failed delivery is still recorded for this target." >&2
    retire_hint; exit 1 ;;
  *) die "host apply failed (output above)" ;;
esac

prepare_line=$(printf '%s\n' "$APPLY_OUT" | sed -n 's/.*recovery_context_required; prepare with: //p' | head -n 1)
[ -n "$prepare_line" ] || die "apply said recovery_context_required but printed no job-start command"

# Split the printed command with shlex (never eval), raise --timeout, add --json.
JOB_ARGV=()
while IFS= read -r -d '' word; do JOB_ARGV+=("$word"); done < <("$PYTHON" -c '
import shlex, sys
argv = shlex.split(sys.argv[1])
timeout = sys.argv[2]
if len(argv) < 2 or argv[1] != "job-start" or "--" not in argv:
    sys.exit("unexpected prepare command: " + sys.argv[1])
sep = argv.index("--")
if argv[sep + 2:sep + 4] != ["host", "apply"]:
    sys.exit("prepare command does not wrap sb host apply")
head = argv[:sep]
if "--timeout" in head:
    head[head.index("--timeout") + 1] = timeout
else:
    head += ["--timeout", timeout]
head.insert(2, "--json")
sys.stdout.write("\0".join(head + argv[sep:]) + "\0")
' "$prepare_line" "$JOB_TIMEOUT")
[ "${#JOB_ARGV[@]}" -gt 0 ] || die "could not parse the job-start command"

CAFF_PID=""
if [ "$CAFFEINATE" = 1 ] && command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -t "$POLL_TIMEOUT" &
  CAFF_PID=$!
  trap '[ -z "$CAFF_PID" ] || kill "$CAFF_PID" 2>/dev/null || true' EXIT
fi

log "+ $(quote_argv "${JOB_ARGV[@]}")"
job_json=$("${JOB_ARGV[@]}") || die "sb job-start was refused: $job_json"
JOB_ID=$(printf '%s' "$job_json" | tail -n 1 | json_field job_id)
[ -n "$JOB_ID" ] || die "no job_id in job-start output: $job_json"
log "job $JOB_ID accepted; polling every ${POLL_INTERVAL}s for up to ${POLL_TIMEOUT}s"

deadline=$((SECONDS + POLL_TIMEOUT))
lifecycle="" status_json=""
while [ "$SECONDS" -lt "$deadline" ]; do
  status_json=$("$SB" job-status "$JOB_ID" --json 2>/dev/null || true)
  lifecycle=$(printf '%s' "$status_json" | json_field lifecycle)
  health=$(printf '%s' "$status_json" | json_field health)
  log "job $JOB_ID: ${lifecycle:-unknown} (${health:-?})"
  if [ "$health" = suspected_stalled ] && [ -z "${stall_hint:-}" ]; then
    stall_hint=1
    log "suspected_stalled is normal during a long silent image build or readiness wait; check the remote's ~/sandbox/runtime/hosts/<project>/<env>/apply.log and docker ps before acting. Do not cancel: a cancel can land after compose_recreate."
  fi
  case $lifecycle in succeeded | failed | timed_out | cancelled | interrupted) break ;; esac
  sleep "$POLL_INTERVAL"
done

case $lifecycle in
  succeeded)
    log "deploy of $ENVIRONMENT to $REMOTE succeeded (job $JOB_ID)"
    exit 0 ;;
  failed | timed_out | cancelled | interrupted)
    "$SB" job-output "$JOB_ID" --lines 200 >&2 || true
    request_id=$(printf '%s' "$status_json" | json_field request_id)
    printf 'job %s ended %s.\n' "$JOB_ID" "$lifecycle" >&2
    retire_hint "$request_id" >&2
    exit 1 ;;
  *)
    printf 'job %s still %s after %ss; it keeps running. Poll: %s job-status %s\n' \
      "$JOB_ID" "${lifecycle:-unknown}" "$POLL_TIMEOUT" "$SB" "$JOB_ID" >&2
    exit 3 ;;
esac
