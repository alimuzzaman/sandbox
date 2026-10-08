#!/usr/bin/env bash
# Dump a compose Postgres service on the old server and restore it on the new one.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
  cat <<'USAGE'
Usage: pg-transfer.sh --old TARGET --new TARGET --container NAME [options] --confirm

Steps (each can be run alone):
  dump     on OLD: docker exec CONTAINER pg_dump -Fc -Z 6, piped straight over ssh into a
           file on NEW (old -> new with agent forwarding; nothing passes through this Mac)
  restore  on NEW: check the archive with pg_restore -l, copy it into the container and
           restore it with --no-owner --no-acl in a single transaction
  check    compare SELECT count(*) of each --check-table on old and new

The database user and name come from the containers' own POSTGRES_USER and POSTGRES_DB
environment, so no credential is passed or printed. Stop every writer (web, workers)
on OLD before the final dump, and on NEW before the restore.

Required:
  --old TARGET              ssh target of the old server
  --new TARGET              ssh target of the new server
  --container NAME          Postgres container on the old server, e.g. <compose>-<db>-1

Options:
  --new-container NAME      Postgres container on the new server (default: same name)
  --new-from-old TARGET     how the OLD server reaches the new one (default: --new)
  --dump PATH               dump file on the new server, relative to the login home
                            (default: migration/<container>.dump)
  --restore-mode MODE       clean (default): pg_restore --clean --if-exists
                            recreate: dropdb --force + createdb -O POSTGRES_USER, then
                            pg_restore (PostgreSQL 13+ for --force; what the Lenzora
                            cutover used)
  --check-table TABLE       compare row counts old vs new; repeatable
  --dump-only | --restore-only | --check-only
  --dry-run                 print the commands, run nothing
  --confirm                 required for restore (it replaces the new database)
  --yes                     skip the interactive prompt
  -h, --help                this help
USAGE
}

OLD="" NEW="" HOP="" CONTAINER="" NEW_CONTAINER="" DUMP="" MODE=clean
DO_DUMP=1 DO_RESTORE=1 DO_CHECK=1 ONLY=""
TABLES=()
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --old) require_value "$1" "${2:-}"; OLD=$2; shift 2 ;;
    --new) require_value "$1" "${2:-}"; NEW=$2; shift 2 ;;
    --new-from-old) require_value "$1" "${2:-}"; HOP=$2; shift 2 ;;
    --container) require_value "$1" "${2:-}"; CONTAINER=$2; shift 2 ;;
    --new-container) require_value "$1" "${2:-}"; NEW_CONTAINER=$2; shift 2 ;;
    --dump) require_value "$1" "${2:-}"; DUMP=$2; shift 2 ;;
    --restore-mode) require_value "$1" "${2:-}"; MODE=$2; shift 2 ;;
    --check-table) require_value "$1" "${2:-}"; TABLES+=("$2"); shift 2 ;;
    --dump-only | --restore-only | --check-only)
      [ -z "$ONLY" ] || usage_error "give only one of --dump-only/--restore-only/--check-only"
      ONLY=$1; shift ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
case $ONLY in
  --dump-only) DO_RESTORE=0 DO_CHECK=0 ;;
  --restore-only) DO_DUMP=0 DO_CHECK=0 ;;
  --check-only) DO_DUMP=0 DO_RESTORE=0 ;;
esac
require_set --new "$NEW"
require_set --container "$CONTAINER"
check_name --container "$CONTAINER"
[ "$DO_DUMP" = 0 ] && [ "$DO_CHECK" = 0 ] || require_set --old "$OLD"
[ -n "$NEW_CONTAINER" ] || NEW_CONTAINER=$CONTAINER
check_name --new-container "$NEW_CONTAINER"
[ -n "$HOP" ] || HOP=$NEW
[ -n "$DUMP" ] || DUMP="migration/$CONTAINER.dump"
case $MODE in clean | recreate) ;; *) usage_error "--restore-mode must be clean or recreate" ;; esac
for t in ${TABLES[@]+"${TABLES[@]}"}; do check_table_name "$t"; done

# Expanded inside the container, never on this machine.
# shellcheck disable=SC2016
PSQL_ENV='-U "$POSTGRES_USER" -d "$POSTGRES_DB"'

if [ "$DO_DUMP" = 1 ]; then
  start=$SECONDS
  inner="mkdir -p $(shq "$(dirname "$DUMP")") && cat > $(shq "$DUMP.partial") && mv $(shq "$DUMP.partial") $(shq "$DUMP") && ls -l $(shq "$DUMP")"
  remote_bash "$OLD" "$DOCKER exec $(shq "$CONTAINER") sh -c $(shq "pg_dump $PSQL_ENV -Fc -Z 6") | $(hop_ssh "$HOP" "$inner")"
  [ "$DRY_RUN" = 1 ] || log "dumped $CONTAINER to $NEW:$DUMP in $((SECONDS - start))s"
fi

if [ "$DO_RESTORE" = 1 ]; then
  confirm_or_die "replace the database in $NEW_CONTAINER on $NEW with $DUMP ($MODE)"
  c=$(shq "$NEW_CONTAINER")
  script="set -e
test -s $(shq "$DUMP")
$DOCKER exec -i $c pg_restore -l < $(shq "$DUMP") >/dev/null
$DOCKER cp $(shq "$DUMP") $c:/tmp/migration.dump"
  if [ "$MODE" = recreate ]; then
    # shellcheck disable=SC2016 # expanded inside the container
    script="$script
$DOCKER exec $c sh -c $(shq '[ "$POSTGRES_DB" != postgres ] && dropdb -U "$POSTGRES_USER" --maintenance-db=postgres --if-exists --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" --maintenance-db=postgres -O "$POSTGRES_USER" "$POSTGRES_DB"')
$DOCKER exec $c sh -c $(shq "pg_restore $PSQL_ENV --no-owner --no-acl --exit-on-error --single-transaction /tmp/migration.dump")"
  else
    script="$script
$DOCKER exec $c sh -c $(shq "pg_restore $PSQL_ENV --clean --if-exists --no-owner --no-acl --exit-on-error --single-transaction /tmp/migration.dump")"
  fi
  script="$script
$DOCKER exec $c rm -f /tmp/migration.dump"
  start=$SECONDS
  remote_bash "$NEW" "$script"
  [ "$DRY_RUN" = 1 ] || log "restored $DUMP into $NEW_CONTAINER in $((SECONDS - start))s"
fi

count_rows() { # target container table
  remote_bash "$1" "$DOCKER exec $(shq "$2") sh -c $(shq "psql $PSQL_ENV -Atc 'select count(*) from $3'")"
}

if [ "$DO_CHECK" = 1 ] && [ "${#TABLES[@]}" -gt 0 ]; then
  mismatch=0
  for t in "${TABLES[@]}"; do
    if [ "$DRY_RUN" = 1 ]; then
      count_rows "$OLD" "$CONTAINER" "$t"
      count_rows "$NEW" "$NEW_CONTAINER" "$t"
      continue
    fi
    old_n=$(count_rows "$OLD" "$CONTAINER" "$t")
    new_n=$(count_rows "$NEW" "$NEW_CONTAINER" "$t")
    if [ "$old_n" = "$new_n" ]; then
      log "rows $t: $old_n = $new_n"
    else
      warn "rows $t: old=$old_n new=$new_n"
      mismatch=1
    fi
  done
  [ "$mismatch" = 0 ] || die "row counts differ (writers still running on old, or an incomplete restore)"
fi
