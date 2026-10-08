#!/usr/bin/env bash
# Clear a failed delivery record so the next `sb host apply` is admitted again.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
  cat <<'EOF'
Usage: retire-failed.sh --project-dir DIR --environment ENV --remote NAME
                        (--original-request-id REQ | --job-id JOB) --confirm

After a failed durable apply, the next apply refuses with required_evidence_missing
or operation_busy. This runs:
  sb host retire-delivery --project-dir DIR --environment ENV --remote NAME \
    --original-request-id REQ --confirm --json
With --job-id the request id is read from `sb job-status JOB --json`.

Only retire a delivery whose job has ended (failed, timed_out, cancelled,
interrupted). Read `sb job-output JOB` first: the record is the evidence of what failed.

Options:
  --dry-run   print the commands, run nothing
  --confirm   required
  --yes       skip the interactive prompt
  -h, --help  this help
EOF
}

PROJECT_DIR="" ENVIRONMENT="" REMOTE="" REQUEST_ID="" JOB_ID=""
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --project-dir) require_value "$1" "${2:-}"; PROJECT_DIR=$2; shift 2 ;;
    --environment) require_value "$1" "${2:-}"; ENVIRONMENT=$2; shift 2 ;;
    --remote) require_value "$1" "${2:-}"; REMOTE=$2; shift 2 ;;
    --original-request-id) require_value "$1" "${2:-}"; REQUEST_ID=$2; shift 2 ;;
    --job-id) require_value "$1" "${2:-}"; JOB_ID=$2; shift 2 ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
require_set --project-dir "$PROJECT_DIR"
require_set --environment "$ENVIRONMENT"
require_set --remote "$REMOTE"
[ -n "$REQUEST_ID" ] || [ -n "$JOB_ID" ] || usage_error "give --original-request-id or --job-id"

if [ -z "$REQUEST_ID" ]; then
  if [ "$DRY_RUN" = 1 ]; then
    run "$SB" job-status "$JOB_ID" --json
    REQUEST_ID="<request_id from job-status>"
  else
    status_json=$("$SB" job-status "$JOB_ID" --json) || die "sb job-status $JOB_ID failed"
    lifecycle=$(printf '%s' "$status_json" | json_field lifecycle)
    case $lifecycle in
      failed | timed_out | cancelled | interrupted) ;;
      *) die "job $JOB_ID is '${lifecycle:-unknown}', not ended; refusing to retire a live delivery" ;;
    esac
    REQUEST_ID=$(printf '%s' "$status_json" | json_field request_id)
    [ -n "$REQUEST_ID" ] || die "job $JOB_ID has no request_id"
  fi
fi

confirm_or_die "retire delivery $REQUEST_ID for $ENVIRONMENT on $REMOTE"
if [ "$DRY_RUN" = 1 ]; then
  run "$SB" host retire-delivery --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" \
    --remote "$REMOTE" --original-request-id "$REQUEST_ID" --confirm --json
  exit 0
fi
set +e
out=$("$SB" host retire-delivery --project-dir "$PROJECT_DIR" --environment "$ENVIRONMENT" \
  --remote "$REMOTE" --original-request-id "$REQUEST_ID" --confirm --json 2>&1)
rc=$?
set -e
printf '%s\n' "$out"
case $out in
  *delivery_terminal_conflict*)
    # Retired once already. The retire that clears a stale staged revision only runs on
    # a fresh record, so let the next apply refuse, then retire that request instead.
    die "delivery $REQUEST_ID was already retired; if the next apply refuses with unproven_staged_revision, retire that apply's request id" ;;
esac
exit $rc
