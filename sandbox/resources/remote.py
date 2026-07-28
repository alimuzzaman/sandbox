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
from datetime import datetime, timezone
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

REQUEST = json.loads(__REQUEST__)
HOME = Path(os.environ.get("SANDBOX_HOME") or (Path.home() / "sandbox")).resolve()
RUNTIME = HOME / "runtime"
DEPLOY = HOME / "deploy-src"
SB = HOME / "sb-src" / "sb"
BUDGET_SECONDS = max(float(REQUEST.get("budget_seconds", 15)), 0.5)
DEADLINE = time.monotonic() + max(BUDGET_SECONDS - 2, 0.25)
PHASE = "startup"

BYTE_SIZE = re.compile(
    r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?i?b)?$", re.I,
)

def byte_count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    match = BYTE_SIZE.fullmatch(value.strip())
    if not match:
        return None
    unit = (match.group(2) or "b").lower()
    powers = {
        "b": 0,
        "kb": 1, "kib": 1,
        "mb": 2, "mib": 2,
        "gb": 3, "gib": 3,
        "tb": 4, "tib": 4,
        "pb": 5, "pib": 5,
        "eb": 6, "eib": 6,
    }
    power = powers.get(unit)
    if power is None:
        return None
    return int(float(match.group(1)) * (1024 ** power))

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
    inventory = {
        "containers": [], "volumes": [], "networks": [], "images": [],
        "build_cache": [],
    }
    commands = (
        ("containers", ["docker", "ps", "-aq"], ["docker", "inspect", "--size"]),
        ("volumes", ["docker", "volume", "ls", "-q"], ["docker", "volume", "inspect"]),
        ("networks", ["docker", "network", "ls", "-q"], ["docker", "network", "inspect"]),
        ("images", ["docker", "image", "ls", "-q"], ["docker", "image", "inspect"]),
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
    code, out, _err = run(
        ["docker", "buildx", "du", "--format=json"], 20,
    )
    if code == 0:
        try:
            inventory["build_cache"] = [
                json.loads(line) for line in out.splitlines() if line.strip()
            ]
            status = "complete"
        except json.JSONDecodeError:
            status = "unavailable"
    else:
        status = "timed_out" if code == 124 else "unavailable"
    outcomes.append({"category": "docker_build_cache", "status": status})
    return inventory, outcomes

def observation(kind, locator, display, owner_kind, owner_id, classification,
                size_state, size_bytes, reclaimable, references=(), evidence=(),
                errors=(), capacity_accounted=False):
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
        "capacity_accounted": bool(capacity_accounted),
        "age_seconds": None,
        "references": list(references),
        "evidence": list(evidence),
        "errors": list(errors),
    }

def lifecycle_evidence():
    protected_paths = {}
    protected_projects = set()
    outcomes = []
    registry_ok = True
    registry_records = None
    try:
        from sandbox.project_registry import JsonRegistryRepository

        registry_records = JsonRegistryRepository(
            RUNTIME / "registry.json"
        ).read_only_all()
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError):
        code, out, _err = run([str(SB), "instances", "--json"], 20)
        if code == 0:
            try:
                payload = json.loads(out)
                registry_records = payload.get("instances")
                if not isinstance(registry_records, list):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                registry_records = None
    if registry_records is not None:
        records = (
            registry_records.values()
            if isinstance(registry_records, dict)
            else registry_records
        )
        for record in records:
            if not isinstance(record, dict):
                continue
            root = record.get("root")
            if isinstance(root, str) and root:
                canonical = str(Path(root).resolve(strict=False))
                protected_paths.setdefault(canonical, []).append(
                    "instance_registry"
                )
            instance = record.get("instance")
            if not isinstance(instance, str) or not instance:
                instance = record.get("name")
            if isinstance(instance, str) and instance:
                protected_projects.update((instance, "sandbox-" + instance))
            project = record.get("project")
            if (
                isinstance(project, str)
                and project
                and project != "—"
            ):
                protected_projects.update((project, "sandbox-" + project))
                protected_paths.setdefault(
                    str((DEPLOY / project).resolve(strict=False)), []
                ).append("instance_registry")
        outcomes.append({"category": "instance_registry", "status": "complete"})
    else:
        registry_ok = False
        outcomes.append({"category": "instance_registry", "status": "unavailable"})

    jobs_ok = True
    artifacts_complete = True
    job_index = None
    try:
        from sandbox.jobs.registry import read_resource_index

        job_index = read_resource_index(
            RUNTIME / "jobs" / "registry.sqlite3"
        )
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError):
        code, out, _err = run(
            [str(SB), "job-list", "--limit", "200", "--json"], 20,
        )
        if code == 0:
            try:
                payload = json.loads(out)
                rows = payload.get("jobs")
                if not isinstance(rows, list):
                    raise ValueError
                job_index = {"jobs": rows, "artifacts": []}
                artifacts_complete = False
                if len(rows) >= 200:
                    jobs_ok = False
            except (json.JSONDecodeError, ValueError):
                job_index = None
    if job_index is None:
        jobs = []
        artifacts = []
        jobs_ok = False
        outcomes.append({"category": "job_registry", "status": "unavailable"})
    else:
        jobs = list(job_index.get("jobs") or ())
        artifacts = list(job_index.get("artifacts") or ())
        outcomes.append({
            "category": "job_registry",
            "status": "complete" if jobs_ok else "partial",
        })

    terminal = {
        "succeeded", "failed", "timed_out", "cancelled", "interrupted",
    }
    for job in jobs:
        root = job.get("project_root")
        protect = (
            job.get("lifecycle") not in terminal
            or job.get("cleanup_policy") == "retain"
            or job.get("cleanup_state") == "retained"
        )
        if protect and isinstance(root, str) and root:
            canonical = str(Path(root).resolve(strict=False))
            protected_paths.setdefault(canonical, []).append("retained_job")
            workspace_project = Path(canonical).name
            if workspace_project:
                protected_projects.update((
                    workspace_project, "sandbox-" + workspace_project,
                ))
    return (
        {key: tuple(sorted(set(refs))) for key, refs in protected_paths.items()},
        protected_projects,
        jobs,
        artifacts,
        artifacts_complete,
        registry_ok and jobs_ok,
        outcomes,
    )

def host_capacity_resources(thorough):
    if not thorough:
        return [], [{"category": "host_filesystem", "status": "not_measured"}]
    resources = []
    status = "complete"
    roots = (
        (Path("/var"), "host /var"),
        (Path("/root"), "host /root"),
        (Path("/usr"), "host /usr"),
        (Path("/home"), "host /home"),
        (Path("/opt"), "host /opt"),
        (Path("/srv"), "host /srv"),
        (Path("/tmp"), "host /tmp"),
        (Path("/boot"), "host /boot"),
        (Path("/etc"), "host /etc"),
    )
    for path, display in roots:
        if time.monotonic() >= DEADLINE:
            status = "timed_out"
            break
        if not path.exists():
            continue
        code, out, _err = run(
            ["sudo", "-n", "du", "-x", "-sk", str(path)], 45,
        )
        if code == 0:
            try:
                measured = int(out.split()[0]) * 1024
                state, error = "measured", None
            except (ValueError, IndexError):
                measured, state, error = None, "unavailable", "measurement unavailable"
        elif code == 124:
            measured, state, error = None, "timed_out", "measurement timed out"
            status = "timed_out"
        else:
            measured, state, error = None, "unavailable", "measurement unavailable"
            if status == "complete":
                status = "partial"
        resources.append(observation(
            "host_root", str(path), display, "host", REQUEST.get("remote_name"),
            "retained", state, measured, 0, ("monitoring_only",),
            ("filesystem_capacity_root",), (error,) if error else (),
            capacity_accounted=True,
        ))
    return resources, [{"category": "host_filesystem", "status": status}]

def docker_storage_resources(thorough):
    if not thorough:
        return [], [{"category": "docker_storage", "status": "not_measured"}]
    resources = []
    status = "complete"
    for path, display in (
        (Path("/var/lib/docker/overlay2"), "Docker overlay layers"),
        (Path("/var/lib/docker/volumes"), "Docker volume storage"),
        (Path("/var/lib/docker/buildkit"), "Docker BuildKit storage"),
        (Path("/var/lib/docker/containers"), "Docker container logs"),
        (Path("/var/lib/docker/image"), "Docker image metadata"),
    ):
        if time.monotonic() >= DEADLINE:
            status = "timed_out"
            break
        code, out, _err = run(["sudo", "-n", "du", "-x", "-sk", str(path)], 25)
        if code == 0:
            try:
                measured = int(out.split()[0]) * 1024
                state, error = "measured", None
            except (ValueError, IndexError):
                measured, state, error = None, "unavailable", "measurement unavailable"
        elif code == 124:
            measured, state, error = None, "timed_out", "measurement timed out"
            status = "timed_out"
        else:
            measured, state, error = None, "unavailable", "measurement unavailable"
            if status == "complete":
                status = "partial"
        resources.append(observation(
            "engine_storage", str(path), display, "host",
            REQUEST.get("remote_name"), "retained", state, measured, 0,
            ("monitoring_only",), ("docker_storage_root",),
            (error,) if error else (),
        ))
    return resources, [{"category": "docker_storage", "status": status}]

def scan():
    global PHASE
    thorough = bool(REQUEST.get("thorough"))
    focus = REQUEST.get("focus")
    target_kind = REQUEST.get("target_kind")
    target_locator = str(REQUEST.get("target_locator") or "")
    targeted = bool(target_kind and target_locator)
    usage = shutil.disk_usage("/")
    identity_source = platform.node() + ":" + str(os.stat("/").st_dev) + ":" + str(HOME)
    identity = hashlib.sha256(identity_source.encode()).hexdigest()[:24]
    capacity = {
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "available_bytes": int(usage.free),
        "reserved_bytes": max(int(usage.total) - int(usage.used) - int(usage.free), 0),
    }
    PHASE = "lifecycle_evidence"
    (
        protected_paths,
        protected_projects,
        _jobs,
        artifacts,
        artifacts_complete,
        lifecycle_complete,
        lifecycle_outcomes,
    ) = lifecycle_evidence()
    PHASE = "docker_inventory"
    inventory, outcomes = docker_inventory()
    outcomes.extend(lifecycle_outcomes)
    resources = []
    if not targeted and focus is None:
        PHASE = "docker_storage"
        storage_resources, storage_outcomes = docker_storage_resources(thorough)
        resources.extend(storage_resources)
        outcomes.extend(storage_outcomes)
        PHASE = "host_filesystem"
        host_resources, host_outcomes = host_capacity_resources(thorough)
        resources.extend(host_resources)
        outcomes.extend(host_outcomes)
    active_volumes = set()
    active_sources = set()
    active_projects = set()
    PHASE = "docker_resource_classification"
    for container in inventory["containers"]:
        running = bool((container.get("State") or {}).get("Running"))
        labels = (container.get("Config") or {}).get("Labels") or {}
        project = owner(labels)
        if running and project:
            active_projects.add(project)
        for mount in container.get("Mounts") or ():
            if running and mount.get("Type") == "volume" and mount.get("Name"):
                active_volumes.add(mount["Name"])
            if running and mount.get("Type") == "bind" and mount.get("Source"):
                active_sources.add(str(mount["Source"]))
    for container in inventory["containers"]:
        running = bool((container.get("State") or {}).get("Running"))
        labels = (container.get("Config") or {}).get("Labels") or {}
        project = owner(labels)
        if not project:
            continue
        oneoff = str(labels.get("com.docker.compose.oneoff") or "").lower() == "true"
        locator = str(container.get("Id") or container.get("Name") or "")
        if targeted and (target_kind != "container" or target_locator != locator):
            continue
        raw_size = container.get("SizeRw")
        measured = isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size >= 0
        if running:
            classification = "active"
            references = ("running_container",)
        elif project in active_projects:
            classification = "retained"
            references = ("live_compose_project",)
        elif project in protected_projects:
            classification = "retained"
            references = ("instance_or_job_registry",)
        elif oneoff and lifecycle_complete:
            classification = "disposable_cache"
            references = ()
        else:
            classification = "unverified"
            references = ()
        resources.append(observation(
            "container", locator, str(container.get("Name") or locator).lstrip("/"),
            "project", project, classification,
            "measured" if measured else "unavailable", raw_size if measured else None,
            raw_size if measured and classification == "disposable_cache" else 0,
            references,
            (
                "compose_project_label",
                "running" if running else (
                    "instance_or_job_registry" if project in protected_projects else
                    "compose_oneoff" if oneoff and lifecycle_complete else
                    "lifecycle_evidence_unavailable"
                ),
            ),
        ))
    for volume in inventory["volumes"]:
        name = volume.get("Name")
        if not isinstance(name, str) or not name:
            continue
        if targeted and (target_kind != "volume" or target_locator != name):
            continue
        project = owner(volume.get("Labels"))
        active = name in active_volumes
        state, measured_size, error = "not_measured", None, None
        if not project:
            classification = "unmanaged"
        elif active:
            classification = "active"
        elif project in active_projects:
            classification = "retained"
        elif project in protected_projects:
            classification = "retained"
        else:
            classification = "unverified"
        if thorough and focus != "cache" and project and not active:
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
        if (
            project
            and not active
            and project not in active_projects
            and project not in protected_projects
            and lifecycle_complete
            and state == "measured"
        ):
            classification = "stale_candidate"
        references = (
            ("live_container_mount",) if active else
            ("live_compose_project",)
            if project in active_projects else
            ("instance_or_job_registry",)
            if project in protected_projects else ()
        )
        resources.append(observation(
            "volume", name, name, "project" if project else "unmanaged", project,
            classification, state, measured_size,
            measured_size if classification == "stale_candidate" else 0,
            references,
            (
                ("compose_project_label",)
                if (
                    active or project in active_projects
                    or project in protected_projects
                ) else
                ("compose_project_label", "registry_and_job_absence")
                if lifecycle_complete else
                ("compose_project_label", "lifecycle_evidence_unavailable")
            ) if project else ("ownership_unverified",),
            (error,) if error else (),
        ))
    for network in inventory["networks"]:
        network_id = network.get("Id")
        project = owner(network.get("Labels"))
        if not isinstance(network_id, str) or not network_id or not project:
            continue
        if targeted and (
            target_kind != "network" or target_locator != network_id
        ):
            continue
        active = bool(network.get("Containers"))
        classification = (
            "active" if active else
            "retained" if project in active_projects else
            "retained" if project in protected_projects else
            "disposable_cache" if lifecycle_complete else
            "unverified"
        )
        resources.append(observation(
            "network", network_id, str(network.get("Name") or network_id),
            "project", project, classification,
            "measured", 0, 0, ("connected_container",) if active else (),
            (
                ("compose_project_label",)
                if active or project in protected_projects else
                ("compose_project_label", "registry_and_job_absence")
                if lifecycle_complete else
                ("compose_project_label", "lifecycle_evidence_unavailable")
            ),
        ))
    used_images = {
        str(container.get("Image"))
        for container in inventory["containers"]
        if container.get("Image")
    }
    for image in inventory["images"]:
        locator = image.get("Id")
        if not isinstance(locator, str) or not locator:
            continue
        if targeted and (target_kind != "image" or target_locator != locator):
            continue
        project = owner((image.get("Config") or {}).get("Labels"))
        if not project:
            continue
        used = locator in used_images
        raw_size = image.get("Size")
        measured = (
            isinstance(raw_size, int)
            and not isinstance(raw_size, bool)
            and raw_size >= 0
        )
        classification = (
            "active" if used else
            "retained" if project in protected_projects else
            "disposable_cache" if lifecycle_complete else
            "unverified"
        )
        display = next(iter(image.get("RepoTags") or ()), locator)
        resources.append(observation(
            "image", locator, str(display), "project", project,
            classification,
            "measured" if measured else "unavailable",
            raw_size if measured else None,
            raw_size if measured and classification == "disposable_cache" else 0,
            ("container_image",) if used else (
                ("instance_or_job_registry",)
                if project in protected_projects else ()
            ),
            ("compose_project_label",),
        ))
    for record in inventory["build_cache"]:
        locator = record.get("ID")
        if (
            not isinstance(locator, str)
            or not locator.isalnum()
            or not 12 <= len(locator) <= 128
        ):
            continue
        if targeted and (
            target_kind != "build_cache" or target_locator != locator
        ):
            continue
        measured_size = byte_count(record.get("Size"))
        reclaimable = record.get("Reclaimable") is True
        mutable = record.get("Mutable") is True
        shared = record.get("Shared") is True
        parents = record.get("Parents") or ()
        try:
            usage_count = int(record.get("UsageCount") or 0)
        except (TypeError, ValueError):
            usage_count = -1
        managed_host = REQUEST.get("managed_host") is True
        eligible = (
            managed_host
            and reclaimable
            and not mutable
            and not shared
            and not parents
            and usage_count == 0
            and measured_size is not None
            and measured_size >= 0
        )
        resources.append(observation(
            "build_cache", locator, "build cache " + locator[:12],
            "sandbox" if managed_host else "unmanaged",
            REQUEST.get("remote_name") if managed_host else None,
            "disposable_cache" if eligible else "unverified",
            "measured" if measured_size is not None and measured_size >= 0
            else "unavailable",
            measured_size if measured_size is not None and measured_size >= 0
            else None,
            measured_size if eligible else 0,
            ("mutable_build_cache",) if mutable else (),
            (
                "buildx_disk_usage",
                "provisioned_sandbox_remote" if managed_host else
                "ownership_unverified",
                "engine_reports_reclaimable" if reclaimable else
                "engine_retained",
                "immutable" if not mutable else "mutable",
                "shared" if shared else "private",
                "root_record" if not parents else "parented_record",
                "unused" if usage_count == 0 else "used",
            ),
        ))
    PHASE = "managed_path_classification"
    for root, category in ((DEPLOY, "deploy_worktrees"), (RUNTIME, "sandbox_runtime")):
        if targeted and target_kind not in {
            "worktree", "download_cache", "runtime", "job_artifact",
        }:
            continue
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
                if targeted and (
                    target_kind != "worktree" or target_locator != str(path)
                ):
                    continue
                is_workspace = "-workspace-" in path.name or ".workspace-" in path.name
                active = any(source == str(path) or source.startswith(str(path) + os.sep) for source in active_sources)
                protections = protected_paths.get(
                    str(path.resolve(strict=False)), (),
                )
                if active:
                    classification, references = "active", ("live_container_mount",)
                elif (
                    path.name in active_projects
                    or "sandbox-" + path.name in active_projects
                ):
                    classification, references = (
                        "retained", ("live_compose_project",),
                    )
                elif protections:
                    classification, references = "retained", protections
                elif path.name == "hosts" or not is_workspace:
                    classification, references = (
                        "retained", ("permanent_or_base_deployment",),
                    )
                elif lifecycle_complete:
                    classification, references = "stale_candidate", ()
                else:
                    classification, references = "unverified", ()
                state, measured_size, error = size(path, thorough)
                if (
                    classification == "stale_candidate"
                    and state != "measured"
                ):
                    classification = "unverified"
                item = observation(
                    "worktree", str(path), path.name,
                    "workspace" if is_workspace else "project", path.name,
                    classification, state, measured_size,
                    measured_size if classification == "stale_candidate" else 0,
                    references,
                    (
                        "sandbox_deploy_root",
                        "registry_and_job_absence",
                    ) if (
                        is_workspace and not active and not protections
                        and lifecycle_complete
                    ) else (
                        "sandbox_deploy_root",
                        "lifecycle_evidence_unavailable",
                    ) if (
                        is_workspace and not active and not protections
                    ) else ("sandbox_deploy_root",),
                    (error,) if error else (),
                )
            else:
                is_cache = path.name == "dl-cache"
                resource_kind = "download_cache" if is_cache else "runtime"
                if targeted and (
                    target_kind != resource_kind
                    or target_locator != str(path)
                ):
                    continue
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
    PHASE = "job_artifact_classification"
    terminal = {
        "succeeded", "failed", "timed_out", "cancelled", "interrupted",
    }
    artifact_status = "complete" if artifacts_complete else "unavailable"
    for artifact in artifacts:
        if time.monotonic() >= DEADLINE:
            artifact_status = "timed_out"
            break
        job_id = artifact.get("job_id")
        artifact_id = artifact.get("artifact_id")
        relative = artifact.get("stored_relative_path")
        if not all(
            isinstance(value, str) and value
            for value in (job_id, artifact_id, relative)
        ):
            continue
        path = (RUNTIME / "jobs" / job_id / relative).resolve(strict=False)
        try:
            path.relative_to((RUNTIME / "jobs").resolve(strict=False))
        except ValueError:
            continue
        if targeted and (
            target_kind != "job_artifact" or target_locator != str(path)
        ):
            continue
        if not path.exists() and not path.is_symlink():
            continue
        expired_by_time = False
        expires_at = artifact.get("expires_at")
        if isinstance(expires_at, str) and expires_at:
            try:
                expired_by_time = (
                    datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                    <= datetime.now(timezone.utc)
                )
            except (TypeError, ValueError):
                pass
        expired = (
            artifact.get("job_lifecycle") in terminal
            and (
                artifact.get("status") == "expired"
                or expired_by_time
            )
        )
        state, measured_size, error = size(path, thorough)
        classification = "disposable_cache" if expired else "retained"
        if classification == "disposable_cache" and state != "measured":
            classification = "unverified"
        resources.append(observation(
            "job_artifact", str(path),
            str(artifact.get("display_name") or artifact_id),
            "job", job_id, classification, state, measured_size,
            measured_size if classification == "disposable_cache" else 0,
            () if expired else ("job_retention",),
            ("job_registry", "terminal", "expired")
            if expired else ("job_registry", "retained"),
            (error,) if error else (),
        ))
    outcomes.append({"category": "job_artifacts", "status": artifact_status})
    PHASE = "serialize"
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
        "image": ["docker", "image", "rm", locator],
    }
    if kind == "build_cache":
        if (
            not locator.isalnum()
            or not 12 <= len(locator) <= 128
        ):
            return {"status": "failed", "reason": "invalid_build_cache_id"}
        commands["build_cache"] = [
            "docker", "buildx", "prune", "--force", "--all",
            "--filter", "id=" + locator,
        ]
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
except Exception as exc:
    print(json.dumps({
        "error": "resource probe failed",
        "error_phase": PHASE,
        "error_type": type(exc).__name__,
    }, separators=(",", ":")))
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
        capacity_accounted=value.get("capacity_accounted", False),
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
            entry,
            (
                'sandbox_runtime="${SANDBOX_HOME:-$HOME/sandbox}/sb-src"; '
                'PYTHONPATH="$sandbox_runtime" python3 -'
            ),
            input_data=_program(request),
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
        progress=None, focus: str | None = None,
    ) -> ProviderSnapshot:
        entry = self._entry()
        if progress:
            progress("remote_probe")
        response = self._ssh(entry, {
            "action": "observe",
            "thorough": bool(thorough),
            "budget_seconds": float(budget_seconds),
            "managed_host": bool(entry.get("provisioned")),
            "remote_name": self.remote_name,
            "focus": focus,
        }, budget_seconds + 15)
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
        entry = self._entry()
        response = self._ssh(entry, {
            "action": "observe",
            "thorough": True,
            "budget_seconds": 30,
            "managed_host": bool(entry.get("provisioned")),
            "remote_name": self.remote_name,
            "target_kind": candidate.kind,
            "target_locator": candidate.locator,
        }, 32)
        if response.returncode != 0:
            return None
        try:
            payload = json.loads(response.stdout)
            identity = payload["identity"]
            target = StorageTarget("remote", self.remote_name, identity)
            if self._target is not None and self._target != target:
                return None
            self._target = target
            resources = tuple(
                _observation(item) for item in payload.get("resources") or ()
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return next((
            item for item in resources
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
