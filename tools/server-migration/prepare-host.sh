#!/usr/bin/env bash
# Phase 2: one-time preparation of a NEW migration target server.
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() {
  cat <<'EOF'
Usage: prepare-host.sh --root-ssh TARGET --user NAME [options]

One-time preparation of a new server before Sandbox can deploy to it (phase 2 of
docs/server-migration.md). Needs root once: TARGET must log in as root or as a user
with passwordless sudo.

Host preparation (runs on TARGET, idempotent):
  * /etc/sudoers.d/90-sandbox-NAME with NOPASSWD, validated with visudo -cf first
  * adds NAME to the docker group
  * extra swapfile /swapfile-sandbox (default 8G) in /etc/fstab and
    vm.swappiness in /etc/sysctl.d/90-sandbox-swap.conf; a panel-owned /swapfile
    is never touched
  * reports whether ufw allows 80/tcp and 443/tcp (--open-ufw adds the rules)

Sandbox registration (runs locally, only with --remote-name):
  ./sb remote add NAME SSH_URL --front-door nginx
  ./sb remote provision NAME --control https --control-host HOST --front-door nginx --confirm

Required:
  --root-ssh TARGET       ssh target with root rights, e.g. root@host
  --user NAME             the unprivileged account Sandbox will use

Options:
  --swap-size SIZE        size of /swapfile-sandbox (default 8G; 0 skips swap)
  --swappiness N          vm.swappiness (default 10)
  --open-ufw              add ufw allow rules for 80/tcp and 443/tcp when missing
  --remote-name NAME      also register + provision this Sandbox remote
  --ssh-url URL           user@host for `sb remote add` (required with --remote-name)
  --control-host HOST     HTTPS control hostname (required with --remote-name). It MUST
                          be a DNS-only (grey cloud) record pointing at the new IP:
                          a proxied record makes Cloudflare answer the control client
                          with 403 error 1010.
  --skip-host-prep        only do the Sandbox registration
  --dry-run               print the commands, run nothing
  --confirm               required to change anything
  --yes                   skip the interactive prompt
  -h, --help              this help
EOF
}

ROOT_SSH="" USER_NAME="" SWAP_SIZE=8G SWAPPINESS=10 OPEN_UFW=0
REMOTE_NAME="" SSH_URL="" CONTROL_HOST="" SKIP_PREP=0
while [ $# -gt 0 ]; do
  common_flag "$1" && { shift; continue; }
  case $1 in
    -h | --help) usage; exit 0 ;;
    --root-ssh) require_value "$1" "${2:-}"; ROOT_SSH=$2; shift 2 ;;
    --user) require_value "$1" "${2:-}"; USER_NAME=$2; shift 2 ;;
    --swap-size) require_value "$1" "${2:-}"; SWAP_SIZE=$2; shift 2 ;;
    --swappiness) require_value "$1" "${2:-}"; SWAPPINESS=$2; shift 2 ;;
    --open-ufw) OPEN_UFW=1; shift ;;
    --remote-name) require_value "$1" "${2:-}"; REMOTE_NAME=$2; shift 2 ;;
    --ssh-url) require_value "$1" "${2:-}"; SSH_URL=$2; shift 2 ;;
    --control-host) require_value "$1" "${2:-}"; CONTROL_HOST=$2; shift 2 ;;
    --skip-host-prep) SKIP_PREP=1; shift ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done

if [ "$SKIP_PREP" = 0 ]; then
  require_set --root-ssh "$ROOT_SSH"
  require_set --user "$USER_NAME"
  check_name --user "$USER_NAME"
  case $SWAP_SIZE in 0 | [1-9]*[GM]) ;; *) usage_error "--swap-size must look like 8G, 4096M or 0" ;; esac
  case $SWAPPINESS in '' | *[!0-9]*) usage_error "--swappiness must be a number" ;; esac
fi
if [ -n "$REMOTE_NAME" ]; then
  check_name --remote-name "$REMOTE_NAME"
  require_set --ssh-url "$SSH_URL"
  require_set --control-host "$CONTROL_HOST"
fi
[ "$SKIP_PREP" = 0 ] || [ -n "$REMOTE_NAME" ] || usage_error "nothing to do"

if [ "$SKIP_PREP" = 0 ]; then
  confirm_or_die "prepare $ROOT_SSH for user $USER_NAME (sudoers, docker group, swap $SWAP_SIZE)"
  sudoers="/etc/sudoers.d/90-sandbox-$USER_NAME"
  prep=$(cat <<EOF
set -eu
SUDO=; [ "\$(id -u)" = 0 ] || SUDO="sudo -n"
id $(shq "$USER_NAME") >/dev/null
tmp=\$(mktemp)
printf '%s ALL=(ALL) NOPASSWD:ALL\n' $(shq "$USER_NAME") > "\$tmp"
\$SUDO visudo -cf "\$tmp"
\$SUDO install -o root -g root -m 0440 "\$tmp" $(shq "$sudoers")
rm -f "\$tmp"
\$SUDO visudo -c >/dev/null
getent group docker >/dev/null || { echo 'docker group missing: install Docker first' >&2; exit 1; }
\$SUDO usermod -aG docker $(shq "$USER_NAME")
EOF
)
  if [ "$SWAP_SIZE" != 0 ]; then
    prep="$prep
if ! \$SUDO swapon --show=NAME --noheadings | grep -qx /swapfile-sandbox; then
  if [ ! -f /swapfile-sandbox ]; then
    \$SUDO fallocate -l $SWAP_SIZE /swapfile-sandbox || \$SUDO dd if=/dev/zero of=/swapfile-sandbox bs=1M count=\$(( \$(numfmt --from=iec $SWAP_SIZE) / 1048576 ))
    \$SUDO chmod 600 /swapfile-sandbox
    \$SUDO mkswap /swapfile-sandbox
  fi
  \$SUDO swapon /swapfile-sandbox
fi
grep -q '^/swapfile-sandbox ' /etc/fstab || echo '/swapfile-sandbox none swap sw 0 0' | \$SUDO tee -a /etc/fstab >/dev/null
echo 'vm.swappiness=$SWAPPINESS' | \$SUDO tee /etc/sysctl.d/90-sandbox-swap.conf >/dev/null
\$SUDO sysctl -q -p /etc/sysctl.d/90-sandbox-swap.conf"
  fi
  prep="$prep
if command -v ufw >/dev/null 2>&1 && \$SUDO ufw status | grep -q '^Status: active'; then
  for port in 80 443; do
    if \$SUDO ufw status | grep -Eq \"^\$port(/tcp)?[[:space:]]+ALLOW\"; then echo \"ufw: \$port allowed\";
    elif [ $OPEN_UFW = 1 ]; then \$SUDO ufw allow \"\$port/tcp\"; else echo \"ufw: \$port NOT allowed (re-run with --open-ufw)\" >&2; fi
  done
else
  echo 'ufw: inactive or not installed (check the provider firewall for 80/443 instead)'
fi
\$SUDO swapon --show
echo 'host prep done; $USER_NAME must log in again for the docker group to apply'"
  remote_bash "$ROOT_SSH" "$prep"
fi

if [ -n "$REMOTE_NAME" ]; then
  confirm_or_die "register and provision Sandbox remote $REMOTE_NAME with the nginx front door"
  log "control host $CONTROL_HOST must be DNS-only (not proxied) and point at the new server"
  run "$SB" remote add "$REMOTE_NAME" "$SSH_URL" --front-door nginx
  run "$SB" remote provision "$REMOTE_NAME" --control https --control-host "$CONTROL_HOST" \
    --front-door nginx --confirm
  run "$SB" remote edge "$REMOTE_NAME"
fi
