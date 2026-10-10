"""Run the readiness rows under one deadline and keep a reusable proof.

The rows run concurrently; any row unfinished at the 60 s deadline becomes
``unknown`` (``timeout``). A proof is stored per remote and project at
``$SANDBOX_HOME/runtime/readiness/<remote>/<project_sha16>.json`` (0600) and
is reused for 300 s only when it has no ``not_ready`` row and the remote's
recorded installed revision is unchanged. Installs (``service migrate``,
``provision``, ``up``) and ``network-range assign`` delete the remote's proofs
and rotate its ``generation`` token; a proof carries the token read before its
probes ran, so a check still in flight across an invalidation publishes a proof
that is never reused.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sandbox.readiness import rows

DEADLINE_SECONDS = 60
REUSE_SECONDS = 300
_REMOTE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_PROJECT_KEY = re.compile(r"[0-9a-f]{16}")


@dataclass
class Probes:
    """Existing probes the rows reuse; injected so tests run without SSH."""

    resolve: Callable          # (project_dir, remote|None) -> ResolvedTarget
    remote_lookup: Callable    # name -> registration dict or None
    ssh_run: Callable
    service_status: Callable   # registration -> 061 status envelope
    capacity_decision: Callable  # (registration, *, remote_name) -> decision
    sb_path: Callable          # registration -> remote sb path
    propose: Callable          # registration -> {"proposed", "assign_command", ...}
    clock: Callable = time.time
    home: Path | None = None


def default_probes() -> Probes:
    from sandbox.application.context import durable_job_dependencies
    from sandbox.core import _remote
    from sandbox.core._paths import _sandbox_base
    from sandbox.jobs.models import TargetRequest
    from sandbox.remote_network.store import RangeStore

    service = durable_job_dependencies()["target_service"]
    return Probes(
        resolve=lambda project_dir, remote: service.resolve(TargetRequest(
            project_dir=project_dir, remote=remote, required_capability="job.exec")),
        remote_lookup=_remote.get_remote,
        ssh_run=_remote.ssh_run,
        service_status=_remote.remote_mcp_service_status,
        capacity_decision=lambda remote, *, remote_name: _remote.remote_network_capacity_admission(
            remote, remote_name=remote_name),
        sb_path=_remote.remote_sb_path,
        propose=lambda remote: RangeStore(remote, _remote.ssh_run).propose(),
        home=_sandbox_base(),
    )


def readiness_dir(home: Path, remote: str) -> Path:
    if not isinstance(remote, str) or not _REMOTE_NAME.fullmatch(remote):
        raise ValueError("invalid remote name")
    return Path(home) / "runtime" / "readiness" / remote


def project_key(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def recorded_revision(registration: dict | None) -> str | None:
    """The installed runtime revision this controller recorded for the remote."""
    service = (registration or {}).get("mcp_service")
    revision = service.get("runtime_revision") if isinstance(service, dict) else None
    return revision if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{24}", revision) \
        else None


def _write_private(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def invalidate(remote: str, home: Path | None = None) -> int:
    """Delete every reusable proof for ``remote``; the handoff record stays
    (it is bound to the installed revision on its own)."""
    if home is None:
        from sandbox.core._paths import _sandbox_base
        home = _sandbox_base()
    try:
        directory = readiness_dir(home, remote)
    except ValueError:
        return 0
    # Rotate first: a check that read the old token before this point can no
    # longer publish a reusable proof, whatever it writes afterwards.
    _write_private(directory / "generation.json", {"token": secrets.token_hex(16)})
    removed = 0
    for path in directory.glob("*.json"):
        if _PROJECT_KEY.fullmatch(path.stem):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def generation(home: Path, remote: str) -> str | None:
    """The remote's current invalidation token; None until first invalidated."""
    value = _read(readiness_dir(home, remote) / "generation.json")
    token = (value or {}).get("token")
    return token if isinstance(token, str) else None


def reusable(remote: str, identity: str, *, probes: Probes) -> dict | None:
    """A stored proof that may stand in for a fresh check, or None."""
    proof = _read(readiness_dir(probes.home, remote) / f"{project_key(identity)}.json")
    if proof is None or proof.get("remote") != remote or proof.get("project") != identity:
        return None
    if any(item.get("state") == "not_ready" for item in proof.get("rows") or []
           if isinstance(item, dict)):
        return None
    revision = proof.get("installed_runtime_revision")
    if revision is None or revision != recorded_revision(probes.remote_lookup(remote)):
        return None
    if not probes.clock() < proof.get("reusable_until", 0):
        return None
    if proof.get("generation") != generation(probes.home, remote):
        return None
    return proof


def run(project_dir: str, remote: str | None, *, probes: Probes | None = None) -> dict:
    """One readiness result (the contract's ``data``); stores a proof when a
    remote was selected and registered."""
    probes = probes or default_probes()
    taken_at = int(probes.clock())
    result = {"remote": remote, "remote_selection": "explicit" if remote else None,
              "rows": [], "installed_runtime_revision": None,
              "taken_at": taken_at, "reusable_until": taken_at + REUSE_SECONDS}
    try:
        target = probes.resolve(project_dir, remote)
        error_code = None
    except Exception as exc:  # TargetResolutionError and config failures
        target, error_code = None, getattr(exc, "code", None) or "invalid_project"
    if target is not None:
        result["remote_selection"] = (getattr(target, "sources", None) or {}).get(
            "remote_selection")
        if target.kind != "remote":
            # Nothing remote was selected: every row is not applicable.
            result["remote"] = None
            result["rows"] = [rows.not_applicable(aspect, "local_target")
                              for aspect in rows.ASPECTS]
            return result
        remote = result["remote"] = target.remote_name
    if error_code is not None:
        result["rows"] = [rows.registration(error_code, remote)] + [
            rows.not_applicable(aspect, "no_registration") for aspect in rows.ASPECTS[1:]]
        return result

    registration = target.remote or probes.remote_lookup(remote) or {}
    identity = (getattr(target, "sources", None) or {}).get("identity") or target.project_root
    # Read before probing: an invalidation during the probes rotates it.
    token = generation(probes.home, remote)
    evaluated = _evaluate(registration, remote, probes)
    revision = evaluated.pop("_revision", None)
    proposed = evaluated.pop("_proposed", None)
    handoff_record = _read(readiness_dir(probes.home, remote) / "handoff.json")
    if not (isinstance(handoff_record, dict) and handoff_record.get("project") == identity):
        handoff_record = None
    result["rows"] = [rows.registration(None, remote)] + [
        evaluated[aspect] for aspect in rows.ASPECTS[1:5]] + [
        rows.handoff(handoff_record, revision, remote, target.project_root)]
    result["installed_runtime_revision"] = revision
    if proposed is not None:
        result["proposed_range"] = proposed
    _write_private(readiness_dir(probes.home, remote) / f"{project_key(identity)}.json",
                   {**result, "project": identity, "generation": token})
    return result


def _evaluate(registration: dict, name: str, probes: Probes) -> dict:
    """Reachability, compatibility, capacity and repair rows under the deadline."""

    def compatibility():
        value, revision = rows.runtime_compatibility(probes.service_status, registration, name)
        return {"runtime_compatibility": value, "_revision": revision}

    def capacity():
        value = rows.capacity(probes.capacity_decision, registration, name)
        found = {"capacity": value}
        if value.get("reason") == "missing_pool_evidence":
            found["_proposed"] = _proposal(registration, probes)
        return found

    tasks = {
        "reachability": lambda: {"reachability": rows.reachability(
            probes.ssh_run, registration, name)},
        "runtime_compatibility": compatibility,
        "capacity": capacity,
        "ownership_repair": lambda: {"ownership_repair": rows.ownership_repair(
            probes.ssh_run, registration, name, probes.sb_path(registration))},
    }
    found: dict = {}
    lock = threading.Lock()

    def worker(task):
        try:
            value = task()
        except Exception:
            return
        with lock:
            found.update(value)

    # Daemon threads: a probe still running at the deadline neither delays the
    # answer nor keeps the CLI process alive at interpreter exit.
    threads = [threading.Thread(target=worker, args=(task,), daemon=True,
                                name=f"readiness-{aspect}") for aspect, task in tasks.items()]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + DEADLINE_SECONDS
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    with lock:
        found = dict(found)
    for aspect in tasks:
        if aspect not in found:
            found[aspect] = rows.row(aspect, "unknown", probe_state="timeout",
                                     remedy=f"./sb remote readiness {name}")
    return found


def _proposal(registration: dict, probes: Probes) -> dict | None:
    """A proposed range and its assign command, only from a complete inventory."""
    try:
        proposed = probes.propose(registration)
    except Exception:
        return None
    if not isinstance(proposed, dict) or not isinstance(proposed.get("assign_command"), str):
        return None
    return {"cidr": proposed.get("proposed"), "subnet_prefix": proposed.get("subnet_prefix"),
            "assign_command": proposed["assign_command"]}


def record_ensure(remote: str, identity: str, home: Path | None = None) -> None:
    """``ensure --remote`` succeeded; ``exec --remote`` completes the handoff."""
    home = home or _home()
    _write_private(readiness_dir(home, remote) / "ensure.json",
                   {"project": identity, "recorded_at": int(time.time())})


def record_exec(remote: str, identity: str, home: Path | None = None) -> bool:
    """``exec --remote`` succeeded after ``ensure``: record the round trip at the
    installed revision of the current proof. True when a handoff was recorded."""
    home = home or _home()
    directory = readiness_dir(home, remote)
    ensured = _read(directory / "ensure.json")
    proof = _read(directory / f"{project_key(identity)}.json")
    if not ensured or ensured.get("project") != identity or not proof:
        return False
    revision = proof.get("installed_runtime_revision")
    if not isinstance(revision, str):
        return False
    _write_private(directory / "handoff.json", {
        "installed_runtime_revision": revision, "proven_at": int(time.time()),
        "project": identity})
    return True


def _home() -> Path:
    from sandbox.core._paths import _sandbox_base
    return _sandbox_base()
