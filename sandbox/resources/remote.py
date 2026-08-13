from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Callable

from sandbox.services.process import ProcessResult

from .adapters import ProviderSnapshot
from .attribution import DeepAttribution
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
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return 124, stdout[:4000000], (stderr + "\ntimed out")[:4096]
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

def load_workspace_projection():
    # Read ownership through the installed typed application boundary.
    try:
        from sandbox.application.context import workspace_ownership_projection
        return workspace_ownership_projection()
    except Exception:
        return None

def workspace_owner(projection, resource_type, resource_id):
    # Resolve one exact binding; never infer ownership from a label/path.
    if not isinstance(projection, dict):
        return "unknown", None, ("workspace_index_unavailable",), False
    records = projection.get("records", projection.get("workspaces"))
    if not isinstance(records, list):
        return "unknown", None, ("workspace_index_unavailable",), False
    counts = projection.get("counts") or {}
    projection_generation = projection.get(
        "index_generation", projection.get("generation"))
    valid_lifecycles = {
        "provisioning", "ready", "resetting", "destroying", "destroyed",
        "indeterminate",
    }
    incomplete = any(
        isinstance(counts.get(key), int) and counts.get(key) > 0
        for key in ("unresolved", "conflict", "incomplete")
    )
    if (isinstance(projection_generation, bool) or
            not isinstance(projection_generation, int)):
        incomplete = True
    if not records:
        incomplete = True
    matches = set()
    for record in records:
        if not isinstance(record, dict):
            incomplete = True
            continue
        if record.get("complete") is False:
            incomplete = True
            continue
        if (projection_generation is not None and
                record.get("index_generation") != projection_generation):
            incomplete = True
            continue
        workspace_id = record.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id:
            incomplete = True
            continue
        lifecycle = record.get("lifecycle")
        status = record.get("status")
        observed_at = record.get("observed_at")
        if (record.get("owner_kind") != "workspace" or
                not isinstance(lifecycle, str) or
                lifecycle.lower() not in valid_lifecycles or
                not isinstance(status, str) or
                not isinstance(observed_at, str) or not observed_at):
            incomplete = True
            continue
        if lifecycle.lower() in {
            "invalid", "incomplete", "unresolved", "conflict", "indeterminate",
            "destroyed", "tombstoned",
        } or status.lower() in {
            "invalid", "incomplete", "unresolved", "conflict", "indeterminate",
            "destroyed", "tombstoned",
        }:
            incomplete = True
            continue
        active_references = record.get("active_references")
        reference_active = isinstance(active_references, dict) and any(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            or value is True
            for value in active_references.values()
        )
        for binding in record.get("bindings") or ():
            if not isinstance(binding, dict):
                incomplete = True
                continue
            binding_type = binding.get("resource_type", binding.get("type"))
            binding_id = binding.get("resource_id", binding.get("id"))
            if binding_type != resource_type or binding_id != resource_id:
                continue
            binding_status = str(binding.get("status") or "owned").lower()
            if binding_status not in {"owned", "active", "retained", "ready"}:
                incomplete = True
                continue
            matches.add((workspace_id, binding_status, reference_active))
    if len(matches) == 1:
        workspace_id, _status, reference_active = next(iter(matches))
        evidence = ("workspace_binding", resource_type)
        if reference_active:
            evidence += ("workspace_active_reference",)
        return "workspace", workspace_id, evidence, True
    if len(matches) > 1:
        return "unknown", None, ("workspace_alias_collision", resource_type), False
    return "unknown", None, (
        "workspace_index_incomplete" if incomplete else "workspace_binding_missing",
    ), False

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

def deep_finding(kind, identity, display, observed_bytes, filesystem_id=None,
                 owner_kind=None, owner_id=None, capacity_accounted=False,
                 overlap="unknown", activity="unknown",
                 guidance="monitoring_only", evidence=(), limitations=(),
                 unique_bytes=None, shared_bytes=None,
                 potentially_reclaimable_bytes=None):
    return {
        "finding_id": rid(kind, identity),
        "kind": kind,
        "display_name": display,
        "filesystem_id": filesystem_id,
        "owner": {"kind": owner_kind, "id": owner_id},
        "observed_bytes": max(int(observed_bytes), 0),
        "capacity_accounted": bool(capacity_accounted),
        "overlap": overlap,
        "activity": activity,
        "guidance": guidance,
        "evidence": list(evidence),
        "limitations": list(limitations),
        "unique_bytes": unique_bytes,
        "shared_bytes": shared_bytes,
        "potentially_reclaimable_bytes": potentially_reclaimable_bytes,
    }

def mount_topology():
    # Return sanitized mount topology without publishing sources or options.
    rows = {}
    try:
        lines = Path("/proc/self/mountinfo").read_text(errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        parts = line.split()
        try:
            separator = parts.index("-")
            mount_id, parent_mount_id = parts[0], parts[1]
            device = parts[2]
            mount_point = (
                parts[4].replace("\\040", " ").replace("\\011", "\t")
                .replace("\\012", "\n").replace("\\134", "\\")
            )
            filesystem_type = parts[separator + 1]
            mount_options = frozenset(parts[5].split(","))
        except (IndexError, ValueError):
            continue
        rows[mount_point] = {
            "mount_id": mount_id,
            "parent_mount_id": parent_mount_id,
            "device": device,
            "filesystem_type": filesystem_type,
            "writable": "rw" in mount_options,
            "mount_flags": sorted(
                mount_options.intersection({
                    "ro", "rw", "nodev", "nosuid", "noexec", "relatime",
                })
            ),
        }
    return rows

def df_rows():
    topology = mount_topology()
    code, out, _err = run(["df", "-Pk"], 5)
    rows = []
    if code == 0:
        for index, line in enumerate(out.splitlines()):
            if index == 0 and "filesystem" in line.lower():
                continue
            parts = line.split(None, 5)
            if len(parts) != 6 or not parts[5].startswith("/"):
                continue
            try:
                total = int(parts[1]) * 1024
                used = int(parts[2]) * 1024
                available = int(parts[3]) * 1024
            except ValueError:
                continue
            topology_row = topology.get(parts[5], {})
            rows.append({
                "source": parts[0],
                "mount_point": parts[5],
                "mount_id": topology_row.get("mount_id"),
                "parent_mount_id": topology_row.get("parent_mount_id"),
                "device": topology_row.get("device"),
                "filesystem_type": topology_row.get(
                    "filesystem_type", "unknown",
                ),
                "writable": topology_row.get(
                    "writable", os.access(parts[5], os.W_OK),
                ),
                "mount_flags": topology_row.get("mount_flags", []),
                "total_bytes": max(total, 0),
                "used_bytes": max(min(used, total), 0),
                "available_bytes": max(available, 0),
            })
    rows.sort(key=lambda item: (
        len(item["mount_point"]), item["mount_point"],
    ))
    return rows, (
        "complete" if code == 0 and rows else
        "timed_out" if code == 124 else "unavailable"
    )

def mount_for(path, rows):
    target = str(Path(path).resolve(strict=False))
    matches = []
    for row in rows:
        mount = row["mount_point"]
        if target == mount or target.startswith(mount.rstrip("/") + os.sep):
            matches.append(mount)
    return max(matches, key=len) if matches else None

def filesystem_for_device(device, filesystems):
    if not device:
        return None
    def normalized(value):
        text = str(value).lower()
        if ":" in text:
            try:
                major, minor = text.split(":", 1)
                return str(int(major, 10)) + ":" + str(int(minor, 10))
            except ValueError:
                return text
        try:
            encoded = int(text, 0)
            # lsof reports Linux dev_t as hex; decode with Linux's stable
            # userspace major/minor layout instead of the controller OS APIs.
            major = (encoded >> 8) & 0xfff
            minor = (encoded & 0xff) | ((encoded >> 12) & 0xfff00)
            return str(major) + ":" + str(minor)
        except ValueError:
            return text
    expected = normalized(device)
    for item in sorted(
        filesystems, key=lambda value: bool(value.get("selected")), reverse=True,
    ):
        candidate = normalized(item.get("device") or "")
        if candidate == expected:
            return item["filesystem_id"]
    return None

def parse_ranked_sizes(
    output, filesystem_id, root, multiplier, safe_labels=None,
):
    safe_roots = {
        "/var": "host variable data",
        "/home": "user home data",
        "/root": "root user data",
        "/usr": "system software",
        "/opt": "optional software",
        "/srv": "service data",
        "/tmp": "temporary data",
        "/boot": "boot data",
        "/etc": "host configuration",
        "/var/lib": "host state data",
        "/var/log": "host logs",
        "/var/cache": "host package cache",
    }
    safe_roots.update(safe_labels or {})
    rows = []
    total = None
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            measured = int(parts[0]) * multiplier
        except ValueError:
            continue
        path = parts[1].strip()
        if path.rstrip("/") == str(root).rstrip("/"):
            total = measured
        else:
            rows.append((measured, path))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    findings = [
        deep_finding(
            "directory", filesystem_id + ":" + path,
            safe_roots.get(path, "entry " + str(index + 1)), measured,
            filesystem_id=filesystem_id,
            owner_kind="host",
            capacity_accounted=False,
            overlap="directory_root",
            guidance="monitoring_only",
            evidence=("allocated_blocks", "one_filesystem"),
        )
        for index, (measured, path) in enumerate(rows[:100])
    ]
    paths = {os.path.normpath(path) for _measured, path in rows}
    frontier_total = sum(
        measured for measured, path in rows
        if not any(
            parent != os.path.normpath(path)
            and os.path.normpath(path).startswith(parent.rstrip("/") + os.sep)
            for parent in paths
        )
    )
    return findings, total if total is not None else frontier_total

def deleted_open_findings(output, filesystems):
    process = {}
    current = None
    records = []

    def flush():
        nonlocal current
        if current:
            records.append((dict(process), current))
        current = None

    for line in output.splitlines():
        if not line:
            continue
        key, value = line[0], line[1:]
        if key == "p":
            flush()
            process = {"p": value}
        elif key == "c":
            process["c"] = value
        elif key == "f":
            flush()
            current = {"f": value}
        elif current is not None:
            current[key] = value
    flush()
    seen = set()
    grouped = {}
    for process, record in records:
        if record.get("t") not in {"REG", "VREG"}:
            continue
        device, inode = record.get("D"), record.get("i")
        filesystem_id = filesystem_for_device(device, filesystems)
        if filesystem_id is None:
            continue
        selected_filesystem = any(
            item["filesystem_id"] == filesystem_id and item.get("selected")
            for item in filesystems
        )
        pid = process.get("p") or "unknown"
        fd = record.get("f") or ""
        # Request/observe lsof access mode (record.get("a")) only as metadata;
        # allocation comes from stat blocks, never from the access flag.
        try:
            stat_result = os.stat("/proc/" + pid + "/fd/" + fd)
            measured = int(stat_result.st_blocks) * 512
            apparent_fallback = False
        except (AttributeError, OSError, TypeError, ValueError):
            # lsof's size is an apparent-size fallback, explicitly identified
            # below; it must never be treated as exact allocated blocks.
            try:
                measured = int(record.get("s", ""))
            except ValueError:
                continue
            apparent_fallback = True
        if measured <= 0:
            continue
        identity = (
            (device, inode) if device and inode else
            (
                process.get("p", ""), record.get("f", ""),
                record.get("n", ""), str(measured),
            )
        )
        if identity in seen:
            continue
        seen.add(identity)
        key = (filesystem_id, pid)
        previous = grouped.get(key, (0, False, selected_filesystem))
        grouped[key] = (
            previous[0] + measured, previous[1] or apparent_fallback,
            previous[2] and selected_filesystem,
        )
    findings = [
        deep_finding(
            "deleted_open", filesystem_id + ":" + pid,
            "process " + pid, measured,
            filesystem_id=filesystem_id,
            owner_kind="process", owner_id=pid,
            capacity_accounted=selected and not fallback, overlap="none",
            activity="active", guidance="manual",
            evidence=("zero_link_count", "regular_file", "allocated_blocks",
                      "device_filesystem_mapping"),
            limitations=(
                (("apparent_size_fallback",) if fallback else ())
                + (("unselected_filesystem",) if not selected else ())
            ),
        )
        for (filesystem_id, pid), (measured, fallback, selected) in sorted(
            grouped.items(), key=lambda item: (item[1][0], item[0]), reverse=True,
        )
    ]
    return findings, sum(
        item["observed_bytes"] for item in findings
        if item["capacity_accounted"]
    )

def docker_deep_findings(output):
    try:
        payload = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        return [], 0
    if isinstance(payload, list):
        merged = {}
        for section in payload:
            if isinstance(section, dict):
                merged.update(section)
        payload = merged
    if not isinstance(payload, dict):
        return [], 0
    findings = []

    def category_rows(name):
        value = payload.get(name)
        return value if isinstance(value, (list, tuple)) else ()

    def add(kind, identity, display, value, overlap, activity, evidence,
            limitations=("logical_engine_accounting",), unique=None,
            shared=None, reclaimable=None):
        measured = byte_count(value)
        if measured is None:
            return
        unique_value = byte_count(unique)
        shared_value = byte_count(shared)
        reclaimable_value = byte_count(reclaimable)
        findings.append(deep_finding(
            kind, identity, display[:120], measured,
            owner_kind="container_engine", owner_id=identity,
            capacity_accounted=False, overlap=overlap,
            activity=activity, guidance="monitoring_only",
            evidence=evidence,
            limitations=limitations,
            unique_bytes=(measured if unique_value is None else unique_value),
            shared_bytes=(0 if shared_value is None else shared_value),
            potentially_reclaimable_bytes=(
                0 if reclaimable_value is None else reclaimable_value
            ),
        ))

    for row in category_rows("Images"):
        if not isinstance(row, dict):
            continue
        identity = str(row.get("ID") or row.get("Repository") or "")
        if not identity:
            continue
        display = "image " + hashlib.sha256(identity.encode()).hexdigest()[:12]
        unique_value = row.get("UniqueSize")
        if unique_value is None:
            total_value = byte_count(row.get("Size"))
            shared_value = byte_count(row.get("SharedSize"))
            unique_value = (
                max(total_value - shared_value, 0)
                if total_value is not None and shared_value is not None
                else row.get("Size")
            )
        active = str(row.get("Containers") or "0") != "0"
        add(
            "container_image", identity, display,
            unique_value, "shared_layers", "active" if active else "inactive",
            (
                "docker_system_df", "unique_size", "shared_size_reported",
                "potentially_reclaimable" if row.get("Reclaimable") else
                "retained_by_engine",
            ), unique=unique_value, shared=row.get("SharedSize", 0),
            reclaimable=0 if active else unique_value,
        )
    for row in category_rows("Containers"):
        if not isinstance(row, dict):
            continue
        identity = str(row.get("ID") or row.get("Names") or "")
        if identity:
            active = str(row.get("State") or "").lower() == "running"
            add(
                "container", identity,
                "container " + hashlib.sha256(identity.encode()).hexdigest()[:12],
                row.get("Size"), "directory_root",
                "active" if active else "inactive",
                (
                    "docker_system_df", "writable_layer",
                    "potentially_reclaimable" if row.get("Reclaimable") else
                    "retained_by_engine",
                ), reclaimable=0 if active else row.get("Size"),
            )
    for row in category_rows("LocalVolumes"):
        if not isinstance(row, dict):
            continue
        identity = str(row.get("Name") or "")
        if identity:
            active = str(row.get("Links") or "0") != "0"
            add(
                "volume", identity,
                "volume " + hashlib.sha256(identity.encode()).hexdigest()[:12],
                row.get("Size"), "directory_root",
                "active" if active else "inactive",
                (
                    "docker_system_df", "volume_detail",
                    "potentially_reclaimable" if row.get("Reclaimable") else
                    "retained_by_engine",
                ), reclaimable=0 if active else row.get("Size"),
            )
    for row in category_rows("BuildCache"):
        if not isinstance(row, dict):
            continue
        identity = str(row.get("ID") or "")
        if identity:
            active = str(row.get("InUse") or "").lower() == "true"
            add(
                "build_cache", identity,
                "build cache " + identity[:12], row.get("Size"),
                "logical_cache",
                "active" if active else "inactive",
                (
                    "docker_system_df", "build_cache_detail",
                    "potentially_reclaimable" if row.get("Reclaimable") else
                    "retained_by_engine",
                ), reclaimable=0 if active else row.get("Size"),
            )
    findings.sort(key=lambda item: (
        item["observed_bytes"], item["kind"], item["finding_id"],
    ), reverse=True)
    return findings[:100], sum(
        item["observed_bytes"] for item in findings
    )

def deep_attribution(capacity, managed_roots=()):
    deep_started = time.monotonic()
    rows, mount_status = df_rows()
    if not rows:
        rows = [{
            "source": "target",
            "mount_point": "/",
            "total_bytes": capacity["total_bytes"],
            "used_bytes": capacity["used_bytes"],
            "available_bytes": capacity["available_bytes"],
            "filesystem_type": "unknown", "writable": True,
            "mount_id": None, "parent_mount_id": None, "device": None,
            "mount_flags": ["rw"],
        }]
    host_mount = mount_for(Path("/"), rows) or rows[0]["mount_point"]
    sandbox_mount = mount_for(HOME, rows)
    code, out, _err = run(
        ["docker", "info", "--format", "{{json .DockerRootDir}}"], 4,
    )
    docker_root = None
    if code == 0:
        try:
            docker_root = Path(json.loads(out.strip()))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    docker_mount = mount_for(docker_root, rows) if docker_root else None
    selected = {host_mount: "root"}
    if sandbox_mount and sandbox_mount not in selected:
        selected[sandbox_mount] = "sandbox_home"
    if docker_mount and docker_mount not in selected:
        selected[docker_mount] = "container_data"
    for managed_root in managed_roots:
        managed_mount = mount_for(managed_root, rows)
        if managed_mount and managed_mount not in selected:
            selected[managed_mount] = "managed_root"
    # A bind/alias mount can expose the same filesystem more than once. Select
    # one boundary per device so neither capacity nor allocation is doubled.
    selected_devices = set()
    for mount in list(selected):
        row = next((item for item in rows if item["mount_point"] == mount), {})
        device = row.get("device") or row.get("source") or mount
        if device in selected_devices:
            del selected[mount]
        else:
            selected_devices.add(device)
    elevated = run(["sudo", "-n", "true"], 2)[0] == 0
    prefix = ["sudo", "-n"] if elevated else []
    gdu = shutil.which("gdu")
    scanner = "gdu" if gdu else "du"
    scanner_fallback = not bool(gdu)
    scanner_limitations = ["allocated_blocks_not_exact_physical_ownership"]
    scanner_version = None
    if gdu:
        version_code, version_out, _version_err = run([gdu, "--version"], 2)
        if version_code == 0:
            scanner_version = (version_out.splitlines() or [None])[0]
    filesystems = []
    findings = []
    coverage = [{
        "category": "mount_inventory",
        "boundary_id": None,
        "status": mount_status,
        "duration_ms": max(int((time.monotonic() - deep_started) * 1000), 0),
        "confidence": "high" if mount_status == "complete" else "low",
        "privilege_sufficient": True,
        "reason": None if mount_status == "complete"
        else "mount_inventory_unavailable",
    }]
    directory_allocated = 0
    directory_states = set()
    attribution_rechecks = []
    public_mount_ids = {
        row.get("mount_id"): rid(
            "mount", str(row.get("mount_id")) + "\0" + row["mount_point"],
        )
        for row in rows if row.get("mount_id")
    }
    for index, row in enumerate(rows):
        mount = row["mount_point"]
        selected_mount = mount in selected
        filesystem_id = rid(
            "filesystem", str(row.get("device") or row.get("source") or "")
            + "\0" + mount,
        )
        category_started = time.monotonic()
        observed = None
        hardlinks = "unavailable"
        reason = None
        nested_mounts = sorted(
            other["mount_point"] for other in rows
            if other["mount_point"] != mount
            and other["mount_point"].startswith(mount.rstrip("/") + "/")
        )
        if selected_mount and time.monotonic() < DEADLINE:
            if gdu:
                argv = prefix + [
                    gdu, "-n", "-p", "-c", "--no-prefix", "-x",
                    "--depth", "4",
                    "--no-delete", "--no-spawn-shell", "--no-view-file",
                ]
                argv.extend("--exclude=" + path for path in nested_mounts)
                argv.append(mount)
                multiplier = 1
            else:
                argv = prefix + ["du", "-x", "-k", "-d", "4"]
                argv.extend("--exclude=" + path for path in nested_mounts)
                argv.append(mount)
                multiplier = 1024
            directory_timeout = min(
                max(BUDGET_SECONDS - 90, 120), 600,
            )
            code, out, _err = run(argv, directory_timeout)
            if code not in {0, 124} and gdu and time.monotonic() < DEADLINE:
                scanner = "du"
                scanner_version = None
                scanner_fallback = True
                scanner_limitations.append("gdu_failed_fell_back_to_du")
                argv = prefix + ["du", "-x", "-k", "-d", "4"]
                argv.extend("--exclude=" + path for path in nested_mounts)
                argv.append(mount)
                multiplier = 1024
                code, out, _err = run(argv, directory_timeout)
            if code == 0 or (code == 124 and out.strip()):
                try:
                    ranked, observed = parse_ranked_sizes(
                        out, filesystem_id, mount, multiplier, {
                        str(HOME): "Sandbox home",
                        str(HOME.parent): "Sandbox host account",
                        str(HOME / "runtime"): "Sandbox runtime data",
                        str(HOME / "deploy-src"):
                            "Sandbox deployment sources",
                        str(HOME / "sb-src"): "Sandbox tool source",
                        **({
                            str(docker_root): "Docker data root",
                            str(docker_root / "overlay2"):
                                "Docker image and container layers",
                            str(docker_root / "volumes"):
                                "Docker volume data",
                            str(docker_root / "buildkit"):
                                "Docker build cache data",
                            str(docker_root / "containers"):
                                "Docker container metadata and logs",
                            str(docker_root / "image"):
                                "Docker image metadata",
                        } if docker_root else {}),
                        },
                    )
                except Exception:
                    ranked, observed = [], None
                if observed is None:
                    state = "unavailable"
                    reason = "directory_parser_failure"
                else:
                    findings.extend(ranked)
                    state = "complete" if code == 0 else "partial"
                    hardlinks = "confirmed" if code == 0 else "partial"
                    if state == "partial":
                        reason = "directory_measurement_timed_out_with_partial"
                    directory_allocated += observed
                    attribution_rechecks.append((
                        mount, observed,
                        "gdu" if multiplier == 1 else "du",
                        tuple(nested_mounts),
                    ))
            else:
                state = "timed_out" if code == 124 else "unavailable"
                reason = (
                    "directory_measurement_timed_out"
                    if state == "timed_out"
                    else "directory_measurement_unavailable"
                )
        elif selected_mount:
            state, reason = "timed_out", "overall_budget_exhausted"
        else:
            state, reason = "not_selected", "unrelated_filesystem"
        if selected_mount:
            directory_states.add(state)
        filesystems.append({
            "filesystem_id": filesystem_id,
            "device": row.get("device"),
            "display_name": (
                "root filesystem" if mount == host_mount
                else "filesystem " + str(index + 1)
            ),
            "filesystem_type": row.get("filesystem_type") or "unknown",
            "total_bytes": row["total_bytes"],
            "used_bytes": row["used_bytes"],
            "available_bytes": row["available_bytes"],
            "writable": bool(row.get("writable")),
            "selected": selected_mount,
            "selection_reason": selected.get(mount, "unrelated"),
            "status": state,
            "observed_allocated_bytes": observed,
            "hardlink_deduplication": hardlinks,
            "mount_id": public_mount_ids.get(row.get("mount_id")),
            "parent_mount_id": public_mount_ids.get(
                row.get("parent_mount_id"),
            ),
            "capacity_scope_id": rid(
                "capacity", str(row.get("device") or row.get("source") or mount),
            ),
            "mount_flags": row.get("mount_flags") or [
                "rw" if row.get("writable") else "ro"
            ],
            "limitations": (
                ["nested_mount_excluded"]
                if selected_mount and nested_mounts
                and state in {"complete", "partial"} else []
            ),
        })
        coverage.append({
            "category": "directory",
            "boundary_id": filesystem_id,
            "status": state,
            "duration_ms": max(
                int((time.monotonic() - category_started) * 1000), 0,
            ),
            "confidence": (
                "high" if state == "complete"
                else "medium"
                if state in {"partial", "not_selected"} else "low"
            ),
            "privilege_sufficient": (
                elevated or state in {"complete", "partial", "not_selected"}
            ),
            "reason": reason,
        })
    directory_status = (
        "complete" if directory_states == {"complete"}
        else "partial" if "partial" in directory_states
        else "timed_out" if "timed_out" in directory_states else "partial"
    )
    capabilities = [{
        "category": "directory",
        "name": scanner,
        "version": scanner_version,
        "fallback": scanner_fallback,
        "privilege": "elevated" if elevated else "unprivileged",
        "status": directory_status,
        "limitations": scanner_limitations,
    }]
    deleted_started = time.monotonic()
    lsof = shutil.which("lsof")
    deleted_bytes = 0
    if lsof and time.monotonic() < DEADLINE:
        try:
            code, out, _err = run(
                prefix + [lsof, "-nP", "-FpcfDitsn", "+L1"], 20,
            )
        except Exception:
            code, out = 127, ""
        if code in {0, 1}:
            try:
                deleted, deleted_bytes = deleted_open_findings(
                    out, filesystems,
                )
            except Exception:
                deleted, deleted_bytes = [], 0
                deleted_status = "unavailable"
                deleted_reason = "deleted_open_parser_failure"
            else:
                findings.extend(deleted)
                if elevated:
                    deleted_status, deleted_reason = "complete", None
                else:
                    deleted_status = "partial"
                    deleted_reason = (
                        "deleted_open_visibility_requires_elevation"
                    )
                if any(item["limitations"] for item in deleted):
                    deleted_status = "partial"
                    deleted_reason = "deleted_open_allocated_blocks_incomplete"
        else:
            deleted_status = "timed_out" if code == 124 else "unavailable"
            deleted_reason = "deleted_open_measurement_unavailable"
    else:
        deleted_status = (
            "timed_out" if time.monotonic() >= DEADLINE else "unavailable"
        )
        deleted_reason = (
            "overall_budget_exhausted"
            if deleted_status == "timed_out" else "lsof_unavailable"
        )
    capabilities.append({
        "category": "deleted_open",
        "name": "lsof" if lsof else "unavailable",
        "version": None,
        "fallback": False,
        "privilege": (
            "elevated" if elevated else
            "unprivileged" if lsof else "unavailable"
        ),
        "status": deleted_status,
        "limitations": (
            [] if elevated else ["other_user_processes_may_be_inaccessible"]
        ),
    })
    coverage.append({
        "category": "deleted_open",
        "boundary_id": None,
        "status": deleted_status,
        "duration_ms": max(
            int((time.monotonic() - deleted_started) * 1000), 0,
        ),
        "confidence": "high" if deleted_status == "complete" else "low",
        "privilege_sufficient": elevated,
        "reason": deleted_reason,
    })
    docker_started = time.monotonic()
    try:
        code, out, _err = run(
            ["docker", "system", "df", "-v", "--format", "json"], 30,
        )
    except Exception:
        code, out = 127, ""
    if code == 0:
        try:
            docker_findings, logical_bytes = docker_deep_findings(out)
        except Exception:
            docker_findings, logical_bytes = [], 0
            docker_status = "unavailable"
            docker_reason = "docker_accounting_parser_failure"
        else:
            findings.extend(docker_findings)
            docker_status, docker_reason = "complete", None
    else:
        logical_bytes = 0
        docker_status = "timed_out" if code == 124 else "unavailable"
        docker_reason = "docker_accounting_unavailable"
    capabilities.append({
        "category": "container_storage",
        "name": "docker_system_df",
        "version": None,
        "fallback": False,
        "privilege": "unprivileged",
        "status": docker_status,
        "limitations": ["logical_engine_accounting"],
    })
    coverage.append({
        "category": "container_storage",
        "boundary_id": None,
        "status": docker_status,
        "duration_ms": max(
            int((time.monotonic() - docker_started) * 1000), 0,
        ),
        "confidence": "high" if docker_status == "complete" else "low",
        "privilege_sufficient": docker_status == "complete",
        "reason": docker_reason,
    })
    selected_rows = [
        row for row in rows if row["mount_point"] in selected
    ]
    selected_scope_ids = {
        rid(
            "capacity",
            str(row.get("device") or row.get("source") or row["mount_point"]),
        )
        for row in selected_rows
    }
    used = sum(row["used_bytes"] for row in selected_rows)
    raw_accounted = directory_allocated + deleted_bytes
    accounted = min(raw_accounted, used)
    findings.sort(key=lambda item: (
        item["observed_bytes"], item["kind"], item["finding_id"],
    ), reverse=True)
    attributed_before = 0
    attributed_after = 0
    attributed_rechecks_complete = bool(attribution_rechecks)
    for mount, previous, recheck_scanner, nested_mounts in attribution_rechecks:
        if time.monotonic() >= DEADLINE:
            attributed_rechecks_complete = False
            break
        if recheck_scanner == "gdu" and gdu:
            argv = prefix + [
                gdu, "-n", "-p", "-c", "--no-prefix", "-x",
                "--depth", "0", "--no-delete", "--no-spawn-shell",
                "--no-view-file",
            ]
            multiplier = 1
        else:
            argv = prefix + ["du", "-x", "-k", "-s"]
            multiplier = 1024
        argv.extend("--exclude=" + path for path in nested_mounts)
        argv.append(mount)
        try:
            code, out, _err = run(argv, 30)
        except Exception:
            attributed_rechecks_complete = False
            continue
        if code != 0:
            attributed_rechecks_complete = False
            continue
        try:
            _ranked, current = parse_ranked_sizes(
                out, "attribution-drift", mount, multiplier,
            )
        except Exception:
            attributed_rechecks_complete = False
            continue
        attributed_before += previous
        attributed_after += current
    attributed_drift = (
        abs(attributed_after - attributed_before)
        if attributed_rechecks_complete else 0
    )
    coverage.append({
        "category": "attributed_drift",
        "boundary_id": None,
        "status": "complete" if attributed_rechecks_complete else "partial",
        "duration_ms": 0,
        "confidence": "high" if attributed_rechecks_complete else "low",
        "privilege_sufficient": True,
        "reason": None if attributed_rechecks_complete
        else "attributed_recheck_unknown",
    })
    try:
        current_rows, current_status = df_rows()
    except Exception:
        current_rows, current_status = [], "unavailable"
    current_by_device = {
        (row.get("device") or row.get("source") or row["mount_point"]): row
        for row in current_rows
    }
    selected_devices = [
        row.get("device") or row.get("source") or row["mount_point"]
        for row in selected_rows
    ]
    capacity_recheck_complete = (
        current_status == "complete"
        and all(device in current_by_device for device in selected_devices)
    )
    current_used = sum(
        current_by_device[device]["used_bytes"] for device in selected_devices
        if device in current_by_device
    )
    capacity_drift = (
        abs(current_used - used) if capacity_recheck_complete else 0
    )
    coverage.append({
        "category": "capacity_drift",
        "boundary_id": None,
        "status": "complete" if capacity_recheck_complete else "partial",
        "duration_ms": 0,
        "confidence": "high" if capacity_recheck_complete else "low",
        "privilege_sufficient": True,
        "reason": None if capacity_recheck_complete
        else "capacity_recheck_unknown",
    })
    drift_threshold = max(int(used * 0.01), 64 * 1024 * 1024)
    drift_bytes = max(capacity_drift, attributed_drift)
    incomplete = any(
        item["status"] not in {"complete", "not_selected"}
        for item in coverage
    )
    # Device identities are needed only while correlating lsof and capacity.
    # Do not expose them in the compact public payload.
    for item in filesystems:
        item.pop("device", None)
    return {
        "status": "partial" if incomplete else "complete",
        "capacity_scope_id": (
            next(iter(selected_scope_ids))
            if len(selected_scope_ids) == 1 else None
        ),
        "filesystems": filesystems,
        "findings": findings[:300],
        "capabilities": capabilities,
        "coverage": coverage,
        "reconciliation": {
            "used_bytes": used,
            "directory_allocated_bytes": directory_allocated,
            "deleted_open_bytes": deleted_bytes,
            "observable_overhead_bytes": 0,
            "overlapping_logical_bytes": logical_bytes,
            "accounted_bytes": accounted,
            "residual_unexplained_bytes": used - accounted,
            "overage_bytes": max(raw_accounted - used, 0),
            "drift_bytes": drift_bytes,
            "drift_material": drift_bytes > drift_threshold,
            "capacity_drift_bytes": capacity_drift,
            "attributed_drift_bytes": attributed_drift,
            "capacity_drift_material": capacity_drift > drift_threshold,
            "attributed_drift_material": attributed_drift > drift_threshold,
        },
    }

__JOB_LIST_PARSER__


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
    job_list_error = None
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
                rows = parse_job_list_payload(payload)
                job_index = {"jobs": rows, "artifacts": []}
                artifacts_complete = False
                if len(rows) >= 200:
                    jobs_ok = False
            except (json.JSONDecodeError, ValueError) as exc:
                job_index = None
                job_list_error = str(exc)
    if job_index is None:
        jobs = []
        artifacts = []
        jobs_ok = False
        outcomes.append({
            "category": "job_registry",
            "status": "unavailable",
            "reason": "job_list_invalid_shape" if job_list_error else None,
        })
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
    deep_requested = bool(REQUEST.get("deep"))
    resource_thorough = thorough and not deep_requested
    focus = REQUEST.get("focus")
    target_kind = REQUEST.get("target_kind")
    target_locator = str(REQUEST.get("target_locator") or "")
    targeted = bool(target_kind and target_locator)
    usage = shutil.disk_usage("/")
    root_stat = os.stat("/")
    root_device = (
        str(os.major(root_stat.st_dev)) + ":" + str(os.minor(root_stat.st_dev))
    )
    capacity_scope_id = rid("capacity", root_device)
    identity_source = platform.node() + ":" + str(root_stat.st_dev) + ":" + str(HOME)
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
    PHASE = "workspace_ownership"
    workspace_projection = load_workspace_projection()
    PHASE = "docker_inventory"
    inventory, outcomes = docker_inventory()
    outcomes.extend(lifecycle_outcomes)
    outcomes.append({
        "category": "workspace_ownership",
        "status": (
            "complete" if isinstance(workspace_projection, dict)
            and not (workspace_projection.get("counts") or {}).get("incomplete")
            and not (workspace_projection.get("counts") or {}).get("unresolved")
            and not (workspace_projection.get("counts") or {}).get("conflict")
            else "partial" if isinstance(workspace_projection, dict)
            else "unavailable"
        ),
        "reason": "workspace_index_unavailable" if workspace_projection is None else None,
    })
    resources = []
    deep = None
    if not targeted and focus is None and deep_requested:
        PHASE = "deep_attribution"
        try:
            deep = deep_attribution(
                capacity,
                tuple(Path(path) for path in protected_paths),
            )
        except Exception:
            outcomes.append({
                "category": "deep_attribution", "status": "unavailable",
                "reason": "category_failure_isolated",
            })
    if not targeted and focus is None and not deep_requested:
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
        owner_kind, owner_id, owner_evidence, owner_protected = workspace_owner(
            workspace_projection, "compose_project", project,
        )
        oneoff = str(labels.get("com.docker.compose.oneoff") or "").lower() == "true"
        locator = str(container.get("Id") or container.get("Name") or "")
        if targeted and (target_kind != "container" or target_locator != locator):
            continue
        raw_size = container.get("SizeRw")
        measured = isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size >= 0
        workspace_active = "workspace_active_reference" in owner_evidence
        if running or workspace_active:
            classification = "active"
            references = (("running_container",) if running else ()) + (
                ("workspace_active_reference",) if workspace_active else ()
            )
        elif owner_kind == "unknown":
            classification = "unverified"
            references = ()
        elif project in active_projects:
            classification = "retained"
            references = ("live_compose_project",)
        elif owner_protected or project in protected_projects:
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
            owner_kind, owner_id, classification,
            "measured" if measured else "unavailable", raw_size if measured else None,
            raw_size if measured and classification == "disposable_cache" else 0,
            references,
            (
                *owner_evidence,
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
        owner_kind, owner_id, owner_evidence, owner_protected = (
            workspace_owner(workspace_projection, "compose_project", project)
            if project else ("unmanaged", None, ("ownership_unverified",), False)
        )
        state, measured_size, error = "not_measured", None, None
        if not project:
            classification = "unmanaged"
        elif active or "workspace_active_reference" in owner_evidence:
            classification = "active"
        elif owner_kind == "unknown":
            classification = "unverified"
        elif project in active_projects:
            classification = "retained"
        elif owner_protected or project in protected_projects:
            classification = "retained"
        else:
            classification = "unverified"
        if thorough and focus != "cache" and project and not active \
                and "workspace_active_reference" not in owner_evidence:
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
            and owner_kind != "unknown"
            and not owner_protected
            and project not in protected_projects
            and lifecycle_complete
            and state == "measured"
        ):
            classification = "stale_candidate"
        references = (
            ("live_container_mount",) if active else
            ("workspace_active_reference",)
            if "workspace_active_reference" in owner_evidence else
            ("live_compose_project",)
            if project in active_projects else
            ("instance_or_job_registry",)
            if owner_protected or project in protected_projects else ()
        )
        resources.append(observation(
            "volume", name, name, owner_kind, owner_id,
            classification, state, measured_size,
            measured_size if classification == "stale_candidate" else 0,
            references,
            (
                tuple(owner_evidence)
                if (
                    active or project in active_projects
                    or project in protected_projects
                ) else
                tuple((*owner_evidence, "registry_and_job_absence"))
                if lifecycle_complete else
                tuple((*owner_evidence, "lifecycle_evidence_unavailable"))
            ) if project else ("ownership_unverified",),
            (error,) if error else (),
        ))
    for network in inventory["networks"]:
        network_id = network.get("Id")
        network_name = str(network.get("Name") or network_id or "")
        if network_name in {"bridge", "host", "none"}:
            continue
        project = owner(network.get("Labels"))
        if not isinstance(network_id, str) or not network_id:
            continue
        if targeted and (
            target_kind != "network" or target_locator != network_id
        ):
            continue
        active = bool(network.get("Containers"))
        if project:
            owner_kind, owner_id, owner_evidence, owner_protected = workspace_owner(
                workspace_projection, "compose_project", project,
            )
            classification = (
                "active" if active or "workspace_active_reference" in owner_evidence else
                "retained" if project in active_projects else
                "retained" if owner_protected or project in protected_projects else
                # A stopped job is not sufficient evidence that its network
                # is stale; require an explicit lifecycle release signal.
                "unverified"
            )
            evidence = owner_evidence
            if not active and "workspace_active_reference" not in owner_evidence \
                    and classification == "unverified":
                evidence += ("network_liveness_unverified",)
            references = ("connected_container",) if active else (
                ("workspace_active_reference",)
                if "workspace_active_reference" in owner_evidence else (
                "live_compose_project",) if project in active_projects else (
                "instance_or_job_registry",) if owner_protected or project in protected_projects else ())
        else:
            labels = network.get("Labels")
            owner_kind = "foreign" if isinstance(labels, dict) and labels.get(
                "com.docker.compose.project"
            ) else "unmanaged"
            owner_id = None
            classification = "active" if active else "unmanaged"
            evidence = ("ownership_unverified",)
            references = ("connected_container",) if active else ()
        resources.append(observation(
            "network", network_id, network_name,
            owner_kind, owner_id, classification,
            "measured", 0, 0, references, evidence,
            capacity_accounted=False,
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
        owner_kind, owner_id, owner_evidence, owner_protected = workspace_owner(
            workspace_projection, "compose_project", project,
        )
        used = locator in used_images
        raw_size = image.get("Size")
        measured = (
            isinstance(raw_size, int)
            and not isinstance(raw_size, bool)
            and raw_size >= 0
        )
        classification = (
            "active" if used or "workspace_active_reference" in owner_evidence else
            "retained" if owner_protected or project in protected_projects else
            "unverified" if owner_kind == "unknown" else
            "disposable_cache" if lifecycle_complete else
            "unverified"
        )
        display = next(iter(image.get("RepoTags") or ()), locator)
        resources.append(observation(
            "image", locator, str(display), owner_kind, owner_id,
            classification,
            "measured" if measured else "unavailable",
            raw_size if measured else None,
            raw_size if measured and classification == "disposable_cache" else 0,
            (("container_image",) if used else ()) + (
                ("workspace_active_reference",)
                if "workspace_active_reference" in owner_evidence else ()
            ) if used or "workspace_active_reference" in owner_evidence else (
                ("instance_or_job_registry",)
                if owner_protected or project in protected_projects else ()
            ),
            owner_evidence,
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
                if is_workspace:
                    workspace_kind, workspace_id, workspace_evidence, _workspace_protected = workspace_owner(
                        workspace_projection, "runtime_instance", path.name,
                    )
                    if workspace_kind == "workspace":
                        owner_kind, owner_id = workspace_kind, workspace_id
                    elif workspace_kind == "unknown":
                        owner_kind, owner_id = "unknown", None
                        if classification == "stale_candidate":
                            classification = "unverified"
                        references = tuple(dict.fromkeys(
                            tuple(references) + tuple(workspace_evidence),
                        ))
                    else:
                        owner_kind, owner_id = "workspace", path.name
                else:
                    owner_kind, owner_id = "project", path.name
                state, measured_size, error = size(path, resource_thorough)
                if (
                    classification == "stale_candidate"
                    and state != "measured"
                ):
                    classification = "unverified"
                item = observation(
                    "worktree", str(path), path.name,
                    owner_kind, owner_id,
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
        state, measured_size, error = size(path, resource_thorough)
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
        "capacity_scope_id": capacity_scope_id,
        "resources": resources,
        "category_outcomes": outcomes,
        "drift": None,
        "deep_attribution": deep,
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
    if kind == "network":
        # A network locator alone cannot prove inactive leases, containers, or
        # jobs.  Keep network recovery in the confirmation-gated workspace
        # lifecycle and refuse direct deletion from resource cleanup.
        return {
            "status": "failed",
            "reason": "network_lifecycle_revalidation_required",
        }
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


def parse_job_list_payload(payload: dict) -> list[dict]:
    """Decode the canonical top-level job-list response.

    ``_program`` injects this exact function source into the isolated remote
    probe, keeping the host and remote consumers on one parser contract.
    """
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("job-list response must be a top-level ok object")
    if "data" in payload:
        raise ValueError("job-list response must expose top-level jobs")
    rows = payload.get("jobs")
    if not isinstance(rows, list):
        raise ValueError("job-list response jobs must be a top-level list")
    if not all(isinstance(item, dict) for item in rows):
        raise ValueError("job-list response jobs must contain objects")
    return rows


def _program(request: dict) -> str:
    import inspect

    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
    parser_source = inspect.getsource(parse_job_list_payload)
    return _REMOTE_PROGRAM.replace("__JOB_LIST_PARSER__", parser_source).replace(
        "__REQUEST__", repr(encoded),
    )


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
        try:
            result = execute(
                entry,
                (
                    'sandbox_runtime="${SANDBOX_HOME:-$HOME/sandbox}/sb-src"; '
                    'PYTHONPATH="$sandbox_runtime" python3 -'
                ),
                input_data=_program(request),
                timeout=max(int(timeout), 1),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
            return ProcessResult(tuple(exc.cmd) if isinstance(exc.cmd, (list, tuple)) else ("ssh",),
                                 124, stdout, stderr)
        return ProcessResult(
            tuple(getattr(result, "args", getattr(result, "argv", ("ssh",)))),
            int(result.returncode),
            str(result.stdout or ""),
            str(result.stderr or ""),
        )

    @staticmethod
    def _cancelled(signal) -> bool:
        if isinstance(signal, bool):
            return signal
        probe = getattr(signal, "is_set", None)
        if not callable(probe) and callable(signal):
            probe = signal
        try:
            return bool(probe()) if callable(probe) else False
        except Exception:
            return False

    def observe(
        self, *, thorough: bool, budget_seconds: float,
        progress=None, focus: str | None = None, deep: bool = False,
        cancelled=False,
    ) -> ProviderSnapshot:
        entry = self._entry()
        if self._cancelled(cancelled):
            return ProviderSnapshot(
                self.target(), None, (),
                ({"category": "remote_probe", "status": "cancelled"},),
            )
        if progress:
            progress("remote_probe")
        response = self._ssh(entry, {
            "action": "observe",
            "thorough": bool(thorough),
            "budget_seconds": float(budget_seconds),
            "managed_host": bool(entry.get("provisioned")),
            "remote_name": self.remote_name,
            "focus": focus,
            "deep": bool(deep),
            "cancelled": self._cancelled(cancelled),
        }, budget_seconds + 5)
        try:
            payload = json.loads(response.stdout)
            identity = payload["identity"]
            resources = tuple(
                _observation(item) for item in payload.get("resources") or ()
            )
            target = StorageTarget("remote", self.remote_name, identity)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            status = (
                "timed_out" if response.returncode == 124 else "unavailable"
            )
            return ProviderSnapshot(
                self.target(), None, (),
                ({"category": "remote_probe", "status": status},),
            )
        self._target = target
        outcomes = list(payload.get("category_outcomes") or ())
        terminal = None
        if self._cancelled(cancelled):
            terminal = "cancelled"
        elif response.returncode == 124:
            terminal = "timed_out"
        elif response.returncode != 0:
            terminal = "disconnected"
        if terminal is not None:
            outcomes.append({"category": "remote_transport", "status": terminal})
        return ProviderSnapshot(
            target,
            payload.get("capacity"),
            resources,
            tuple(outcomes),
            payload.get("drift"),
            DeepAttribution.from_dict(payload.get("deep_attribution")),
            payload.get("capacity_scope_id"),
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
