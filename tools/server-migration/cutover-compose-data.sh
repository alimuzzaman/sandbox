#!/usr/bin/env bash
# Phase 5: move the data of one stateful compose project from the old to the new server.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$HERE/lib.sh"

usage() {
  cat <<'USAGE'
Usage: cutover-compose-data.sh --old TARGET --new TARGET --compose-project P
         --db-service S [--volume V ...] [--keep-service S ...] [--service S ...]
         --health-url URL [options] --confirm

Run it only after the deploy job has SUCCEEDED on the new remote (wait-and-cutover.sh
does the waiting). Never run it while `sb host apply` is still verifying the edge:
stopping the web then fails verification and apply rolls DNS back.

Order (phase 5 of docs/server-migration.md; generalized from the Lenzora dev cutover):
  1. preflight: the Postgres container runs on both servers
  2. NEW: stop the project's containers except the database and --keep-service ones;
     the names go to WORK/stopped.txt on NEW, and step 7 restarts exactly that list.
     NEW goes first because DNS already points at it: until the data lands it would
     serve a fresh, near-empty database, and an error page is better than that.
  3. OLD: stop the same selection; the names go to WORK/stopped-old.txt on OLD
     (for rollback)
  4. final pg_dump on OLD, streamed straight to NEW            (pg-transfer.sh)
  5. final tar of every --volume on OLD, streamed to NEW       (copy-volume.sh)
     A volume copy refuses while any running container uses the volume, on either side.
  6. NEW: restore the database, then empty and extract each volume
  7. NEW: start the recorded containers
  8. verify the health URL (and revision header), compare --check-table row counts
OLD's stopped containers and data stay in place for the rollback window. If a step fails
after step 3, the script prints the rollback commands.

With --maintenance-service, the app's read-only maintenance mode (Lenzora spec 056) wraps
the cutover. The flag lives in Postgres, bound to that cluster's system identifier:
  1. preflight also runs `status` on both servers, so a missing CLI stops before any change
  2a. NEW: `enable` (read_only now). Until the restore, NEW serves its pre-seed read-only and
      refuses writes with 503, instead of accepting writes the restore would discard.
  2b. OLD: `drain --wait`: refuse writes, let queued work finish, then read_only. A drain
      lasts at least 5.5 minutes. It times out with exit 4 and OLD stays draining; the
      script then prints the `disable` commands for both servers.
  then steps 2-7 as above. The restore replaces NEW's database, and OLD's flag is bound to
  OLD's cluster, so NEW comes up read_write with no extra step; step 8 checks that.
  OLD keeps read_only for the rollback window; the rollback commands include `disable`.

Containers are selected by the label com.docker.compose.project=P, so scaled replicas and
one-off workers are included. Re-running after a partial failure is safe: the recorded
lists are merged, never truncated.

Required:
  --old TARGET               ssh target of the old server
  --new TARGET               ssh target of the new server
  --compose-project P        e.g. sandbox-host-<project>-<env> (same name on both servers)
  --db-service S             compose service running Postgres (never stopped)
  --health-url URL           public health endpoint, e.g. https://host/api/health

Options:
  --volume V                 volume to copy as files; repeatable. Full name or the
                             suffix after "<project>_" (lenzora-storage). Every
                             container using it must be stopped, so do not also list
                             its service under --keep-service. Do not list the Postgres
                             data volume: the database moves by pg_dump.
                             Example: a durable Redis job queue
                             (lenzora-production-job-queue-data) must be copied with
                             Redis stopped, not left behind.
  --keep-service S           service that keeps running on both sides (e.g. a
                             non-durable queue); repeatable
  --service S                stop ONLY these services instead of "all but db/keep";
                             repeatable
  --new-from-old TARGET      how the OLD server reaches the new one (default: --new)
  --db-container NAME        override the DB container name (default P-S-1, both sides)
  --restore-mode MODE        recreate (default, as used live) or clean; see pg-transfer.sh
  --check-table TABLE        row-count spot check old vs new; repeatable
  --expect-status CODE       health status (default 200)
  --revision-header NAME     header that carries the revision (e.g. x-lenzora-revision)
  --expect-revision VALUE    expected revision (prefix match)
  --resolve-ip IP            pass to verify-site.sh (new server IP)
  --basic-auth-env VAR       pass to verify-site.sh
  --stop-timeout SECONDS     docker stop -t (default 30)
  --work-dir PATH            transfer dir on both servers, relative to the login home
                             (default: migration/<compose-project>)
  --maintenance-service S    run the read-only maintenance CLI in this service's container
                             (Lenzora: lenzora-monitor-worker); see above
  --maintenance-command C    CLI prefix the verb is appended to
                             (default: "pnpm --silent maintenance:")
  --maintenance-reason TEXT  reason recorded with the mode change (default: server migration)
  --maintenance-timeout MIN  drain --wait budget in minutes, at least 6 (default 15)
  --maintenance-old-only     drain OLD only; leave NEW writable until its containers stop
  --dry-run                  print every command, run nothing
  --confirm                  required
  --yes                      skip the interactive prompt
  -h, --help                 this help
USAGE
}

OLD="" NEW="" HOP="" PROJECT="" DB_SERVICE="" DB_CONTAINER="" MODE=recreate HEALTH_URL=""
STATUS=200 HEADER="" REVISION="" RESOLVE_IP="" AUTH_ENV="" WORK="" STOP_TIMEOUT=30
MAINT_SERVICE="" MAINT_CMD="pnpm --silent maintenance:" MAINT_REASON="server migration"
MAINT_TIMEOUT=15 MAINT_NEW=1
SERVICES=() KEEP=() VOLUMES=() TABLES=()
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --old) require_value "$1" "${2:-}"; OLD=$2; shift 2 ;;
    --new) require_value "$1" "${2:-}"; NEW=$2; shift 2 ;;
    --new-from-old) require_value "$1" "${2:-}"; HOP=$2; shift 2 ;;
    --compose-project) require_value "$1" "${2:-}"; PROJECT=$2; shift 2 ;;
    --db-service) require_value "$1" "${2:-}"; DB_SERVICE=$2; shift 2 ;;
    --db-container) require_value "$1" "${2:-}"; DB_CONTAINER=$2; shift 2 ;;
    --service) require_value "$1" "${2:-}"; SERVICES+=("$2"); shift 2 ;;
    --keep-service) require_value "$1" "${2:-}"; KEEP+=("$2"); shift 2 ;;
    --volume) require_value "$1" "${2:-}"; VOLUMES+=("$2"); shift 2 ;;
    --check-table) require_value "$1" "${2:-}"; TABLES+=("$2"); shift 2 ;;
    --restore-mode) require_value "$1" "${2:-}"; MODE=$2; shift 2 ;;
    --health-url) require_value "$1" "${2:-}"; HEALTH_URL=$2; shift 2 ;;
    --expect-status) require_value "$1" "${2:-}"; STATUS=$2; shift 2 ;;
    --revision-header) require_value "$1" "${2:-}"; HEADER=$2; shift 2 ;;
    --expect-revision) require_value "$1" "${2:-}"; REVISION=$2; shift 2 ;;
    --resolve-ip) require_value "$1" "${2:-}"; RESOLVE_IP=$2; shift 2 ;;
    --basic-auth-env) require_value "$1" "${2:-}"; AUTH_ENV=$2; shift 2 ;;
    --stop-timeout) require_value "$1" "${2:-}"; STOP_TIMEOUT=$2; shift 2 ;;
    --work-dir) require_value "$1" "${2:-}"; WORK=$2; shift 2 ;;
    --maintenance-service) require_value "$1" "${2:-}"; MAINT_SERVICE=$2; shift 2 ;;
    --maintenance-command) require_value "$1" "${2:-}"; MAINT_CMD=$2; shift 2 ;;
    --maintenance-reason) require_value "$1" "${2:-}"; MAINT_REASON=$2; shift 2 ;;
    --maintenance-timeout) require_value "$1" "${2:-}"; MAINT_TIMEOUT=$2; shift 2 ;;
    --maintenance-old-only) MAINT_NEW=0; shift ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
require_set --old "$OLD"
require_set --new "$NEW"
require_set --compose-project "$PROJECT"
require_set --db-service "$DB_SERVICE"
require_set --health-url "$HEALTH_URL"
check_name --compose-project "$PROJECT"
check_name --db-service "$DB_SERVICE"
is_positive_int "$STOP_TIMEOUT" || usage_error "--stop-timeout must be a positive integer"
case $MODE in clean | recreate) ;; *) usage_error "--restore-mode must be clean or recreate" ;; esac
for s in ${SERVICES[@]+"${SERVICES[@]}"} ${KEEP[@]+"${KEEP[@]}"}; do
  check_name "service" "$s"
  [ "$s" != "$DB_SERVICE" ] || usage_error "--service/--keep-service must not name the database service"
done
if [ -n "$MAINT_SERVICE" ]; then
  check_name --maintenance-service "$MAINT_SERVICE"
  [ "$MAINT_SERVICE" != "$DB_SERVICE" ] || usage_error "--maintenance-service must not name the database service"
  case $MAINT_CMD in '' | *[!A-Za-z0-9_.:/\ -]*) usage_error "--maintenance-command must match [A-Za-z0-9_.:/ -]+" ;; esac
  is_positive_int "$MAINT_TIMEOUT" && [ "$MAINT_TIMEOUT" -ge 6 ] ||
    usage_error "--maintenance-timeout must be at least 6 (a drain lasts at least 5.5 minutes)"
fi
[ -n "$HOP" ] || HOP=$NEW
[ -n "$DB_CONTAINER" ] || DB_CONTAINER="$PROJECT-$DB_SERVICE-1"
[ -n "$WORK" ] || WORK="migration/$PROJECT"

FULL_VOLUMES=()
for v in ${VOLUMES[@]+"${VOLUMES[@]}"}; do
  case $v in "${PROJECT}_"*) FULL_VOLUMES+=("$v") ;; *) FULL_VOLUMES+=("${PROJECT}_$v") ;; esac
  check_name --volume "$v"
done

KEEP_WORDS=" $DB_SERVICE ${KEEP[*]+${KEEP[*]}} "
ONLY_WORDS=" ${SERVICES[*]+${SERVICES[*]}} "
if [ "${#SERVICES[@]}" -gt 0 ]; then SELECTION="services:${SERVICES[*]}"; else SELECTION="all but:$KEEP_WORDS"; fi

confirm_or_die "cut over $PROJECT data from $OLD to $NEW (stops $SELECTION on both, replaces the new database and ${#FULL_VOLUMES[@]} volume(s))"

PASS=(--yes)
[ "$DRY_RUN" = 0 ] || PASS+=(--dry-run)
[ "$CONFIRM" = 0 ] || PASS+=(--confirm)

# Stop the selected running containers of the project and merge their names into
# LIST (one name per line). Merging keeps the first, complete list on a re-run.
stop_selected() { # target list-file
  local list
  list=$(shq "$2")
  remote_bash "$1" "set -e
mkdir -p $(shq "$WORK")
: > $list.now
for id in \$($DOCKER ps -q --filter label=com.docker.compose.project=$(shq "$PROJECT")); do
  svc=\$($DOCKER inspect -f '{{ index .Config.Labels \"com.docker.compose.service\" }}' \"\$id\")
  case $(shq "$KEEP_WORDS") in *\" \$svc \"*) continue ;; esac
  if [ -n $(shq "${ONLY_WORDS// /}") ]; then case $(shq "$ONLY_WORDS") in *\" \$svc \"*) ;; *) continue ;; esac; fi
  $DOCKER inspect -f '{{ .Name }}' \"\$id\" | sed 's#^/##' >> $list.now
done
cat $list.now $list 2>/dev/null | sort -u > $list.merged
mv $list.merged $list
if [ -s $list.now ]; then xargs $DOCKER stop -t $STOP_TIMEOUT < $list.now >/dev/null; fi
echo \"stopped \$(wc -l < $list.now | tr -d ' ') container(s); recorded in $2:\"
cat $list
rm -f $list.now"
}

start_recorded() { # target list-file
  local list
  list=$(shq "$2")
  remote_bash "$1" "set -e
test -f $list || { echo 'no recorded list $2' >&2; exit 1; }
if [ -s $list ]; then xargs $DOCKER start < $list >/dev/null; fi
sleep 5
$DOCKER ps -a --filter label=com.docker.compose.project=$(shq "$PROJECT") --format '{{ .Names }} {{ .Status }}'"
}

require_running() { # target container
  remote_bash "$1" "[ \"\$($DOCKER inspect -f '{{ .State.Running }}' $(shq "$2"))\" = true ] || { echo '$2 is not running' >&2; exit 1; }"
}

# Run the maintenance CLI verb in the first running container of --maintenance-service.
maint_exec() { # target verb [arg...]
  local target=$1 verb=$2
  shift 2
  remote_bash "$target" "set -e
id=\$($DOCKER ps -q --filter label=com.docker.compose.project=$(shq "$PROJECT") --filter label=com.docker.compose.service=$(shq "$MAINT_SERVICE") | head -1)
[ -n \"\$id\" ] || { echo 'no running $MAINT_SERVICE container in $PROJECT' >&2; exit 1; }
$DOCKER exec \"\$id\" $MAINT_CMD$verb${*:+ $(quote_argv "$@")}"
}

# Fail unless `status --json` on the target reports the expected mode.
maint_require_mode() { # target mode
  remote_bash "$1" "set -e
id=\$($DOCKER ps -q --filter label=com.docker.compose.project=$(shq "$PROJECT") --filter label=com.docker.compose.service=$(shq "$MAINT_SERVICE") | head -1)
[ -n \"\$id\" ] || { echo 'no running $MAINT_SERVICE container in $PROJECT' >&2; exit 1; }
out=\$($DOCKER exec \"\$id\" ${MAINT_CMD}status --json)
printf '%s\\n' \"\$out\"
printf '%s' \"\$out\" | tr -d ' \\n' | grep -q '\"mode\":\"$2\"' || { echo 'maintenance mode is not $2' >&2; exit 1; }"
}

maint_disable_text() { # target
  printf '  ssh %s %s\n' "$(shq "$1")" "$(shq "$DOCKER exec \$($DOCKER ps -q --filter label=com.docker.compose.project=$PROJECT --filter label=com.docker.compose.service=$MAINT_SERVICE | head -1) ${MAINT_CMD}disable")"
}

rollback_text() {
  printf 'Rollback: re-apply the project on the OLD remote (moves DNS back), then restart\n'
  printf 'the old containers:\n  ssh %s %s\n' "$(shq "$OLD")" \
    "$(shq "xargs $DOCKER start < $WORK/stopped-old.txt")"
  if [ -n "$MAINT_SERVICE" ]; then
    printf 'then make OLD writable again (its read_only flag survives on its own cluster):\n'
    maint_disable_text "$OLD"
  fi
}

OLD_STOPPED=0
MAINT_ON=0
on_exit() {
  local status=$?
  if [ "$status" != 0 ] && [ "$OLD_STOPPED" = 0 ] && [ "$MAINT_ON" = 1 ]; then
    printf '\nCutover failed before any container was stopped. To leave maintenance mode:\n' >&2
    maint_disable_text "$OLD" >&2
    [ "$MAINT_NEW" = 0 ] || maint_disable_text "$NEW" >&2
  fi
  if [ "$status" != 0 ] && [ "$OLD_STOPPED" = 1 ]; then
    printf '\nCutover failed after the old containers were stopped. DNS points at the new server.\n' >&2
    printf 'Fix and re-run this script (it is safe to re-run), or roll back.\n' >&2
    printf 'To roll back: ' >&2
    rollback_text >&2
  fi
}
trap on_exit EXIT

log "1/8 preflight"
require_running "$OLD" "$DB_CONTAINER"
require_running "$NEW" "$DB_CONTAINER"
if [ -n "$MAINT_SERVICE" ]; then
  maint_exec "$OLD" status
  [ "$MAINT_NEW" = 0 ] || maint_exec "$NEW" status

  MAINT_ON=1
  if [ "$MAINT_NEW" = 1 ]; then
    log "2a/8 NEW -> read_only (serves its pre-seed read-only until the restore)"
    maint_exec "$NEW" enable --reason "$MAINT_REASON"
  fi
  log "2b/8 drain OLD -> read_only (at least 5.5 minutes, budget ${MAINT_TIMEOUT}m)"
  maint_exec "$OLD" drain --reason "$MAINT_REASON" --wait --timeout "$MAINT_TIMEOUT"
  maint_require_mode "$OLD" read_only
fi

log "2/8 stop $SELECTION on NEW (database keeps running)"
stop_selected "$NEW" "$WORK/stopped.txt"

log "3/8 stop the same on OLD"
OLD_STOPPED=1
stop_selected "$OLD" "$WORK/stopped-old.txt"

log "4/8 final database dump OLD -> NEW"
"$HERE/pg-transfer.sh" "${PASS[@]}" --dump-only --old "$OLD" --new "$NEW" \
  --new-from-old "$HOP" --container "$DB_CONTAINER" --dump "$WORK/db.dump"

log "5/8 final volume copies OLD -> NEW"
for v in ${FULL_VOLUMES[@]+"${FULL_VOLUMES[@]}"}; do
  "$HERE/copy-volume.sh" "${PASS[@]}" --fetch-only --old "$OLD" --new "$NEW" \
    --new-from-old "$HOP" --volume "$v" --tar "$WORK/$v.tar"
done

log "6/8 restore on NEW"
"$HERE/pg-transfer.sh" "${PASS[@]}" --restore-only --new "$NEW" \
  --container "$DB_CONTAINER" --dump "$WORK/db.dump" --restore-mode "$MODE"
for v in ${FULL_VOLUMES[@]+"${FULL_VOLUMES[@]}"}; do
  "$HERE/copy-volume.sh" "${PASS[@]}" --extract-only --new "$NEW" \
    --volume "$v" --tar "$WORK/$v.tar"
done

log "7/8 start the recorded containers on NEW"
start_recorded "$NEW" "$WORK/stopped.txt"

log "8/8 verify"
VERIFY=(--url "$HEALTH_URL" --expect-status "$STATUS")
[ -z "$HEADER" ] || VERIFY+=(--revision-header "$HEADER")
[ -z "$REVISION" ] || VERIFY+=(--expect-revision "$REVISION")
[ -z "$RESOLVE_IP" ] || VERIFY+=(--resolve-ip "$RESOLVE_IP")
[ -z "$AUTH_ENV" ] || VERIFY+=(--basic-auth-env "$AUTH_ENV")
[ "$DRY_RUN" = 0 ] || VERIFY+=(--dry-run)
"$HERE/verify-site.sh" "${VERIFY[@]}"
if [ -n "$MAINT_SERVICE" ]; then
  # The restored flag is bound to OLD's cluster, so NEW must read as writable.
  maint_require_mode "$NEW" read_write
fi
if [ "${#TABLES[@]}" -gt 0 ]; then
  CHECK=()
  for t in "${TABLES[@]}"; do CHECK+=(--check-table "$t"); done
  "$HERE/pg-transfer.sh" "${PASS[@]}" --check-only --old "$OLD" --new "$NEW" \
    --container "$DB_CONTAINER" "${CHECK[@]}"
fi

OLD_STOPPED=0
printf '# Cutover of %s complete. OLD keeps its stopped containers and data for the rollback window.\n# ' "$PROJECT"
rollback_text | sed '2,$s/^/# /'
