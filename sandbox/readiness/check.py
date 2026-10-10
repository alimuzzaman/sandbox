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
    # (registration, target) -> whether the project's instance is registered
    # on the remote; None skips the no-instance check.
    instance_present: Callable | None = None


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
        instance_present=lambda remote, target: project_instance_present(
            remote, target, ssh_run=_remote.ssh_run,
            workspace_path=_remote.remote_workspace_path,
            list_instances=_remote.list_remote_instances),
    )


def project_instance_present(remote: dict, target, *, ssh_run: Callable,
                             workspace_path: Callable, list_instances: Callable) -> bool | None:
    """Whether the remote registry holds an instance for this project's
    workspace. Instance names are derived (and truncated) on the remote, so
    the lookup is by project, never by a predicted name. False when the
    workspace does not exist; None when that cannot be observed; raises when
    the registry cannot be read."""
    import shlex
    path = workspace_path(remote, target.project_root, target.workspace_label)
    probe = ssh_run(remote, f"test -d {shlex.quote(path)}", timeout=rows.REACHABILITY_TIMEOUT)
    code = getattr(probe, "returncode", None)
    if code == 1:
        return False
    if code != 0:
        return None
    return bool(list_instances(remote, target_path=path))


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


def run(project_dir: str, remote: str | None, *, probes: Probes | None = None,
        target=None, full: bool = False) -> dict:
    """One readiness result (the contract's ``data``); stores a proof when a
    remote was selected and registered. ``target`` is the caller's already
    resolved target, so its explicit configuration selection is kept; ``full``
    returns the stored proof, generation included."""
    probes = probes or default_probes()
    taken_at = int(probes.clock())
    result = {"remote": remote, "remote_selection": "explicit" if remote else None,
              "rows": [], "installed_runtime_revision": None,
              "taken_at": taken_at, "reusable_until": taken_at + REUSE_SECONDS}
    error_code = selection = None
    if target is None:
        try:
            target = probes.resolve(project_dir, remote)
        except Exception as exc:  # TargetResolutionError and config failures
            target, error_code = None, getattr(exc, "code", None) or "invalid_project"
            selection = getattr(exc, "data", None)
            selection = selection if isinstance(selection, dict) else None
            refused = getattr(exc, "remote_name", None)
            if remote is None and isinstance(refused, str):
                # The declared remote that failed registration (FR-020).
                remote = result["remote"] = refused
            if remote is not None and selection is not None:
                result["remote_selection"] = rows.SELECTION_SOURCES.get(
                    selection.get("name_source"), result["remote_selection"])
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
        result["rows"] = [rows.registration(error_code, remote, selection=selection)] + [
            rows.not_applicable(aspect, "no_registration") for aspect in rows.ASPECTS[1:]]
        return result

    registration = target.remote or probes.remote_lookup(remote) or {}
    identity = (getattr(target, "sources", None) or {}).get("identity") or target.project_root
    # Read before probing: an invalidation during the probes rotates it.
    token = generation(probes.home, remote)
    evaluated = _evaluate(registration, remote, probes, _instance_check(registration, target, probes))
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
    proof = {**result, "project": identity, "generation": token}
    _write_private(readiness_dir(probes.home, remote) / f"{project_key(identity)}.json", proof)
    return proof if full else result


def _instance_check(registration: dict, target, probes: Probes) -> Callable | None:
    """A deferred instance lookup; it runs inside the ownership probe, under
    the deadline."""
    if probes.instance_present is None or not getattr(target, "workspace_label", None):
        return None
    return lambda: probes.instance_present(registration, target)


def _evaluate(registration: dict, name: str, probes: Probes,
              instance: Callable | None = None) -> dict:
    """Reachability, compatibility, capacity and repair rows under the deadline."""
    found: dict = {}
    lock = threading.Lock()

    def publish(value: dict) -> None:
        with lock:
            found.update(value)

    def compatibility():
        value, revision = rows.runtime_compatibility(probes.service_status, registration, name)
        return {"runtime_compatibility": value, "_revision": revision}

    def capacity():
        value = rows.capacity(probes.capacity_decision, registration, name)
        # The verdict stands on its own; a slow proposal never hides it.
        publish({"capacity": value})
        if value.get("reason") == "missing_pool_evidence":
            return {"_proposed": _proposal(registration, probes)}
        return {}

    tasks = {
        "reachability": lambda: {"reachability": rows.reachability(
            probes.ssh_run, registration, name)},
        "runtime_compatibility": compatibility,
        "capacity": capacity,
        "ownership_repair": lambda: {"ownership_repair": rows.ownership_repair(
            probes.ssh_run, registration, name, probes.sb_path(registration),
            instance=instance)},
    }

    def worker(task):
        try:
            value = task()
        except Exception:
            return
        publish(value)

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


def _bound(proof) -> tuple[str, str | None] | None:
    """The revision and generation an operation's own gate proved, or None.
    A ``None`` generation means the remote was never invalidated."""
    if not isinstance(proof, dict):
        return None
    revision, token = proof.get("installed_runtime_revision"), proof.get("generation")
    if not isinstance(revision, str) or not (token is None or isinstance(token, str)):
        return None
    return revision, token


def record_ensure(remote: str, identity: str, home: Path | None = None, *,
                  proof: dict | None = None) -> None:
    """``ensure --remote`` succeeded; ``exec --remote`` completes the handoff.
    ``proof`` is the one this ensure's gate passed; it binds the record to a
    revision and generation."""
    home = home or _home()
    bound = _bound(proof)
    _write_private(readiness_dir(home, remote) / "ensure.json", {
        "project": identity, "recorded_at": int(time.time()),
        "installed_runtime_revision": bound[0] if bound else None,
        "generation": bound[1] if bound else None})


def record_exec(remote: str, identity: str, home: Path | None = None, *,
                proof: dict | None = None) -> bool:
    """``exec --remote`` succeeded after ``ensure``: record the round trip.
    Both operations' gates must have proved the same revision and generation,
    and no invalidation may have happened since. True when recorded."""
    home = home or _home()
    directory = readiness_dir(home, remote)
    ensured = _read(directory / "ensure.json")
    bound = _bound(proof)
    if not ensured or ensured.get("project") != identity or bound is None:
        return False
    if (ensured.get("installed_runtime_revision"), ensured.get("generation")) != bound \
            or generation(home, remote) != bound[1]:
        return False
    _write_private(directory / "handoff.json", {
        "installed_runtime_revision": bound[0], "proven_at": int(time.time()),
        "project": identity})
    return True


def _home() -> Path:
    from sandbox.core._paths import _sandbox_base
    return _sandbox_base()
