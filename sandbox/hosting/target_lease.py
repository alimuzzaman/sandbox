"""One remote lease per hosted target (spec 081 rollout).

Every ``sb host apply``, edge-only recovery and ``sb host destroy`` holds it for the
whole operation, so two controllers cannot interleave changes to one target's route
and runtime. The lease never expires on its own: an expired owner that kept writing
would be the interleaving it exists to stop. The controller that holds it releases it,
or takes it over on its next operation (its own state lock already serializes its own
operations). A lease left by another controller is removed by hand, once nothing of
that controller still runs (docs/remote-hosting.md, `host_apply_lease_held`).

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
mkdir -p "$(dirname "$f")"
# The physical anchor must satisfy exact exec recovery's owner-only policy.
# Set this before opening it; changing umask later only protects the lease body.
umask 077
exec 9>"$f.lock"
flock -w 30 9 || exit 46
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
