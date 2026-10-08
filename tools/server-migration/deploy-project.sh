#!/usr/bin/env bash
# Phase 4: deploy one project environment to the new remote through a durable job.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
  cat <<'EOF'
Usage: deploy-project.sh --project-dir DIR --environment ENV --remote NAME [options]

Preflight, then `sb host plan`, then `sb host apply --confirm`. When apply refuses with
recovery_context_required it prints a `sb job-start ... -- sb host apply ...` command;
this script runs that command (with --timeout raised to --job-timeout), keeps the Mac
awake with caffeinate, and polls the job: every --poll-interval seconds, reading
lifecycle and exit_code, bounded by the job's deadline. Success means lifecycle
succeeded with exit_code 0.

Preflight (skip with --skip-preflight):
  * `sb remote service status NAME --json` must report runtime_revision_state=match.
    Every local Sandbox commit changes the runtime revision; fix it with
    `sb remote up NAME --confirm` (or --fix-runtime). That replaces the runtime for
    every checkout using the remote, including deploy scripts pinned to a revision.
  * with --ssh TARGET: the remote's `docker compose build --help` must list
    --with-dependencies (Compose 2.24+). Sandbox builds depends_on services with it.

Fence clearing (--clear-fence). After a failed attempt that was already retired
(delivery_terminal_conflict), the staged revision can survive and the next apply
refuses fast with unproven_staged_revision. With --clear-fence the script then:
  1. retires THAT refused request id (this retire clears staged_revision),
  2. waits (bounded) until `sb host status --json` shows staged_revision null,
  3. runs the apply once more.
Without --clear-fence it prints those steps.

Required:
  --project-dir DIR      clean checkout (a worktree) of the branch the environment allows
  --environment ENV      environment name from the project's hosting manifest
  --remote NAME          the NEW Sandbox remote

Options:
  --job-timeout SECONDS  --timeout given to sb job-start (default 900; Lenzora needed 5400)
  --poll-timeout SECONDS stop polling after this long (default: job-timeout + 300)
  --poll-interval SECONDS seconds between job-status polls (default 5)
  --ssh TARGET           ssh target of the remote host, for the Compose preflight
  --skip-preflight       skip both preflight checks
  --fix-runtime          on a runtime revision mismatch run `sb remote up NAME --confirm`
                         (waiting out remote_registration_busy), then continue
  --busy-timeout SECONDS how long --fix-runtime waits for a registry lock holder (default 1800)
  --clear-fence          run the fence-clearing steps above once (needs --confirm)
  --fence-timeout SECONDS how long to wait for staged_revision to read null (default 120)
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

PROJECT_DIR="" ENVIRONMENT="" REMOTE="" JOB_TIMEOUT=900 POLL_TIMEOUT="" POLL_INTERVAL=5
FIX_RUNTIME=0 BUSY_TIMEOUT=1800 CAFFEINATE=1 PLAN_ONLY=0 SSH_TARGET="" PREFLIGHT=1 CLEAR_FENCE=0 FENCE_TIMEOUT=120
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
    --ssh) require_value "$1" "${2:-}"; SSH_TARGET=$2; shift 2 ;;
    --skip-preflight) PREFLIGHT=0; shift ;;
    --fix-runtime) FIX_RUNTIME=1; shift ;;
    --clear-fence) CLEAR_FENCE=1; shift ;;
    --fence-timeout) require_value "$1" "${2:-}"; FENCE_TIMEOUT=$2; shift 2 ;;
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
for n in "$JOB_TIMEOUT" "$POLL_INTERVAL" "$BUSY_TIMEOUT" "$FENCE_TIMEOUT"; do
  is_positive_int "$n" || usage_error "timeouts and intervals must be positive integers"
done
[ -n "$POLL_TIMEOUT" ] || POLL_TIMEOUT=$((JOB_TIMEOUT + 300))
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

fence_steps() { # request-id
  cat <<EOF

The target is fenced by an unproven staged revision. Clear it (or re-run with --clear-fence):
  1. $(quote_argv "$MIGRATION_LIB_DIR/retire-failed.sh" --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" --remote "$REMOTE" --original-request-id "${1:-<this refused request id>}" --confirm)
  2. $(quote_argv "$SB" host status --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" --remote "$REMOTE" --json)
     must show "staged_revision": null
  3. run this script again
Retiring an OLDER request returns delivery_terminal_conflict and does not clear the fence.
EOF
}

# shellcheck disable=SC2329 # invoked through wait_until
remote_up_once() {
  local out status=0
  out=$("$SB" remote up "$REMOTE" --confirm 2>&1) || status=$?
  printf '%s\n' "$out" >&2
  if [ "$status" != 0 ] && printf '%s' "$out" | grep -q remote_registration_busy; then
    log "remote_registration_busy: another host apply holds ~/sandbox/runtime/remote-registration/registry.lock; waiting (find it with lsof; never kill it)"
  fi
  return "$status"
}

fix_runtime() {
  log "pinned checkouts: \`sb remote up\` replaces the runtime revision every checkout using $REMOTE runs against"
  wait_until "$BUSY_TIMEOUT" 60 "sb remote up $REMOTE" remote_up_once \
    || die "sb remote up $REMOTE did not succeed within ${BUSY_TIMEOUT}s"
}

# --- preflight ---------------------------------------------------------------
preflight() {
  if [ "$DRY_RUN" = 1 ]; then
    run "$SB" remote service status "$REMOTE" --json
    printf '#   requires data.runtime_revision_state = match (else: sb remote up %s --confirm)\n' "$REMOTE"
    if [ -n "$SSH_TARGET" ]; then
      remote_bash "$SSH_TARGET" "$DOCKER compose build --help | grep -q -- --with-dependencies"
    fi
    return 0
  fi
  local state
  state=$("$SB" remote service status "$REMOTE" --json 2>/dev/null | "$PYTHON" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    d = {}
print(((d.get("data") or {}).get("runtime_revision_state")) or "unknown")' || true)
  case $state in
    match) log "preflight: remote runtime revision matches this checkout" ;;
    mismatch)
      if [ "$FIX_RUNTIME" = 1 ]; then fix_runtime
      else die "remote_runtime_revision_mismatch: run \`$SB remote up $REMOTE --confirm\` (positional name, not --remote) or re-run with --fix-runtime"; fi ;;
    *) die "preflight: could not read the remote runtime revision (sb remote service status $REMOTE --json said '${state}'); retry, or --skip-preflight" ;;
  esac
  if [ -n "$SSH_TARGET" ]; then
    remote_bash "$SSH_TARGET" "$DOCKER compose build --help | grep -q -- --with-dependencies" \
      || die "preflight: docker compose on $SSH_TARGET lacks 'build --with-dependencies' (needs Compose 2.24+); upgrade docker-compose-plugin first"
    log "preflight: docker compose supports build --with-dependencies"
  else
    warn "preflight: Compose version not checked (give --ssh TARGET); Sandbox needs Compose 2.24+ for build --with-dependencies"
  fi
}

[ "$PREFLIGHT" = 0 ] || preflight
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
  printf '#   polled every %ss for up to %ss; success = lifecycle succeeded and exit_code 0\n' "$POLL_INTERVAL" "$POLL_TIMEOUT"
  printf '#   on failure: sb job-output <job-id> --lines 200\n'
  retire_hint
  [ "$CLEAR_FENCE" = 0 ] || fence_steps
  exit 0
fi

CAFF_PID=""
if [ "$CAFFEINATE" = 1 ] && command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -t "$((POLL_TIMEOUT * 2))" &
  CAFF_PID=$!
  trap '[ -z "$CAFF_PID" ] || kill "$CAFF_PID" 2>/dev/null || true' EXIT
fi

APPLY_OUT="" JOB_ID="" JOB_OUTPUT=""

apply_once() { # sets APPLY_OUT, returns apply's exit status
  local status=0
  log "+ $(quote_argv "${APPLY[@]}")"
  APPLY_OUT=$("${APPLY[@]}" 2>&1) || status=$?
  printf '%s\n' "$APPLY_OUT" >&2
  return "$status"
}

# One deploy attempt. Returns 0 success, 1 failure, 3 poll budget exhausted.
attempt() {
  local status=0 prepare_line job_json
  JOB_ID="" JOB_OUTPUT="" JOB_REQUEST=""
  apply_once || status=$?
  if [ "$status" != 0 ] && printf '%s' "$APPLY_OUT" | grep -q remote_runtime_revision_mismatch; then
    [ "$FIX_RUNTIME" = 1 ] || die "remote_runtime_revision_mismatch: run \`$SB remote up $REMOTE --confirm\` (positional name, not --remote) or re-run with --fix-runtime"
    fix_runtime
    status=0
    apply_once || status=$?
  fi
  if [ "$status" = 0 ]; then
    log "host apply finished directly (no durable job was needed)"
    return 0
  fi
  case $APPLY_OUT in
    *recovery_context_required*) ;;
    *unproven_staged_revision*) JOB_OUTPUT=$APPLY_OUT; return 1 ;;
    *required_evidence_missing* | *operation_busy*)
      printf '%s\n' "A previous failed delivery is still recorded for this target." >&2
      retire_hint >&2; exit 1 ;;
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

  log "+ $(quote_argv "${JOB_ARGV[@]}")"
  job_json=$("${JOB_ARGV[@]}") || die "sb job-start was refused: $job_json"
  JOB_ID=$(printf '%s' "$job_json" | tail -n 1 | json_field job_id)
  [ -n "$JOB_ID" ] || die "no job_id in job-start output: $job_json"
  log "job $JOB_ID accepted; polling every ${POLL_INTERVAL}s for up to ${POLL_TIMEOUT}s"

  status=0
  poll_job "$JOB_ID" "$POLL_INTERVAL" "$POLL_TIMEOUT" || status=$?
  if [ "$status" = 3 ]; then
    printf 'job %s still %s after %ss; it keeps running. Poll: %s job-status %s --json\n' \
      "$JOB_ID" "${JOB_LIFECYCLE:-unknown}" "$POLL_TIMEOUT" "$SB" "$JOB_ID" >&2
    return 3
  fi
  if job_succeeded; then
    log "deploy of $ENVIRONMENT to $REMOTE succeeded (job $JOB_ID)"
    return 0
  fi
  JOB_OUTPUT=$("$SB" job-output "$JOB_ID" --lines 200 2>&1 || true)
  printf '%s\n' "$JOB_OUTPUT" >&2
  printf 'job %s ended %s (exit_code %s).\n' "$JOB_ID" "$JOB_LIFECYCLE" "${JOB_EXIT:--}" >&2
  return 1
}

# shellcheck disable=SC2329 # invoked through wait_until
staged_revision_cleared() {
  "$SB" host status --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" \
    --remote "$REMOTE" --json 2>/dev/null | "$PYTHON" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
sys.exit(0 if isinstance(d, dict) and "staged_revision" in d and d["staged_revision"] is None else 1)'
}

status=0
attempt || status=$?
if [ "$status" = 1 ] && printf '%s' "$JOB_OUTPUT" | grep -q unproven_staged_revision; then
  if [ "$CLEAR_FENCE" = 0 ] || [ -z "$JOB_REQUEST" ]; then
    fence_steps "$JOB_REQUEST" >&2
    exit 1
  fi
  log "fence: unproven_staged_revision; retiring the refused request $JOB_REQUEST"
  "$MIGRATION_LIB_DIR/retire-failed.sh" --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" \
    --remote "$REMOTE" --original-request-id "$JOB_REQUEST" --confirm --yes \
    || die "retiring $JOB_REQUEST failed; the fence is still in place"
  wait_until "$FENCE_TIMEOUT" 10 "staged_revision cleared" staged_revision_cleared \
    || die "sb host status still shows a staged_revision after retiring $JOB_REQUEST"
  log "fence cleared; applying again"
  status=0
  attempt || status=$?
fi
case $status in
  0) exit 0 ;;
  3) exit 3 ;;
  *)
    if printf '%s' "$JOB_OUTPUT" | grep -q unproven_staged_revision; then
      fence_steps "$JOB_REQUEST" >&2
    else
      retire_hint "$JOB_REQUEST" >&2
    fi
    exit 1 ;;
esac
