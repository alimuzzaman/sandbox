#!/usr/bin/env bash
# Pre-stage a docker volume from the old server onto the new server as a tar file.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
  cat <<'USAGE'
Usage: stage-volume.sh --old TARGET --new TARGET --volume NAME [options]

Copies a tar of docker volume NAME on the old server straight to a file on the new
server (old -> new over ssh with agent forwarding; nothing passes through this Mac).
It only reads the old volume and writes one file on the new server, so it needs no
--confirm. Use it ahead of a cutover to measure the transfer and warm the path; the
cutover still takes a final copy after the services stop (copy-volume.sh).

Required:
  --old TARGET            ssh target of the old server (from this machine)
  --new TARGET            ssh target of the new server (from this machine)
  --volume NAME           docker volume on the old server

Options:
  --new-from-old TARGET   how the OLD server reaches the new one (default: --new)
  --dest PATH             file on the new server, relative to the login home
                          (default: migration/<volume>.tar)
  --dry-run               print the commands, run nothing
  -h, --help              this help

Remote docker runs as "sudo -n docker"; set MIGRATION_DOCKER=docker to change that.
USAGE
}

OLD="" NEW="" HOP="" VOLUME="" DEST=""
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --old) require_value "$1" "${2:-}"; OLD=$2; shift 2 ;;
    --new) require_value "$1" "${2:-}"; NEW=$2; shift 2 ;;
    --new-from-old) require_value "$1" "${2:-}"; HOP=$2; shift 2 ;;
    --volume) require_value "$1" "${2:-}"; VOLUME=$2; shift 2 ;;
    --dest) require_value "$1" "${2:-}"; DEST=$2; shift 2 ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
require_set --old "$OLD"
require_set --new "$NEW"
require_set --volume "$VOLUME"
check_name --volume "$VOLUME"
[ -n "$HOP" ] || HOP=$NEW
[ -n "$DEST" ] || DEST="migration/$VOLUME.tar"

start=$SECONDS
stream_volume_to_file "$OLD" "$HOP" "$VOLUME" "$DEST" 0
[ "$DRY_RUN" = 1 ] || log "staged $VOLUME to $NEW:$DEST in $((SECONDS - start))s"
