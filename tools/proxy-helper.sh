#!/usr/bin/env bash
# Sandbox HTTPS-proxy helper — the ONLY privileged host actions the sandbox
# proxy needs (loopback alias + dnsmasq/resolver for the *.tst TLD).
#
# Deliberately tiny, argument-fixed, and strict so it's safe to allow via a
# scoped passwordless sudoers rule (see `./sb domains setup`). Each action is a
# single fixed command — there is nothing to inject. mkcert is NOT here: it's
# inherently interactive (writes to the login keychain) and runs as the user.
#
# Usage:
#   proxy-helper.sh alias-up      # macOS: ifconfig lo0 alias 127.0.0.77 (idempotent)
#                                 # Linux: no-op — 127.0.0.0/8 already routes to lo
#   proxy-helper.sh alias-down    # inverse of alias-up
#   proxy-helper.sh dns-up <tld>  # write *.<tld> -> 127.0.0.77
#                                 #   macOS: /etc/resolver/<tld> + brew dnsmasq
#                                 #   Linux: our own dnsmasq + /etc/resolv.conf
#   proxy-helper.sh dns-down <tld># remove that TLD's dnsmasq + resolver entries
#
# Linux support (added 2026-07-09, live-verified in a real Ubuntu container —
# see docs/cross-platform-support.md §8): Linux has no lo0-style alias
# restriction (the whole 127.0.0.0/8 range already routes to loopback with
# zero setup — verified: `nc -l 127.0.0.77` accepted connections with no prior
# `ip addr add`) and no `/etc/resolver/<tld>` mechanism, so this runs its OWN
# dnsmasq instance (bound to 127.0.0.1 only, forwarding non-wildcard queries
# to the machine's REAL upstream resolvers) and points /etc/resolv.conf at it.
# Verified end-to-end: a wildcard `*.tst` name resolves to 127.0.0.77 AND a
# real internet domain still resolves via forwarding — both in one dnsmasq
# process, matching the macOS design's own behavior exactly.
#
# Deliberately conservative about when to touch /etc/resolv.conf: only when
# it's a PLAIN regular file (not a symlink) — a symlinked resolv.conf almost
# always means systemd-resolved or NetworkManager manages DNS on this system,
# and fighting either of those (revert races, DBus-based reconfiguration) was
# not something this could be verified safely without a real desktop Linux
# environment (nested-Docker systemd/DBus doesn't behave like a real boot).
# On such systems dns-up declines cleanly (exit 3) rather than guess — the
# caller (sandbox/core/_domains.py) already treats that as "fall back to
# localhost:<port>", same as any other proxy-setup failure.
#
# Privilege model (restored 2026-08-02, hardened): the NOPASSWD sudoers rule
# NEVER points at this repository copy. `install` (an ordinary interactive sudo
# action) copies this script to a root-owned /usr/local/libexec/sandbox-proxy-
# helper and writes /etc/sudoers.d/sandbox-proxy-<uid> naming ONLY that
# immutable path and its fixed actions — the same shape tools/resolver-helper.sh
# uses. Every privileged action below refuses to run from any other path, so a
# stale rule on a user-writable checkout cannot be exploited.
#
# Do not disable or stub this helper: the Docker/Caddy clean-URL stack is the
# product default (specs 037 FR-007/FR-033, 038 FR-029/FR-032). Removing it
# needs live parity of the replacement plus explicit approval.
set -euo pipefail

OS="$(uname -s)"
ALIAS_IP=127.0.0.77
BREW_PREFIX="${HOMEBREW_PREFIX:-/opt/homebrew}"
# Local sandbox TLD — passed as $2 by `sb` (from sandbox.config.json `tld`,
# default `tst`). Each TLD gets its own resolver + dnsmasq file, so several can
# coexist. We avoid `.sb` (a real ccTLD) and `.test` (owned by Herd/Valet).
TLD="${2:-tst}"
DNSMASQ_CONF="$BREW_PREFIX/etc/dnsmasq.d/sandbox-$TLD.conf"
RESOLVER=/etc/resolver/$TLD

# --- Linux-only paths (unused on macOS) -------------------------------------
LINUX_STATE_DIR=/etc/sandbox-dnsmasq
LINUX_CONF_DIR="$LINUX_STATE_DIR/conf.d"
LINUX_TLD_CONF="$LINUX_CONF_DIR/$TLD.conf"
LINUX_UPSTREAM_BACKUP="$LINUX_STATE_DIR/resolv.conf.upstream"
LINUX_PIDFILE=/run/sandbox-dnsmasq.pid
RESOLV_CONF=/etc/resolv.conf

reload_dnsmasq() {
  # Reload the RUNNING dnsmasq + clear its cache, however it's managed. We've
  # seen dnsmasq be Valet-run (root, NOT brew-managed), so `brew services
  # restart` silently no-ops and a stale cached record (e.g. xspeed.tst pointing
  # at an old IP) shadows the wildcard. So: SIGHUP every live dnsmasq by PID
  # (clears cache + re-reads config — works regardless of manager), and also
  # try brew/launchd as a belt-and-suspenders. All best-effort.
  local pids
  pids="$(pgrep dnsmasq 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    # shellcheck disable=SC2086
    kill -HUP $pids 2>/dev/null || true
  fi
  command -v brew >/dev/null 2>&1 && brew services restart dnsmasq >/dev/null 2>&1 || true
}

flush_macos_dns() {
  # Drop macOS's own resolver cache so it re-queries dnsmasq for *.tst names.
  dscacheutil -flushcache 2>/dev/null || true
  killall -HUP mDNSResponder 2>/dev/null || true
}

linux_resolv_conf_is_plain_file() {
  # A symlinked resolv.conf (systemd-resolved's /run/systemd/resolve/... stub,
  # or a NetworkManager-managed path) means something ELSE owns DNS on this
  # box — overwriting it would either get silently reverted or fight that
  # service. A plain regular file (common on servers, WSL2's default config,
  # minimal/container-style installs) is the only case handled here.
  [ -f "$RESOLV_CONF" ] && [ ! -L "$RESOLV_CONF" ]
}

linux_port53_free_or_ours() {
  # True if 127.0.0.1:53 is unused, OR already held by OUR OWN prior dnsmasq
  # (tracked via LINUX_PIDFILE, verified by PID liveness + process name — NOT
  # by parsing `ss`'s process-owner column, which doesn't reliably appear in
  # every container/kernel config: verified live that `ss -lntpu` can list a
  # real bound listener with NO "users:(...)" field at all, which made an
  # earlier version of this check misread the unrelated 0.0.0.0:* peer-address
  # field as if it were a PID — a false positive that rejected restarting our
  # OWN already-running dnsmasq when adding a second TLD). If our pidfile
  # names a live dnsmasq process, trust that outright; only probe the port's
  # mere existence (not identity) when it doesn't.
  if [ -f "$LINUX_PIDFILE" ]; then
    local pid; pid="$(cat "$LINUX_PIDFILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null \
       && grep -q dnsmasq "/proc/$pid/comm" 2>/dev/null; then
      return 0
    fi
  fi
  ! ss -ltnu 2>/dev/null | grep -q '127\.0\.0\.1:53 '
}

linux_dns_up() {
  if ! linux_resolv_conf_is_plain_file; then
    echo "proxy-helper: /etc/resolv.conf is managed elsewhere (symlink — likely" >&2
    echo "  systemd-resolved or NetworkManager) — automatic *.$TLD wildcard DNS" >&2
    echo "  isn't implemented for that case yet. Falling back to localhost:<port>." >&2
    exit 3
  fi
  if ! linux_port53_free_or_ours; then
    echo "proxy-helper: 127.0.0.1:53 is already in use by another process —" >&2
    echo "  refusing to take over DNS. Falling back to localhost:<port>." >&2
    exit 3
  fi
  mkdir -p "$LINUX_CONF_DIR"
  printf '# Generated by ./sb — sandbox *.%s TLD. Safe to delete.\naddress=/.%s/%s\n' \
    "$TLD" "$TLD" "$ALIAS_IP" > "$LINUX_TLD_CONF"
  # Capture the machine's REAL upstream resolvers exactly once — before we
  # ever point resolv.conf at ourselves. Re-running dns-up for another TLD
  # must never re-capture our OWN override as "upstream".
  if [ ! -f "$LINUX_UPSTREAM_BACKUP" ]; then
    cp "$RESOLV_CONF" "$LINUX_UPSTREAM_BACKUP"
  fi
  # ALWAYS fully restart (not SIGHUP) when the file SET in conf-dir changes:
  # verified live that dnsmasq's SIGHUP does NOT pick up a file newly ADDED to
  # --conf-dir after the initial start (a second `dns-up <other-tld>` kept
  # resolving only the FIRST tld's wildcard — the new file was on disk and
  # correctly formatted, but silently ignored until a real restart). SIGHUP
  # remains the right, verified-working tool for dns-flush (no file-set
  # change, just clearing cached answers).
  if [ -f "$LINUX_PIDFILE" ] && kill -0 "$(cat "$LINUX_PIDFILE")" 2>/dev/null; then
    kill "$(cat "$LINUX_PIDFILE")"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$(cat "$LINUX_PIDFILE")" 2>/dev/null || break
      sleep 0.2
    done
  fi
  dnsmasq --conf-dir="$LINUX_CONF_DIR" --resolv-file="$LINUX_UPSTREAM_BACKUP" \
    --no-hosts --listen-address=127.0.0.1 --bind-interfaces \
    --pid-file="$LINUX_PIDFILE"
  echo "nameserver 127.0.0.1" > "$RESOLV_CONF"
}

linux_dns_down() {
  rm -f "$LINUX_TLD_CONF"
  if [ -z "$(ls -A "$LINUX_CONF_DIR" 2>/dev/null)" ]; then
    # No sandbox TLDs left — stop our dnsmasq and restore the real resolvers.
    if [ -f "$LINUX_PIDFILE" ]; then
      kill "$(cat "$LINUX_PIDFILE")" 2>/dev/null || true
      rm -f "$LINUX_PIDFILE"
    fi
    if [ -f "$LINUX_UPSTREAM_BACKUP" ]; then
      cp "$LINUX_UPSTREAM_BACKUP" "$RESOLV_CONF"
      rm -f "$LINUX_UPSTREAM_BACKUP"
    fi
  elif [ -f "$LINUX_PIDFILE" ] && kill -0 "$(cat "$LINUX_PIDFILE")" 2>/dev/null; then
    # Other TLDs remain — full restart, not SIGHUP (see linux_dns_up: SIGHUP
    # doesn't reliably notice conf-dir file-set CHANGES, only verified to
    # clear the cache for an unchanged file set).
    kill "$(cat "$LINUX_PIDFILE")"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$(cat "$LINUX_PIDFILE")" 2>/dev/null || break
      sleep 0.2
    done
    dnsmasq --conf-dir="$LINUX_CONF_DIR" --resolv-file="$LINUX_UPSTREAM_BACKUP" \
      --no-hosts --listen-address=127.0.0.1 --bind-interfaces \
      --pid-file="$LINUX_PIDFILE"
  fi
}


INSTALLED_HELPER=/usr/local/libexec/sandbox-proxy-helper

fail() {
  echo "proxy-helper: $1" >&2
  exit 1
}

require_root() {
  [ "$(id -u)" -eq 0 ] || fail "this action must run through sudo"
}

require_installed_helper() {
  # Privileged actions may only execute from the root-owned installed copy, so
  # a sudoers rule can never reach a user-writable path.
  self=$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")
  [ "$self" = "$INSTALLED_HELPER" ] \
    || fail "privileged actions require $INSTALLED_HELPER (run: sudo tools/proxy-helper.sh install)"
  [ ! -L "$INSTALLED_HELPER" ] || fail "installed helper must not be a symlink"
  owner=$(stat -f '%u %p' "$INSTALLED_HELPER" 2>/dev/null \
    || stat -c '%u %a' "$INSTALLED_HELPER" 2>/dev/null) \
    || fail "installed helper is unreadable"
  case "$owner" in
    "0 "*) : ;;
    *) fail "installed helper must be owned by root" ;;
  esac
}

install_helper() {
  require_root
  [ ! -L "$0" ] || fail "helper source must not be a symlink"
  source_directory=$(cd "$(dirname "$0")" && pwd -P) || fail "could not resolve helper source"
  source_path="$source_directory/$(basename "$0")"
  [ -n "${SUDO_UID:-}" ] && [ -n "${SUDO_USER:-}" ] || fail "install must be invoked by a sudo user"
  case "$SUDO_UID" in ''|*[!0-9]*) fail "invalid sudo user" ;; esac
  [ "$SUDO_UID" -gt 0 ] || fail "refusing a root-owned clean-URL policy"
  login=$(id -un "$SUDO_UID" 2>/dev/null) || fail "sudo user is unavailable"
  [ "$login" = "$SUDO_USER" ] || fail "sudo identity mismatch"
  install -d -o root -g "$(id -gn 0)" -m 0755 /usr/local/libexec
  install -o root -g "$(id -gn 0)" -m 0755 -- "$source_path" "$INSTALLED_HELPER"
  sudoers="/etc/sudoers.d/sandbox-proxy-$SUDO_UID"
  sudoers_temporary="$sudoers.new.$$"
  trap 'rm -f -- "$sudoers_temporary"' EXIT HUP INT TERM
  allowed="$INSTALLED_HELPER installed-status, $INSTALLED_HELPER alias-up, $INSTALLED_HELPER alias-down, $INSTALLED_HELPER dns-up *, $INSTALLED_HELPER dns-down *, $INSTALLED_HELPER dns-flush"
  printf '%s ALL=(root) NOPASSWD: %s\n' "$login" "$allowed" > "$sudoers_temporary"
  chown root:"$(id -gn 0)" "$sudoers_temporary"
  chmod 0440 "$sudoers_temporary"
  visudo -cf "$sudoers_temporary" >/dev/null || fail "clean-URL sudo policy validation failed"
  mv -f -- "$sudoers_temporary" "$sudoers"
  trap - EXIT HUP INT TERM
}

action="${1:-}"
case "$action" in
  install)
    install_helper
    ;;
  installed-status)
    require_root
    require_installed_helper
    echo ready
    exit 0
    ;;
  alias-up)
    require_root
    require_installed_helper
    if [ "$OS" = "Linux" ]; then
      : # no-op — 127.0.0.0/8 already routes to lo, no alias needed (verified)
    else
      # Idempotent: adding an existing alias is a no-op that returns 0.
      ifconfig lo0 alias "$ALIAS_IP" up
    fi
    ;;
  alias-down)
    require_root
    require_installed_helper
    if [ "$OS" = "Linux" ]; then
      : # no-op — see alias-up
    else
      ifconfig lo0 -alias "$ALIAS_IP" 2>/dev/null || true
    fi
    ;;
  dns-up)
    require_root
    require_installed_helper
    if [ "$OS" = "Linux" ]; then
      linux_dns_up
    else
      mkdir -p "$(dirname "$DNSMASQ_CONF")"
      printf '# Generated by ./sb — sandbox *.%s TLD. Safe to delete.\naddress=/.%s/%s\n' \
        "$TLD" "$TLD" "$ALIAS_IP" > "$DNSMASQ_CONF"
      mkdir -p /etc/resolver
      printf 'nameserver 127.0.0.1\n' > "$RESOLVER"
      reload_dnsmasq
      flush_macos_dns
    fi
    ;;
  dns-down)
    require_root
    require_installed_helper
    if [ "$OS" = "Linux" ]; then
      linux_dns_down
    else
      rm -f "$DNSMASQ_CONF" "$RESOLVER"
      reload_dnsmasq
      flush_macos_dns
    fi
    ;;
  dns-flush)
    require_root
    require_installed_helper
    # Self-heal: reload dnsmasq (drop stale cached *.tst records) + flush the
    # OS cache. Called automatically by `sb` after any domain change, so the
    # user never has to run a terminal command to fix resolution.
    if [ "$OS" = "Linux" ]; then
      if [ -f "$LINUX_PIDFILE" ] && kill -0 "$(cat "$LINUX_PIDFILE")" 2>/dev/null; then
        kill -HUP "$(cat "$LINUX_PIDFILE")"
      fi
    else
      reload_dnsmasq
      flush_macos_dns
    fi
    ;;
  *)
    echo "proxy-helper: unknown action '$action'" >&2
    echo "usage: proxy-helper.sh install|installed-status|alias-up|alias-down|dns-up|dns-down|dns-flush" >&2
    exit 2
    ;;
esac
echo "proxy-helper: $action ok"
