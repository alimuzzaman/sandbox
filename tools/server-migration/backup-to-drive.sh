#!/usr/bin/env bash
# Phase 1: encrypted backups to Google Drive before anything moves.
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
. "$(dirname "$SELF")/lib.sh"

usage() {
  cat <<'USAGE'
Usage:
  backup-to-drive.sh repo --repo PATH --name NAME --secret-source ALIAS [options] --confirm
  backup-to-drive.sh recovery --remote NAME --profile P [--profile P ...] --backup-id ID
                     --secret-source ALIAS [options] --confirm

repo      For a repository with no runtime: git bundle --all, verify the bundle, encrypt
          it with gpg AES256, decrypt it again and compare sha256 (round trip), upload
          to DRIVE_ROOT/<UTC-stamp>-<name>/ with rclone, then `rclone check` the upload
          (md5 on Drive). The local work directory is removed afterwards.
recovery  A Sandbox recovery set: `sb recovery create --confirm`, then `sb recovery verify`.

The passphrase never passes through this script's environment, arguments or output:
the encryption step (and `sb recovery create`) run as the child of
  ./sb secrets run --source ALIAS --key KEY --destination RECOVERY_PASSPHRASE -- ...
which hands exactly that one value to the child. If the source alias is not registered,
stop and have it registered; never read the secrets file instead.

Options:
  --secret-source ALIAS    registered Sandbox secret source holding the passphrase
  --secret-key KEY         key inside that source (default RECOVERY_PASSPHRASE)
  --drive-root REMOTE:PATH repo mode upload root (default gdrive:hermes-full-recovery/manual)
  --destination REMOTE:PATH recovery mode destination (default gdrive:hermes-full-recovery)
  --work-dir DIR           repo mode scratch dir (default: a new mktemp -d)
  --timeout SECONDS        secrets run timeout, 1..1800 (default 1800)
  --dry-run                print the commands, run nothing
  --confirm                required (uploads to Drive / creates a recovery set)
  --yes                    skip the interactive prompt
  -h, --help               this help
USAGE
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# Child mode, only ever started by `sb secrets run` (see repo mode below).
if [ "${1:-}" = --internal-crypt ]; then
  [ $# = 3 ] || die "--internal-crypt IN OUT"
  [ -n "${RECOVERY_PASSPHRASE:-}" ] || die "RECOVERY_PASSPHRASE was not delivered by sb secrets run"
  in=$2 out=$3
  printf '%s' "$RECOVERY_PASSPHRASE" | gpg --batch --yes --quiet --pinentry-mode loopback \
    --passphrase-fd 0 --symmetric --cipher-algo AES256 --output "$out" "$in"
  round=$(printf '%s' "$RECOVERY_PASSPHRASE" | gpg --batch --quiet --pinentry-mode loopback \
    --passphrase-fd 0 --decrypt "$out" | { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | awk '{print $1}')
  printf 'roundtrip-sha256 %s\n' "$round"
  exit 0
fi

MODE=${1:-}
case $MODE in
  -h | --help) usage; exit 0 ;;
  repo | recovery) shift ;;
  *) usage_error "first argument must be repo or recovery" ;;
esac

REPO="" NAME="" SOURCE="" KEY=RECOVERY_PASSPHRASE DRIVE_ROOT=gdrive:hermes-full-recovery/manual
DESTINATION=gdrive:hermes-full-recovery WORK="" TIMEOUT=1800 REMOTE="" BACKUP_ID=""
PROFILES=()
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --repo) require_value "$1" "${2:-}"; REPO=$2; shift 2 ;;
    --name) require_value "$1" "${2:-}"; NAME=$2; shift 2 ;;
    --secret-source) require_value "$1" "${2:-}"; SOURCE=$2; shift 2 ;;
    --secret-key) require_value "$1" "${2:-}"; KEY=$2; shift 2 ;;
    --drive-root) require_value "$1" "${2:-}"; DRIVE_ROOT=$2; shift 2 ;;
    --destination) require_value "$1" "${2:-}"; DESTINATION=$2; shift 2 ;;
    --work-dir) require_value "$1" "${2:-}"; WORK=$2; shift 2 ;;
    --timeout) require_value "$1" "${2:-}"; TIMEOUT=$2; shift 2 ;;
    --remote) require_value "$1" "${2:-}"; REMOTE=$2; shift 2 ;;
    --profile) require_value "$1" "${2:-}"; PROFILES+=("$2"); shift 2 ;;
    --backup-id) require_value "$1" "${2:-}"; BACKUP_ID=$2; shift 2 ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
require_set --secret-source "$SOURCE"
check_name --secret-key "$KEY"
is_positive_int "$TIMEOUT" && [ "$TIMEOUT" -le 1800 ] || usage_error "--timeout must be 1..1800"

SECRETS_RUN=("$SB" secrets run --source "$SOURCE" --key "$KEY" --destination RECOVERY_PASSPHRASE
  --timeout-seconds "$TIMEOUT" --project-dir "$SANDBOX_ROOT" --)

if [ "$MODE" = recovery ]; then
  require_set --remote "$REMOTE"
  require_set --backup-id "$BACKUP_ID"
  [ "${#PROFILES[@]}" -gt 0 ] || usage_error "give at least one --profile"
  PROFILE_ARGS=()
  for p in "${PROFILES[@]}"; do PROFILE_ARGS+=(--profile "$p"); done
  confirm_or_die "create recovery set $BACKUP_ID from $REMOTE (${PROFILES[*]}) in $DESTINATION"
  run "${SECRETS_RUN[@]}" "$SB" recovery create --remote "$REMOTE" "${PROFILE_ARGS[@]}" \
    --backup-id "$BACKUP_ID" --destination "$DESTINATION" --confirm --json
  run "$SB" recovery verify --remote "$REMOTE" --backup-id "$BACKUP_ID" --destination "$DESTINATION" --json
  exit 0
fi

require_set --repo "$REPO"
require_set --name "$NAME"
check_name --name "$NAME"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$DRIVE_ROOT/$STAMP-$NAME"
if [ -z "$WORK" ]; then
  if [ "$DRY_RUN" = 1 ]; then WORK='<mktemp -d>'; else WORK=$(mktemp -d); fi
fi
UPLOAD="$WORK/upload"
BUNDLE="$WORK/$NAME.bundle"
CIPHER="$UPLOAD/$NAME.bundle.gpg"

confirm_or_die "bundle $REPO, encrypt it and upload to $DEST"
run mkdir -p "$UPLOAD"
run git -C "$REPO" bundle create "$BUNDLE" --all
run git -C "$REPO" bundle verify "$BUNDLE"

if [ "$DRY_RUN" = 1 ]; then
  run "${SECRETS_RUN[@]}" "$SELF" --internal-crypt "$BUNDLE" "$CIPHER"
  printf '# compare the printed roundtrip-sha256 with sha256(%s), write %s/SHA256SUMS\n' "$BUNDLE" "$UPLOAD"
  run rclone copy "$UPLOAD" "$DEST"
  run rclone check "$UPLOAD" "$DEST" --one-way
  exit 0
fi

plain_sha=$(sha256_of "$BUNDLE")
log "+ $(quote_argv "${SECRETS_RUN[@]}" "$SELF" --internal-crypt "$BUNDLE" "$CIPHER")"
crypt_out=$("${SECRETS_RUN[@]}" "$SELF" --internal-crypt "$BUNDLE" "$CIPHER") \
  || die "encryption under sb secrets run failed"
round_sha=$(printf '%s\n' "$crypt_out" | sed -n 's/.*roundtrip-sha256 \([0-9a-f]\{64\}\).*/\1/p' | tail -n 1)
[ "$round_sha" = "$plain_sha" ] || die "round-trip sha256 mismatch (bundle $plain_sha, decrypted ${round_sha:-none})"
log "round trip ok: sha256 $plain_sha"
printf '%s  %s\n%s  %s\n' "$plain_sha" "$NAME.bundle" "$(sha256_of "$CIPHER")" "$NAME.bundle.gpg" > "$UPLOAD/SHA256SUMS"
rm -f "$BUNDLE"

run rclone copy "$UPLOAD" "$DEST"
run rclone check "$UPLOAD" "$DEST" --one-way
rm -rf "$WORK"
log "uploaded and verified: $DEST"
