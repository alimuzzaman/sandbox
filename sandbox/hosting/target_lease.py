"""One remote lease per hosted target (spec 081 rollout).

Every ``sb host apply``, edge-only recovery and ``sb host destroy`` holds it for the
whole operation, so two controllers cannot interleave changes to one target's route
and runtime. The lease never expires on its own: an expired owner that kept writing
would be the interleaving it exists to stop. The controller that holds it releases it,
or takes it over on its next operation (its own state lock already serializes its own
operations). A lease left by another controller is removed by hand, once nothing of
that controller still runs (docs/remote-hosting.md, `host_apply_lease_held`). A
server-owner job's lease is recorded in its job events and taken over once that job
is proven ended (sandbox/application/server_cli_apply_lease.py).

Stdlib only.
"""
from __future__ import annotations

import os
import re
import secrets
import shlex
from pathlib import Path

HELD_EXIT = 45

SCRIPT = r"""set -eu
f=$1; token=$2; owner=$3; action=$4
# The physical anchor and its directory must satisfy exact exec recovery's
# owner-only policy. Set this before creating either; changing umask later only
# protects the lease body.
umask 077
d=$(dirname "$f")
mkdir -p "$d"
# A legacy directory made under a group-writable umask is refused by exact exec
# recovery. Drop group/other write on it only when it is a real directory we own
# whose resolved path is itself.
if [ -d "$d" ] && [ ! -L "$d" ] && [ -O "$d" ] && [ "$(cd "$d" && pwd -P)" = "$d" ]; then
  chmod go-w "$d" 2>/dev/null || true
fi
exec 9>"$f.lock"
flock -w 30 9 || exit 46
# A legacy anchor created before that umask is group-writable, which exact exec
# recovery and the owner panel refuse. Tighten it in place under its flock: only the
# open inode itself, when it is the path's own (not a symlink target), a regular file
# we own with one link. Permissions are checked only at open, so a holder's open
# descriptor is unaffected. Never replace the inode.
a=/proc/$$/fd/9
if [ -f "$a" ] && [ -O "$a" ] \
    && [ "$(stat -c %d:%i "$f.lock" 2>/dev/null)" = "$(stat -L -c %d:%i "$a" 2>/dev/null)" ] \
    && [ "$(stat -L -c %h "$a" 2>/dev/null)" = 1 ] && [ "$(stat -L -c %a "$a" 2>/dev/null)" != 600 ]; then
  chmod 600 "$a" 2>/dev/null || true
fi
t=; o=; since=
if [ -f "$f" ]; then read -r t o since < "$f" || true; fi
case "$action" in
acquire)
  if [ -n "$t" ] && [ "$o" != "$owner" ]; then printf '%s\n' "$since"; exit 45; fi
  printf '%s %s %s\n' "$token" "$owner" "$(date +%s)" > "$f.new"
  mv -f "$f.new" "$f" ;;
release)
  if [ "$t" = "$token" ]; then rm -f "$f"; fi ;;
*) exit 2 ;;
esac
"""


class LeaseHeld(Exception):
    """Another controller holds the target's lease."""


class LeaseUnavailable(Exception):
    """The lease could not be read or written."""


def lease_path(home: str, project: str, environment: str) -> str:
    return f"{home}/.sandbox/host-apply-{project}-{environment}.lease"


def command(path: str, token: str, owner: str, action: str) -> str:
    return (f"sh -c {shlex.quote(SCRIPT)} sandbox-lease {shlex.quote(path)} "
            f"{token} {owner} {action}")


def controller_owner(runtime_dir) -> str:
    """A stable random id for this controller (never a secret, never a hostname)."""
    path = Path(runtime_dir) / "hosting" / "controller-id"
    try:
        value = path.read_text().strip()
        if re.fullmatch(r"[0-9a-f]{32}", value):
            return value
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(16)
    temporary = path.with_name(f".controller-id.{value}")
    temporary.write_text(value + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return value


def acquire(run, path: str, owner: str) -> dict:
    """``run(command) -> CompletedProcess``. Returns the lease to pass to ``release``."""
    lease = {"path": path, "token": secrets.token_hex(16), "owner": owner}
    result = run(command(path, lease["token"], owner, "acquire"))
    if result.returncode == HELD_EXIT:
        since = (result.stdout or "").strip()
        raise LeaseHeld("another controller holds this target's lease"
                        + (f" (since unix time {since})" if since.isdigit() else ""))
    if result.returncode != 0:
        raise LeaseUnavailable(f"the target lease could not be taken (exit {result.returncode})")
    return lease


def release(run, lease: dict | None) -> None:
    if lease is None:
        return
    try:
        run(command(lease["path"], lease["token"], lease["owner"], "release"))
    except Exception:
        pass  # The next operation of this controller takes it over.
