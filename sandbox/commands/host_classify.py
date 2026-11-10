"""`sb host classify`: give an existing hosted target a lifetime (spec 077, FR-009).

A target applied outside `host stack` has no lifetime record and stays protected
from server cleanup. This writes (or with ``--unclassify`` removes) its record on
the remote without redeploying it. Stacks are managed with `sb host stack ttl`.
"""
from __future__ import annotations

import re
from pathlib import Path

import sandbox.core._hosting as hosting
from sandbox.commands import host_stack
from sandbox.hosting.lifetime import hoststore


def _classify(args) -> dict:
    if not getattr(args, "confirm", False):
        raise host_stack.StackCommandError(host_stack.CONFIRMATION_REQUIRED,
                                           "host classify writes the target's lifetime; pass --confirm")
    target = str(getattr(args, "target", None) or "")
    project, sep, environment = target.partition("/")
    if not sep or not hoststore.TARGET_PART_RE.fullmatch(project or "") \
            or not hoststore.TARGET_PART_RE.fullmatch(environment or ""):
        raise host_stack.StackCommandError(hoststore.REQUEST_INVALID, "--target must be PROJECT/ENVIRONMENT")
    clear = bool(getattr(args, "unclassify", False))
    lifetime = getattr(args, "lifetime", None)
    if clear == (lifetime is not None):
        raise host_stack.StackCommandError(host_stack.CONFIRMATION_REQUIRED,
                                           "host classify needs exactly one of --lifetime or --unclassify")
    ttl = None
    if getattr(args, "ttl", None) is not None:
        if lifetime != "temporary":
            raise host_stack.StackCommandError(hoststore.TTL_INVALID, "--ttl is only for --lifetime temporary")
        try:
            ttl = hoststore.parse_ttl(args.ttl)
        except hoststore.StoreRefusal as exc:
            raise host_stack.StackCommandError(exc.code, exc.message) from None
    entry = host_stack._entry(getattr(args, "remote", None))
    from sandbox.hosting.front_door import front_door_mode
    store = host_stack._store(entry, host_stack._caller(Path.cwd(), args))
    known = _known_identity(args.remote, project, environment)
    result = store.call("classify", project=project, environment=environment, lifetime=lifetime,
                        ttl_seconds=ttl, clear=clear, force_owner=bool(getattr(args, "force_owner", False)),
                        front_door=front_door_mode(entry), **known)
    return {"ok": True, "action": "host-classify", "remote": args.remote, "target": target,
            "record": result.get("record"), "removed": result.get("removed")}


def _known_identity(remote_name: str, project: str, environment: str) -> dict:
    """The commit and hostnames this controller recorded for the target, if any."""
    try:
        record = (hosting.load_host_state().get("hosts") or {}).get(f"{remote_name}/{project}/{environment}")
    except (hosting.HostingError, OSError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    known = {}
    commit = record.get("commit")
    if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
        known["commit"] = commit
    hostnames = record.get("hostnames")
    if isinstance(hostnames, list) and all(isinstance(item, str) for item in hostnames):
        known["hostnames"] = hostnames
    return known


def cmd_host_classify(args) -> None:
    try:
        payload = _classify(args)
    except (hosting.HostingError, RuntimeError, OSError) as exc:
        host_stack._refuse(args, exc)
        return
    record = payload["record"]
    if record is None:
        text = f"{payload['target']} on {payload['remote']}: unclassified (protected from server cleanup)"
    else:
        expires = record.get("expires_at") or "never"
        text = (f"{payload['target']} on {payload['remote']}: {record['lifetime']} "
                f"(classified), expires {expires}")
    host_stack._emit(args, payload, text)
