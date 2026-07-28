from __future__ import annotations

import hashlib
import json
from typing import Callable

from sandbox.services.process import ProcessResult

from .adapters import ProviderSnapshot
from .models import (
    CleanupCandidate,
    CleanupItemOutcome,
    ResourceObservation,
    StorageTarget,
    utc_now,
)


_REMOTE_PROGRAM = r"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

REQUEST = json.loads(__REQUEST__)
HOME = Path(os.environ.get("SANDBOX_HOME") or (Path.home() / "sandbox")).resolve()
RUNTIME = HOME / "runtime"
DEPLOY = HOME / "deploy-src"
DEADLINE = time.monotonic() + float(REQUEST.get("budget_seconds", 15))

def rid(kind, locator):
    return kind + "-" + hashlib.sha256(locator.encode()).hexdigest()[:20]

def run(argv, timeout):
    remaining = max(min(float(timeout), DEADLINE - time.monotonic()), 0.01)
    try:
        result = subprocess.run(
            argv, text=True, capture_output=True, timeout=remaining, check=False,
        )
        return result.returncode, result.stdout[:4000000], result.stderr[:4096]
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError:
        return 127, "", "unavailable"

def size(path, thorough):
    if not thorough:
        return "not_measured", None, None
    code, out, _err = run(["du", "-sk", str(path)], 8)
    if code == 124:
        return "timed_out", None, "measurement timed out"
    if code:
        return "unavailable", None, "measurement unavailable"
    try:
        return "measured", int(out.split()[0]) * 1024, None
    except (ValueError, IndexError):
        return "unavailable", None, "measurement unavailable"

def age(path):
    try:
        return max(int(time.time() - path.stat().st_mtime), 0)
    except OSError:
        return None

def owner(labels):
    if not isinstance(labels, dict):
        return None
    project = labels.get("com.docker.compose.project")
    working = str(labels.get("com.docker.compose.project.working_dir") or "")
    if isinstance(project, str) and project and (
        project.startswith("sandbox-")
        or "/sandbox/" in working
        or working.endswith("/sandbox")
    ):
        return project
    return None

def docker_inventory():
    outcomes = []
    inventory = {"containers": [], "volumes": [], "networks": []}
    commands = (
        ("containers", ["docker", "ps", "-aq"], ["docker", "inspect", "--size"]),
        ("volumes", ["docker", "volume", "ls", "-q"], ["docker", "volume", "inspect"]),
        ("networks", ["docker", "network", "ls", "-q"], ["docker", "network", "inspect"]),
    )
    for key, list_argv, inspect_argv in commands:
        code, out, _err = run(list_argv, 3)
        category = "docker_" + key
        if code:
            outcomes.append({"category": category, "status": "timed_out" if code == 124 else "unavailable"})
            continue
        identifiers = out.split()
        if not identifiers:
            outcomes.append({"category": category, "status": "complete"})
            continue
        code, out, _err = run(inspect_argv + identifiers, 5)
        if code:
            outcomes.append({"category": category, "status": "timed_out" if code == 124 else "unavailable"})
            continue
        try:
            inventory[key] = json.loads(out or "[]")
            outcomes.append({"category": category, "status": "complete"})
        except json.JSONDecodeError:
            outcomes.append({"category": category, "status": "unavailable"})
    return inventory, outcomes

def observation(kind, locator, display, owner_kind, owner_id, classification,
                size_state, size_bytes, reclaimable, references=(), evidence=(), errors=()):
    return {
        "resource_id": rid(kind, locator),
        "kind": kind,
        "locator": locator,
        "display_name": display,
        "owner": {"kind": owner_kind, "id": owner_id},
        "classification": classification,
        "size_state": size_state,
        "size_bytes": size_bytes,
        "reclaimable_bytes": reclaimable,
        "age_seconds": None,
        "references": list(references),
        "evidence": list(evidence),
        "errors": list(errors),
    }

def scan():
    thorough = bool(REQUEST.get("thorough"))
    usage = shutil.disk_usage("/")
    identity_source = platform.node() + ":" + str(os.stat("/").st_dev) + ":" + str(HOME)
    identity = hashlib.sha256(identity_source.encode()).hexdigest()[:24]
    capacity = {
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "available_bytes": int(usage.free),
        "reserved_bytes": max(int(usage.total) - int(usage.used) - int(usage.free), 0),
    }
    inventory, outcomes = docker_inventory()
    resources = []
    active_volumes = set()
    active_sources = set()
    for container in inventory["containers"]:
        running = bool((container.get("State") or {}).get("Running"))
        for mount in container.get("Mounts") or ():
            if running and mount.get("Type") == "volume" and mount.get("Name"):
                active_volumes.add(mount["Name"])
            if running and mount.get("Type") == "bind" and mount.get("Source"):
                active_sources.add(str(mount["Source"]))
        labels = (container.get("Config") or {}).get("Labels") or {}
        project = owner(labels)
        if not project:
            continue
        oneoff = str(labels.get("com.docker.compose.oneoff") or "").lower() == "true"
        locator = str(container.get("Id") or container.get("Name") or "")
        raw_size = container.get("SizeRw")
        measured = isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size >= 0
        resources.append(observation(
            "container", locator, str(container.get("Name") or locator).lstrip("/"),
            "project", project,
            "active" if running else (
                "disposable_cache" if oneoff else "unverified"
            ),
            "measured" if measured else "unavailable", raw_size if measured else None,
            raw_size if measured and not running and oneoff else 0,
            ("running_container",) if running else (),
            (
                "compose_project_label",
                "running" if running else (
                    "compose_oneoff" if oneoff else "registry_evidence_unavailable"
                ),
            ),
        ))
    for volume in inventory["volumes"]:
        name = volume.get("Name")
        if not isinstance(name, str) or not name:
            continue
        project = owner(volume.get("Labels"))
        active = name in active_volumes
        state, measured_size, error = "not_measured", None, None
        classification = "unmanaged" if not project else ("active" if active else "unverified")
        if thorough and project and not active:
            mountpoint = str(volume.get("Mountpoint") or "")
            code, out, _err = run([
                "sudo", "-n", "du", "-sk", mountpoint,
            ], 12)
            if code == 0:
                try:
                    measured_size = int(out.split()[0]) * 1024
                    state = "measured"
                except (ValueError, IndexError):
                    state, error = "unavailable", "private volume measurement unavailable"
            elif code == 124:
                state, error = "timed_out", "volume measurement timed out"
            else:
                state, error = "unavailable", "private volume measurement unavailable"
        resources.append(observation(
            "volume", name, name, "project" if project else "unmanaged", project,
            classification, state, measured_size,
            0,
            ("live_container_mount",) if active else (),
            (
                ("compose_project_label",)
                if active else
                ("compose_project_label", "registry_evidence_unavailable")
            ) if project else ("ownership_unverified",),
            (error,) if error else (),
        ))
    for network in inventory["networks"]:
        network_id = network.get("Id")
        project = owner(network.get("Labels"))
        if not isinstance(network_id, str) or not network_id or not project:
            continue
        active = bool(network.get("Containers"))
        resources.append(observation(
            "network", network_id, str(network.get("Name") or network_id),
            "project", project, "active" if active else "unverified",
            "measured", 0, 0, ("connected_container",) if active else (),
            (
                ("compose_project_label",)
                if active else
                ("compose_project_label", "registry_evidence_unavailable")
            ),
        ))
    for root, category in ((DEPLOY, "deploy_worktrees"), (RUNTIME, "sandbox_runtime")):
        if not root.is_dir():
            outcomes.append({"category": category, "status": "complete"})
            continue
        category_status = "complete"
        try:
            entries = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError:
            outcomes.append({"category": category, "status": "unavailable"})
            continue
        for path in entries:
            if time.monotonic() >= DEADLINE:
                category_status = "timed_out"
                break
            if root == DEPLOY:
                is_workspace = "-workspace-" in path.name or ".workspace-" in path.name
                active = any(source == str(path) or source.startswith(str(path) + os.sep) for source in active_sources)
                classification = "active" if active else (
                    "unverified" if is_workspace else "retained"
                )
                state, measured_size, error = size(path, thorough)
                item = observation(
                    "worktree", str(path), path.name,
                    "workspace" if is_workspace else "project", path.name,
                    classification, state, measured_size,
                    0,
                    ("live_container_mount",) if active else (),
                    (
                        "sandbox_deploy_root",
                        "registry_evidence_unavailable",
                    ) if is_workspace and not active else ("sandbox_deploy_root",),
                    (error,) if error else (),
                )
            else:
                is_cache = path.name == "dl-cache"
                state, measured_size, error = size(path, thorough or is_cache)
                classification = "disposable_cache" if is_cache else "retained"
                if is_cache and state != "measured":
                    classification = "unverified"
                item = observation(
                    "download_cache" if is_cache else "runtime", str(path), path.name,
                    "sandbox", "sandbox", classification, state, measured_size,
                    measured_size if classification == "disposable_cache" and measured_size is not None else 0,
                    ("retention_policy",) if not is_cache else (),
                    ("sandbox_runtime_root", "download_cache" if is_cache else "retention_unknown"),
                    (error,) if error else (),
                )
            item["age_seconds"] = age(path)
            resources.append(item)
        outcomes.append({"category": category, "status": category_status})
    return {
        "identity": identity,
        "capacity": capacity,
        "resources": resources,
        "category_outcomes": outcomes,
        "drift": None,
    }

def inside(path, root):
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False

def remove():
    kind = REQUEST.get("kind")
    locator = str(REQUEST.get("locator") or "")
    if kind in {"download_cache", "job_artifact", "worktree", "runtime"}:
        path = Path(locator)
        allowed = inside(path, RUNTIME) or inside(path, DEPLOY)
        if not allowed or path in {RUNTIME, DEPLOY, HOME}:
            return {"status": "failed", "reason": "path_outside_managed_roots"}
        if not path.exists() and not path.is_symlink():
            return {"status": "already_absent", "reason": "already_absent"}
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        if kind == "download_cache":
            path.mkdir(parents=True, exist_ok=True)
        return {"status": "removed", "reason": "removed"}
    commands = {
        "volume": ["docker", "volume", "rm", locator],
        "container": ["docker", "container", "rm", locator],
        "network": ["docker", "network", "rm", locator],
    }
    argv = commands.get(kind)
    if argv is None:
        return {"status": "failed", "reason": "unsupported_resource_kind"}
    code, _out, _err = run(argv, 60)
    return {
        "status": "removed" if code == 0 else ("timed_out" if code == 124 else "failed"),
        "reason": "removed" if code == 0 else ("cleanup_timed_out" if code == 124 else "cleanup_failed"),
    }

try:
    output = remove() if REQUEST.get("action") == "remove" else scan()
    print(json.dumps(output, separators=(",", ":")))
except Exception:
    print(json.dumps({"error": "resource probe failed"}, separators=(",", ":")))
    sys.exit(1)
"""


def _program(request: dict) -> str:
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
    return _REMOTE_PROGRAM.replace("__REQUEST__", repr(encoded))


def _observation(value: dict) -> ResourceObservation:
    owner = value.get("owner") or {}
    return ResourceObservation(
        resource_id=value.get("resource_id"),
        kind=value.get("kind"),
        locator=value.get("locator"),
        display_name=value.get("display_name"),
        owner_kind=owner.get("kind"),
        owner_id=owner.get("id"),
        classification=value.get("classification"),
        size_state=value.get("size_state"),
        size_bytes=value.get("size_bytes"),
        reclaimable_bytes=value.get("reclaimable_bytes"),
        age_seconds=value.get("age_seconds"),
        references=tuple(value.get("references") or ()),
        evidence=tuple(value.get("evidence") or ()),
        errors=tuple(value.get("errors") or ()),
    )


class RemoteResourceAdapter:
    """Named-remote provider using one bounded SSH session per operation."""

    def __init__(
        self,
        remote_name: str,
        *,
        remote_lookup: Callable | None = None,
        ssh_process: Callable | None = None,
        clock=utc_now,
    ) -> None:
        self.remote_name = remote_name
        self._remote_lookup = remote_lookup
        self._ssh_process = ssh_process
        self.clock = clock
        self._target: StorageTarget | None = None

    def _entry(self) -> dict:
        from .service import ResourceError

        if self._remote_lookup is None:
            from sandbox.core._remote import get_remote
            lookup = get_remote
        else:
            lookup = self._remote_lookup
        entry = lookup(self.remote_name)
        if not entry:
            raise ResourceError(
                f"unknown remote {self.remote_name!r}",
                "unknown_remote",
            )
        if not entry.get("provisioned"):
            raise ResourceError(
                f"remote {self.remote_name!r} is not provisioned",
                "remote_unreachable",
                retryable=True,
            )
        return entry

    def target(self) -> StorageTarget:
        entry = self._entry()
        if self._target is not None:
            return self._target
        seed = f"{self.remote_name}:{entry.get('ssh', '')}"
        return StorageTarget(
            "remote", self.remote_name,
            hashlib.sha256(seed.encode()).hexdigest()[:24],
        )

    def _ssh(self, entry: dict, request: dict, timeout: float) -> ProcessResult:
        if self._ssh_process is None:
            from sandbox.core._remote import ssh_process
            execute = ssh_process
        else:
            execute = self._ssh_process
        result = execute(
            entry, "python3 -", input_data=_program(request),
            timeout=max(int(timeout), 1),
        )
        return ProcessResult(
            tuple(getattr(result, "args", getattr(result, "argv", ("ssh",)))),
            int(result.returncode),
            str(result.stdout or ""),
            str(result.stderr or ""),
        )

    def observe(
        self, *, thorough: bool, budget_seconds: float,
        progress=None,
    ) -> ProviderSnapshot:
        entry = self._entry()
        if progress:
            progress("remote_probe")
        response = self._ssh(entry, {
            "action": "observe",
            "thorough": bool(thorough),
            "budget_seconds": float(budget_seconds),
        }, budget_seconds + 2)
        if response.returncode == 124:
            return ProviderSnapshot(
                self.target(), None, (),
                ({"category": "remote_probe", "status": "timed_out"},),
            )
        if response.returncode != 0:
            return ProviderSnapshot(
                self.target(), None, (),
                ({"category": "remote_probe", "status": "unavailable"},),
            )
        try:
            payload = json.loads(response.stdout)
            identity = payload["identity"]
            resources = tuple(
                _observation(item) for item in payload.get("resources") or ()
            )
            target = StorageTarget("remote", self.remote_name, identity)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ProviderSnapshot(
                self.target(), None, (),
                ({"category": "remote_probe", "status": "unavailable"},),
            )
        self._target = target
        return ProviderSnapshot(
            target,
            payload.get("capacity"),
            resources,
            tuple(payload.get("category_outcomes") or ()),
            payload.get("drift"),
        )

    def revalidate(self, candidate: CleanupCandidate) -> ResourceObservation | None:
        snapshot = self.observe(thorough=True, budget_seconds=30)
        return next((
            item for item in snapshot.resources
            if item.resource_id == candidate.resource_id
        ), None)

    def remove(self, candidate: CleanupCandidate) -> CleanupItemOutcome:
        response = self._ssh(self._entry(), {
            "action": "remove",
            "kind": candidate.kind,
            "locator": candidate.locator,
            "budget_seconds": 60,
        }, 62)
        if response.returncode == 124:
            status, reason = "timed_out", "cleanup_timed_out"
        elif response.returncode != 0:
            status, reason = "failed", "cleanup_failed"
        else:
            try:
                payload = json.loads(response.stdout)
                status = payload.get("status")
                reason = payload.get("reason")
                if status not in {
                    "removed", "already_absent", "failed", "timed_out",
                }:
                    raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError):
                status, reason = "failed", "cleanup_failed"
        return CleanupItemOutcome(
            candidate.resource_id,
            status,
            reason,
            candidate.expected_size_bytes,
            False,
            self.clock(),
        )
