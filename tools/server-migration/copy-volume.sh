#!/usr/bin/env bash
# Copy one docker volume old -> new, replacing the new volume's contents.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
  cat <<'USAGE'
Usage: copy-volume.sh --old TARGET --new TARGET --volume NAME [options] --confirm

Two steps, each can be run alone:
  fetch    refuse if a running container on the old server uses the volume, then tar
           it straight into a file on the new server (old -> new over ssh -A; nothing
           passes through this Mac). Works for any plain volume: file storage, a
           durable Redis job queue, a readiness marker.
  extract  on the new server: refuse if a running container uses the volume, check the
           tar is readable, empty the new volume's directory, and extract the tar with
           ownership and permissions preserved (tar --numeric-owner -xp)

The old volume is only read. Stop the services that write to it first (on the old
server) and the services that use the new volume (on the new server);
cutover-compose-data.sh does both.

Required:
  --old TARGET            ssh target of the old server
  --new TARGET            ssh target of the new server
  --volume NAME           docker volume on the old server

Options:
  --new-volume NAME       docker volume on the new server (default: same name)
  --new-from-old TARGET   how the OLD server reaches the new one (default: --new)
  --tar PATH              tar file on the new server, relative to the login home
                          (default: migration/<volume>.tar)
  --fetch-only            only fetch
  --allow-running-source  fetch even while a container on the old server uses the
                          volume (default: refuse, a final copy must be quiescent)
  --extract-only          only extract an already fetched/staged tar
  --dry-run               print the commands, run nothing
  --confirm               required for extract (it empties the new volume)
  --yes                   skip the interactive prompt
  -h, --help              this help
USAGE
}

OLD="" NEW="" HOP="" VOLUME="" NEW_VOLUME="" TAR="" DO_FETCH=1 DO_EXTRACT=1 REQUIRE_STOPPED=1
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --old) require_value "$1" "${2:-}"; OLD=$2; shift 2 ;;
    --new) require_value "$1" "${2:-}"; NEW=$2; shift 2 ;;
    --new-from-old) require_value "$1" "${2:-}"; HOP=$2; shift 2 ;;
    --volume) require_value "$1" "${2:-}"; VOLUME=$2; shift 2 ;;
    --new-volume) require_value "$1" "${2:-}"; NEW_VOLUME=$2; shift 2 ;;
    --tar) require_value "$1" "${2:-}"; TAR=$2; shift 2 ;;
    --fetch-only) DO_EXTRACT=0; shift ;;
    --allow-running-source) REQUIRE_STOPPED=0; shift ;;
    --extract-only) DO_FETCH=0; shift ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
require_set --new "$NEW"
require_set --volume "$VOLUME"
check_name --volume "$VOLUME"
[ "$DO_FETCH" = 1 ] || [ "$DO_EXTRACT" = 1 ] || usage_error "--fetch-only and --extract-only exclude each other"
[ "$DO_FETCH" = 0 ] || require_set --old "$OLD"
[ -n "$NEW_VOLUME" ] || NEW_VOLUME=$VOLUME
check_name --new-volume "$NEW_VOLUME"
[ -n "$HOP" ] || HOP=$NEW
[ -n "$TAR" ] || TAR="migration/$VOLUME.tar"

if [ "$DO_FETCH" = 1 ]; then
  start=$SECONDS
  stream_volume_to_file "$OLD" "$HOP" "$VOLUME" "$TAR" "$REQUIRE_STOPPED"
  [ "$DRY_RUN" = 1 ] || log "fetched $VOLUME in $((SECONDS - start))s"
fi

if [ "$DO_EXTRACT" = 1 ]; then
  confirm_or_die "replace the contents of volume $NEW_VOLUME on $NEW with $TAR"
  dir=$(volume_dir_expr "$NEW_VOLUME")
  remote_bash "$NEW" "set -e
running=\$($DOCKER ps -q --filter volume=$(shq "$NEW_VOLUME"))
if [ -n \"\$running\" ]; then echo \"refusing: running containers use $NEW_VOLUME: \$running\" >&2; exit 1; fi
test -s $(shq "$TAR")
sudo -n tar -tf $(shq "$TAR") >/dev/null
mp=$dir
case \$mp in /*/_data) ;; *) echo \"unexpected volume path: \$mp\" >&2; exit 1 ;; esac
sudo -n find \"\$mp\" -mindepth 1 -delete
sudo -n tar -C \"\$mp\" --numeric-owner -xpf $(shq "$TAR")
sudo -n du -sh \"\$mp\""
fi
