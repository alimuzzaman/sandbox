#!/usr/bin/env bash
# Sandbox /etc/hosts helper — the ONLY thing that touches /etc/hosts.
#
# Deliberately tiny and strict so it's safe to allow via a scoped passwordless
# sudoers rule (see `./sb domains setup`). It manages a single marked block:
#
#   # >>> sandbox domains >>>
#   127.0.0.1   xspeed.tst
#   127.0.0.1   embedpress.tst
#   # <<< sandbox domains <<<
#
# Usage:
#   hosts-helper.sh add <domain>
#   hosts-helper.sh remove <domain>
#
# Only operates inside the marked block; never edits anything else. Domains are
# validated to a safe hostname charset so this can't be abused to inject lines.
set -euo pipefail

HOSTS=/etc/hosts
BEGIN="# >>> sandbox domains >>>"
END="# <<< sandbox domains <<<"

action="${1:-}"
domain="${2:-}"

# Strict hostname validation: labels of [a-z0-9-], dot-separated, max 253.
if ! printf '%s' "$domain" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$'; then
  echo "hosts-helper: invalid domain '$domain'" >&2; exit 2
fi
if [ "${#domain}" -gt 253 ]; then echo "hosts-helper: domain too long" >&2; exit 2; fi

umask 022
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

# Ensure the marked block exists; copy current hosts, then operate on it.
touch "$HOSTS"
cp "$HOSTS" "$tmp"
if ! grep -qF "$BEGIN" "$tmp"; then
  { printf '\n%s\n%s\n' "$BEGIN" "$END"; } >> "$tmp"
fi

# Always strip any existing line for this domain inside the block, then (for
# add) re-insert it. Done with awk so only the block is touched.
awk -v b="$BEGIN" -v e="$END" -v d="$domain" -v act="$action" '
  $0==b { inblock=1; print; next }
  $0==e {
    if (act=="add") print "127.0.0.1\t" d
    inblock=0; print; next
  }
  inblock {
    # skip any existing mapping for this exact domain (2nd field match)
    n=split($0, f, /[ \t]+/)
    if (n>=2 && f[2]==d) next
    print; next
  }
  { print }
' "$tmp" > "$tmp.2" && mv "$tmp.2" "$tmp"

# Atomic install (preserve perms/owner of /etc/hosts).
cat "$tmp" > "$HOSTS"
echo "hosts-helper: $action ok — $domain"
