"""Server-side teardown of expired or merged temporary targets (spec 077, R3).

Runs on the host: inside the cleanup routine, through ``sb host stack reap --here``,
and, over SSH, from a controller's ``stack reap --remote`` or safe-tier cleanup.

Order for each target, every step re-runnable:

1. Under the store lock, re-read the record and re-check eligibility; mark it
   ``reaping`` (a later run resumes a ``reaping`` record).
2. List what the target owns (containers, volumes, networks, own image tags, the
   route file, certificates, runtime and source directories) and append one
   manifest intent line per item before anything is removed.
3. Remove the route through the nginx transaction, then the certificates, then
   the stack and runtime directory with ``host destroy``'s own command, then the
   source directory once no environment of the project remains.
4. Under the store lock, mark the record ``reaped``.

Hosted, permanent, unclassified and invalid targets are never touched. Like
``host destroy``, only the nginx front door is supported.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from sandbox.hosting.lifetime import hoststore as hs

APPLY_GRACE_SECONDS = 2 * 3600
TRIGGERS = ("scheduled_routine", "stack_reap", "safe_tier")
_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")

Runner = Callable[[str, int], "subprocess.CompletedProcess[str]"]


def local_runner(command: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True, check=False,
                          timeout=timeout, start_new_session=True)


def _lines(result) -> list[str]:
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


class Manifest:
    """Append-only deletion manifest, same record shape as the resources probe."""

    def __init__(self, home: Path, run_id: str, trigger: str):
        directory = Path(home) / "runtime" / "resources" / "deletions"
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        self.path = directory / f"hosted-{run_id}.jsonl"
        self.run_id, self.trigger, self.seq = run_id, trigger, 0

    def append(self, record: dict) -> None:
        self.seq += 1
        line = {"schema": 1, "run_id": self.run_id, "seq": self.seq, "trigger": self.trigger,
                "at": hs.to_rfc3339(time.time()), **record}
        descriptor = os.open(str(self.path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _candidates(store: hs.Store, now: int) -> tuple[list[dict], list[dict]]:
    """(eligible targets, skipped targets with reasons); reads only."""
    records, invalid = store.load()
    eligible, skipped = [], []
    for key in sorted(records):
        record = records[key]
        verdict = hs.eligibility(record, now)
        if not verdict.eligible:
            continue
        row = {"compose_project": key, "reason": verdict.reason, "state": record.state,
               "slug": record.stack.slug if record.stack else None,
               "owner": record.owner.label, "expires_at": record.expires_at}
        if record.front_door != "nginx":
            skipped.append({**row, "status": "skipped", "reason": "front_door_capability_unavailable"})
            continue
        updated = hs.parse_rfc3339(record.updated_at) or 0
        if record.state == "active" and now - updated < APPLY_GRACE_SECONDS:
            skipped.append({**row, "status": "skipped", "reason": "recently_applied"})
            continue
        eligible.append(row)
    for key in sorted(invalid):
        skipped.append({"compose_project": key, "status": "skipped", "reason": "record_invalid"})
    return eligible, skipped


def plan(home: Path, now: int) -> dict:
    eligible, skipped = _candidates(hs.Store(home), now)
    return {"candidates": eligible, "skipped": skipped}


def _inventory(name: str, paths: dict, route: str, certs: list[str], runner: Runner) -> dict:
    label = shlex.quote(f"label=com.docker.compose.project={name}")
    own = shlex.quote(f"^{re.escape(name)}-[^:]*:")
    command = (
        "set -u; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        "docker info >/dev/null 2>&1 || exit 3; "
        # A failed listing must fail the inventory, never read as empty.
        f"c=$(docker ps -aq --filter {label}) || exit 3; "
        f"v=$(docker volume ls -q --filter {label}) || exit 3; "
        f"n=$(docker network ls -q --filter {label}) || exit 3; "
        f"t=$(docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' --filter {label}) || exit 3; "
        "printf '%s\\n' \"$c\" | sed '/^$/d; s/^/container=/'; "
        "printf '%s\\n' \"$v\" | sed '/^$/d; s/^/volume=/'; "
        "printf '%s\\n' \"$n\" | sed '/^$/d; s/^/network=/'; "
        f"printf '%s\\n' \"$t\" | sort -u | grep -E {own} | sed 's/^/image=/'; "
        f"if $SUDO test -f {shlex.quote(route)}; then echo route={shlex.quote(route)}; fi; "
        + "".join(f"if $SUDO test -e {shlex.quote(path)}; then echo cert={shlex.quote(path)}; fi; "
                  for path in certs)
        + f"if [ -e {shlex.quote(paths['runtime'])} ]; then echo runtime={shlex.quote(paths['runtime'])}; fi; "
        "echo done=1")
    result = runner(command, 120)
    found: dict = {"container": [], "volume": [], "network": [], "image": [], "route": [], "cert": [],
                   "runtime": []}
    lines = _lines(result)
    if result.returncode != 0 or "done=1" not in lines:
        raise RuntimeError(f"inventory failed (exit {result.returncode})")
    for line in lines:
        kind, _sep, value = line.partition("=")
        if kind in found and value:
            found[kind].append(value)
    return found


def _source_removable(home: Path, record: hs.TargetRecord, records: dict) -> bool:
    """Only when no environment of the project remains, on disk or in a record."""
    project_runtime = home / "runtime" / "hosts" / record.project
    if project_runtime.exists():
        return False
    return not any(other.project == record.project and other.compose_project != record.compose_project
                   and other.state != "reaped" for other in records.values())


def _source_command(path: Path) -> str:
    quoted = shlex.quote(str(path))
    return (f"set -u; [ ! -L {quoted} ] || exit 2; rm -rf {quoted} 2>/dev/null || "
            f"sudo -n rm -rf {quoted} || exit 6; [ ! -e {quoted} ] || exit 6; echo removed")


def _teardown(home: Path, record: hs.TargetRecord, store: hs.Store, manifest: Manifest,
              runner: Runner) -> dict:
    from sandbox.hosting import destroy
    from sandbox.hosting.front_door import nginx as nginx_edge
    name = record.compose_project
    if name != destroy.compose_name(record.project, record.environment):
        raise RuntimeError("record identity does not match its compose project")
    paths = destroy._paths(str(home), record.project, record.environment)
    route = destroy._route_file(name, "nginx")
    certs = destroy._cert_dirs(name, "nginx")
    found = _inventory(name, paths, route, certs, runner)
    with store.lock():
        records, _invalid = store.load()
    source = Path(paths["source"])
    remove_source = False
    for kind in ("route", "cert", "container", "volume", "network", "image", "runtime"):
        for item in found[kind]:
            manifest.append({"phase": "intent", "kind": kind, "path": item, "compose_project": name,
                             "reason": record.reap.reason if record.reap else None})
    # Steps run in order; each is safe to repeat after an interruption.
    if found["route"]:
        result = runner(nginx_edge.transaction_command(route, None, "absent", operation="reap",
                                                       remove=True),
                        nginx_edge.TRANSACTION_TIMEOUT_SECONDS + 30)
        if result.returncode != 0:
            raise RuntimeError(f"route removal failed (exit {result.returncode})")
    result = runner(destroy.edge_cleanup_command(name, "nginx"), 120)
    if result.returncode != 0:
        raise RuntimeError(f"certificate removal failed (exit {result.returncode})")
    result = runner(destroy._remote_teardown_command(name, paths), 600)
    if result.returncode != 0 or "removed" not in _lines(result):
        raise RuntimeError(f"stack teardown incomplete (exit {result.returncode})")
    if source.is_dir() and not source.is_symlink() and _source_removable(home, record, records):
        remove_source = True
        manifest.append({"phase": "intent", "kind": "source", "path": str(source), "compose_project": name})
        result = runner(_source_command(source), 300)
        if result.returncode != 0:
            raise RuntimeError(f"source removal failed (exit {result.returncode})")
    return {"removed": {kind: len(items) for kind, items in found.items()}, "source_removed": remove_source}


def reap(home: Path, *, now: int | None = None, run_id: str | None = None, trigger: str = "stack_reap",
         confirm: bool, runner: Runner | None = None, budget_seconds: float = 1500,
         monotonic: Callable[[], float] = time.monotonic) -> dict:
    """Plan or run one reap pass on this host. Never raises for a single target."""
    home = Path(home)
    runner = runner or local_runner
    now = int(time.time()) if now is None else now
    run_id = run_id or secrets.token_hex(16)
    if not _ID.match(run_id) or trigger not in TRIGGERS:
        raise ValueError("invalid run id or trigger")
    store = hs.Store(home)
    eligible, skipped = _candidates(store, now)
    result = {"ok": True, "action": "stack-reap", "mode": "confirm" if confirm else "plan",
              "run_id": run_id, "candidates": eligible, "skipped": skipped, "outcomes": []}
    if not confirm or not eligible:
        return result
    manifest = Manifest(home, run_id, trigger)
    manifest.append({"phase": "run_start", "candidates": len(eligible)})
    result["manifest"] = str(manifest.path)
    started = monotonic()
    caller = None
    for row in eligible:
        key = row["compose_project"]
        if monotonic() - started > budget_seconds:
            result["outcomes"].append({"compose_project": key, "status": "skipped", "reason": "budget_exhausted"})
            continue
        with store.lock():
            records, _invalid = store.load()
            record = records.get(key)
            verdict = hs.eligibility(record, now) if record is not None else None
            if record is None or not verdict.eligible:
                result["outcomes"].append({"compose_project": key, "status": "skipped",
                                           "reason": "changed_since_plan"})
                manifest.append({"phase": "outcome", "path": key, "status": "skipped",
                                 "reason": "changed_since_plan"})
                continue
            if record.state == "active":
                record = replace(record, state="reaping", updated_at=hs.to_rfc3339(now),
                                 reap=hs.ReapInfo(run_id, hs.to_rfc3339(now), verdict.reason))
                store.write(record)
                store.audit("reaping", key, caller, now, run_id=run_id, reason=verdict.reason)
        try:
            removed = _teardown(home, record, store, manifest, runner)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            reason = str(exc)[:200]
            result["outcomes"].append({"compose_project": key, "status": "failed", "reason": reason})
            manifest.append({"phase": "outcome", "path": key, "status": "failed", "reason": reason})
            result["ok"] = False
            continue
        with store.lock():
            records, _invalid = store.load()
            current = records.get(key) or record
            finished = replace(current, state="reaped", updated_at=hs.to_rfc3339(now), reap=replace(
                current.reap or record.reap, finished_at=hs.to_rfc3339(time.time()),
                dns_left=current.dns_mode != "wildcard"))
            store.write(finished)
            store.audit("reaped", key, caller, now, run_id=run_id)
        manifest.append({"phase": "outcome", "path": key, "status": "removed", **removed})
        result["outcomes"].append({"compose_project": key, "status": "reaped", "reason": verdict.reason,
                                   **removed})
    return result


def host_home() -> Path:
    from sandbox.resources.host_guard import host_runtime
    return host_runtime().parent


def run_here(*, confirm: bool, trigger: str = "stack_reap", run_id: str | None = None,
             budget_seconds: float = 1500) -> dict:
    """``sb host stack reap --here``: hold the reclaim guard, skip a busy host."""
    from sandbox.resources.host_guard import apply_transaction_active, try_reclaim_guard
    home = host_home()
    if not confirm:
        return reap(home, confirm=False, trigger=trigger, run_id=run_id)
    if apply_transaction_active():
        return {"ok": True, "action": "stack-reap", "mode": "confirm", "status": "skipped_busy",
                "reason": "apply_transaction_active", "candidates": [], "outcomes": []}
    with try_reclaim_guard(runtime=home / "runtime") as acquired:
        if not acquired:
            return {"ok": True, "action": "stack-reap", "mode": "confirm", "status": "skipped_busy",
                    "reason": "host_reclaim_busy", "candidates": [], "outcomes": []}
        return reap(home, confirm=True, trigger=trigger, run_id=run_id, budget_seconds=budget_seconds)


REAP_UNAVAILABLE = "stack_reap_unavailable"


def run_remote(entry: dict, remote_name: str, *, confirm: bool, trigger: str = "stack_reap") -> dict:
    """Run the pass on ``entry`` through its installed Sandbox runtime."""
    import sandbox.core._remote as remote
    from sandbox.core._hosting import HostingRefusal
    sb = remote.remote_sb_path(entry)
    argv = [sb, "host", "stack", "reap", "--here", "--confirm" if confirm else "--plan",
            "--trigger", trigger, "--json"]
    result = remote.ssh_run(entry, shlex.join(argv), timeout=1800)
    lines = [line for line in (result.stdout or "").splitlines() if line.strip().startswith("{")]
    try:
        payload = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        detail = remote.redact_text((result.stderr or result.stdout or "").strip())[-300:]
        if "invalid choice" in detail or "unrecognized arguments" in detail:
            raise HostingRefusal(REAP_UNAVAILABLE, "the remote runtime has no stack reap yet; migrate it "
                                 "(sb remote service migrate) first")
        raise HostingRefusal(REAP_UNAVAILABLE, f"stack reap on the remote gave no result "
                             f"(exit {result.returncode}): {detail}")
    if not payload.get("ok") and payload.get("code"):
        raise HostingRefusal(str(payload["code"]), str(payload.get("error") or "refused"))
    payload["remote"] = remote_name
    return payload
