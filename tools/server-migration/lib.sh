# shellcheck shell=bash
# Shared helpers for tools/server-migration/*.sh. Source it; do not execute it.
#
# Conventions every script follows:
#   --dry-run   print every command that would run (to stdout, prefixed "+ ") and run nothing
#   --confirm   required before any step that stops, overwrites, drops or deletes something
#   --yes       skip the interactive "type yes" prompt that --confirm adds on a terminal
#
# Compatible with macOS /bin/bash 3.2: no associative arrays, no mapfile, and empty
# arrays are expanded as ${a[@]+"${a[@]}"} because `set -u` rejects "${a[@]}" there.

MIGRATION_LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SANDBOX_ROOT=$(cd "$MIGRATION_LIB_DIR/../.." && pwd)
SB=${SB:-$SANDBOX_ROOT/sb}
PYTHON=${PYTHON:-python3}

DRY_RUN=0
CONFIRM=0
ASSUME_YES=0
SSH_CONNECT_TIMEOUT=${SSH_CONNECT_TIMEOUT:-15}

# ssh options used for every hop. BatchMode: never prompt for a password or host key.
# ServerAlive*: a dead connection ends after ~3 minutes instead of hanging forever.
ssh_opts() {
  printf '%s\n' -o BatchMode=yes -o "ConnectTimeout=$SSH_CONNECT_TIMEOUT" \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=6
}

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
warn() { printf '[%s] WARNING: %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
usage_error() { printf 'error: %s (see --help)\n' "$*" >&2; exit 2; }

# POSIX single-quote one word. Plain words stay unquoted so dry-run output is readable.
shq() {
  case $1 in
    '' | *[!A-Za-z0-9_./:=@%+,-]*)
      printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")" ;;
    *) printf '%s' "$1" ;;
  esac
}

quote_argv() {
  local out="" a
  for a in "$@"; do out="$out$(shq "$a") "; done
  printf '%s' "${out% }"
}

# Handle the flags every script shares. Returns 0 when the flag was consumed.
common_flag() {
  case $1 in
    --dry-run) DRY_RUN=1 ;;
    --confirm) CONFIRM=1 ;;
    --yes) ASSUME_YES=1 ;;
    *) return 1 ;;
  esac
}

require_value() { # flag value
  [ -n "${2:-}" ] && [ "${2#--}" = "$2" ] || usage_error "$1 needs a value"
}

require_set() { # name value
  [ -n "${2:-}" ] || usage_error "missing required $1"
}

# Run argv, or print it under --dry-run.
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '+ %s\n' "$(quote_argv "$@")"
    return 0
  fi
  log "+ $(quote_argv "$@")"
  "$@"
}

# Run a bash script on a remote host over ssh -A (agent forwarding, so the remote can
# open its own ssh hop to the next server with your agent; no key is copied anywhere).
# Under --dry-run the remote script is printed unquoted after "--" for readability.
remote_bash() { # target script
  local target=$1 script=$2 opts
  opts=$(ssh_opts | tr '\n' ' ')
  if [ "$DRY_RUN" = 1 ]; then
    printf '+ ssh -A %s%s -- %s\n' "$opts" "$(shq "$target")" "$script"
    return 0
  fi
  log "+ ssh -A $opts$target -- $script"
  # shellcheck disable=SC2046
  ssh -A $(ssh_opts) "$target" "bash -o pipefail -c $(shq "$script")"
}

# The ssh hop a remote script uses to reach the next server, as a string to embed in
# that remote script. The inner command runs under bash -o pipefail on the far side.
hop_ssh() { # target inner-script
  printf 'ssh %s %s %s' "$(ssh_opts | tr '\n' ' ' | sed 's/ $//')" "$(shq "$1")" \
    "$(shq "bash -o pipefail -c $(shq "$2")")"
}

# Destructive-step gate. Dry-run passes (nothing runs). Otherwise --confirm is required,
# and on an interactive terminal the operator also types "yes" unless --yes was given.
confirm_or_die() { # description
  if [ "$DRY_RUN" = 1 ]; then
    printf '# destructive step (needs --confirm): %s\n' "$1"
    return 0
  fi
  [ "$CONFIRM" = 1 ] || die "refusing: $1. This step is destructive; re-run with --confirm (or --dry-run to review)."
  if [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; then
    local answer
    printf 'About to: %s\nType yes to continue: ' "$1" >&2
    read -r answer
    [ "$answer" = yes ] || die "not confirmed; nothing was changed by this step"
  fi
}

# Bounded wait: re-run argv until it succeeds or the deadline passes. Never loops forever.
wait_until() { # timeout-seconds interval-seconds description argv...
  local timeout=$1 interval=$2 desc=$3
  shift 3
  if [ "$DRY_RUN" = 1 ]; then
    printf '+ %s   # retried every %ss for up to %ss: %s\n' "$(quote_argv "$@")" "$interval" "$timeout" "$desc"
    return 0
  fi
  local deadline=$((SECONDS + timeout)) attempt=0
  while :; do
    attempt=$((attempt + 1))
    if "$@"; then
      log "$desc: ok after $attempt attempt(s)"
      return 0
    fi
    if [ $((SECONDS + interval)) -gt "$deadline" ]; then
      warn "$desc: gave up after $attempt attempt(s) / ${timeout}s"
      return 1
    fi
    sleep "$interval"
  done
}

# Read one top-level field from a JSON document on stdin (prints nothing if absent).
json_field() { # field
  "$PYTHON" -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
except ValueError:
    sys.exit(0)
value = data.get(sys.argv[1]) if isinstance(data, dict) else None
if value is not None:
    print(value)
' "$1"
}

# A --check-table name goes into SQL verbatim, so allow only identifiers: [schema.]table,
# each part plain (folded to lower case by Postgres) or double-quoted to keep its case
# (Prisma tables are "User", "Snapshot"; pass --check-table '"Snapshot"').
check_table_name() { # value
  local rest=$1 part
  while :; do
    part=${rest%%.*}
    case $part in
      \"[A-Za-z_]*\") part=${part#\"}; part=${part%\"} ;;
      [A-Za-z_]*) ;;
      *) usage_error "bad --check-table: $1 (use name, schema.name or \"Name\")" ;;
    esac
    case $part in *[!A-Za-z0-9_]*) usage_error "bad --check-table: $1 (use name, schema.name or \"Name\")" ;; esac
    [ "$rest" != "${rest#*.}" ] || break
    rest=${rest#*.}
  done
}

is_positive_int() { case ${1:-} in '' | *[!0-9]*) return 1 ;; *) [ "$1" -gt 0 ] ;; esac; }

# Names that end up inside remote shell scripts. Quoting already protects them; this
# rejects obvious mistakes (a stray space, a path) before anything connects.
check_name() { # label value
  case $2 in
    '' | *[!A-Za-z0-9_.-]*) usage_error "$1 must match [A-Za-z0-9_.-]+ (got: $2)" ;;
  esac
}

# How remote scripts call docker. sudo -n works whether or not the login user is in the
# docker group (the old server's user was not; the new one has NOPASSWD sudo from
# prepare-host.sh). Override with MIGRATION_DOCKER=docker if sudo is unavailable.
DOCKER=${MIGRATION_DOCKER:-sudo -n docker}

# Remote-script fragment that resolves a docker volume's host directory.
volume_dir_expr() { # volume
  # shellcheck disable=SC2016 # expanded on the remote host
  printf '"$(%s volume inspect -f %s %s)"' "$DOCKER" "'{{ .Mountpoint }}'" "$(shq "$1")"
}

# Stream a tar of an old-server volume straight into a file on the new server.
# Data path: old --ssh--> new. Nothing lands on the controller; the old server reaches
# the new one with the forwarded agent. The file is written as DEST.partial and renamed
# only when the whole stream arrived.
# With require_stopped=1 the old side first refuses if a running container uses the
# volume (a final copy must be quiescent); a pre-stage copy passes 0.
stream_volume_to_file() { # old new-from-old volume dest require_stopped
  local old=$1 hop=$2 volume=$3 dest=$4 guard="" dir inner
  dir=$(dirname "$dest")
  if [ "${5:-0}" = 1 ]; then
    guard="running=\$($DOCKER ps -q --filter volume=$(shq "$volume"))
if [ -n \"\$running\" ]; then echo \"refusing: running containers use $volume: \$running\" >&2; exit 1; fi
"
  fi
  inner="mkdir -p $(shq "$dir") && cat > $(shq "$dest.partial") && mv $(shq "$dest.partial") $(shq "$dest") && ls -l $(shq "$dest")"
  remote_bash "$old" "$guard""sudo -n tar --numeric-owner -C $(volume_dir_expr "$volume") -cf - . | $(hop_ssh "$hop" "$inner")"
}

# ---------------------------------------------------------------------------
# Durable job watching (the pattern that worked live): poll `sb job-status ID --json`
# every few seconds, read lifecycle AND exit_code, keep waiting while the job is not
# terminal, and bound the whole wait to the job's own deadline plus a grace period.
# Success means lifecycle=succeeded with exit_code 0 and nothing else.

JOB_GRACE_SECONDS=${JOB_GRACE_SECONDS:-120}
JOB_FALLBACK_SECONDS=${JOB_FALLBACK_SECONDS:-7200}

# job-status JSON on stdin -> "lifecycle|exit_code|request_id|health|remaining_seconds".
# remaining_seconds = deadline_seconds - time since started_at (or accepted_at); empty
# when the snapshot does not say.
job_fields() {
  "$PYTHON" -c '
import json, sys
from datetime import datetime, timezone
try:
    d = json.loads(sys.stdin.read() or "{}")
except ValueError:
    d = {}
if not isinstance(d, dict):
    d = {}
def s(v):
    return "" if v is None else str(v).replace("|", "")
deadline = d.get("deadline_seconds")
if deadline is None and isinstance(d.get("deadline"), dict):
    deadline = d["deadline"].get("seconds")
remaining = ""
start = d.get("started_at") or d.get("accepted_at")
try:
    if deadline is not None and start:
        t = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - t).total_seconds()
        remaining = str(max(0, int(int(deadline) - elapsed)))
    elif deadline is not None:
        remaining = str(int(deadline))
except (TypeError, ValueError):
    remaining = ""
print("|".join([s(d.get("lifecycle")), s(d.get("exit_code")), s(d.get("request_id")),
                s(d.get("health")), remaining]))
'
}

# poll_job JOB INTERVAL BUDGET
# BUDGET 0 derives the bound from the job's deadline (+ JOB_GRACE_SECONDS); when the job
# does not report one, JOB_FALLBACK_SECONDS applies. Sets JOB_LIFECYCLE, JOB_EXIT,
# JOB_REQUEST, JOB_HEALTH. Returns 0 once the job is terminal, 3 when the bound ran out.
poll_job() {
  local job=$1 interval=$2 budget=$3 deadline="" json fields remaining stall_hint=""
  JOB_LIFECYCLE="" JOB_EXIT="" JOB_REQUEST="" JOB_HEALTH=""
  if [ "$budget" -gt 0 ]; then deadline=$((SECONDS + budget)); fi
  while :; do
    json=$("$SB" job-status "$job" --json 2>/dev/null || true)
    fields=$(printf '%s' "$json" | job_fields)
    # shellcheck disable=SC2034 # JOB_* are read by the calling scripts
    IFS='|' read -r JOB_LIFECYCLE JOB_EXIT JOB_REQUEST JOB_HEALTH remaining <<EOF_FIELDS
$fields
EOF_FIELDS
    if [ -z "$deadline" ]; then
      if [ -n "$remaining" ]; then
        deadline=$((SECONDS + remaining + JOB_GRACE_SECONDS))
        log "job $job: bounding the wait to its deadline (${remaining}s left) + ${JOB_GRACE_SECONDS}s"
      else
        deadline=$((SECONDS + JOB_FALLBACK_SECONDS))
        log "job $job: no deadline reported; bounding the wait to ${JOB_FALLBACK_SECONDS}s"
      fi
    fi
    log "job $job: ${JOB_LIFECYCLE:-unknown} exit=${JOB_EXIT:--} health=${JOB_HEALTH:-?}"
    if [ "$JOB_HEALTH" = suspected_stalled ] && [ -z "$stall_hint" ]; then
      stall_hint=1
      log "suspected_stalled is normal during a long silent build or readiness wait. On the remote," \
        "grep 'apply phase=' ~/sandbox/runtime/hosts/<project>/<env>/apply.log shows each phase" \
        "with its exit code; docker ps shows the containers. Do not cancel: a cancel can land after compose_recreate."
    fi
    case $JOB_LIFECYCLE in
      succeeded | failed | timed_out | cancelled | interrupted) return 0 ;;
    esac
    # running, queued, accepted, cancelling, or an unreadable snapshot: keep waiting.
    [ $((SECONDS + interval)) -le "$deadline" ] || return 3
    sleep "$interval"
  done
}

job_succeeded() { [ "$JOB_LIFECYCLE" = succeeded ] && [ "$JOB_EXIT" = 0 ]; }
