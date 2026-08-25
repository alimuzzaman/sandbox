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
    NetworkLifecycle,
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
import select
import shutil
import signal
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
ENVELOPE = None
DIRECTORY_CACHE_PATH = RUNTIME / "resources" / "directory-index.json"
DIRECTORY_CACHE_TTL = max(float(REQUEST.get("directory_cache_ttl") or 21600), 0)
DIRECTORY_CACHE_MODE = str(REQUEST.get("directory_cache") or "auto")
# Keep each inspect response comfortably below the bounded stdout cap.  A
# large container environment or mount list must not invalidate every row.
DOCKER_INSPECT_BATCH_SIZE = 10
# cache_only is the always-available fast path: read the cached host index,
# never walk the disk, and never pay for engine inventory.
FAST = DIRECTORY_CACHE_MODE == "cache_only"
# Rows below this size are noise for a host-attribution report; dropping them
# while streaming keeps a full-depth walk inside a bounded response.
DIRECTORY_MIN_BYTES = max(int(REQUEST.get("directory_min_bytes") or 33554432), 0)
DIRECTORY_DEPTH = min(max(int(REQUEST.get("directory_depth") or 6), 1), 12)


INDEX_ROWS = {}


def indexed_size(path):
    # Reuse one host walk instead of paying for a du per managed path.
    return INDEX_ROWS.get(str(path))


def emit(payload):
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()

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

TIMEOUT_TOOL = shutil.which("timeout")


def bounded(argv, seconds):
    # An elevated child runs as root, so this unprivileged probe cannot
    # signal it; only a bound that runs *inside* sudo can stop it on time.
    if TIMEOUT_TOOL and list(argv[:2]) == ["sudo", "-n"]:
        return list(argv[:2]) + [
            TIMEOUT_TOOL, "-k", "1", str(max(int(seconds), 1)),
        ] + list(argv[2:])
    return list(argv)


def spawn(argv):
    # Start a child in its own session so the whole tree stays killable.
    #
    # ``sudo`` forks the real worker, so killing only the direct child leaves
    # the worker alive holding the stdout pipe; the parent then blocks past its
    # own budget.  A dedicated session lets one killpg end the whole tree.
    return subprocess.Popen(
        argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )


def terminate(process):
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def run(argv, timeout):
    remaining = max(min(float(timeout), DEADLINE - time.monotonic()), 0.01)
    try:
        process = spawn(bounded(argv, remaining))
    except OSError:
        return 127, "", "unavailable"
    try:
        stdout, stderr = process.communicate(timeout=remaining)
        return process.returncode, (stdout or "")[:4000000], (stderr or "")[:4096]
    except subprocess.TimeoutExpired:
        terminate(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return 124, (stdout or "")[:4000000], ((stderr or "") + "\ntimed out")[:4096]


def walk_rows(argv, timeout, multiplier, keep_prefixes=()):
    # Stream a directory walk, keeping material and managed rows only.
    #
    # Returns ``(rows, complete)``.  A walk that runs out of time keeps every
    # row it already produced instead of discarding the whole measurement.
    deadline = time.monotonic() + max(
        min(float(timeout), DEADLINE - time.monotonic()), 0.01,
    )
    rows = []
    try:
        process = spawn(bounded(argv, deadline - time.monotonic()))
    except OSError:
        return rows, False
    complete = False
    try:
        while True:
            waiting = deadline - time.monotonic()
            if waiting <= 0:
                break
            # readline() alone can block past the budget on a slow subtree.
            if not select.select([process.stdout], (), (), waiting)[0]:
                break
            line = process.stdout.readline()
            if not line:
                try:
                    complete = process.wait(timeout=2) == 0
                except subprocess.TimeoutExpired:
                    complete = False
                break
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            try:
                measured = int(parts[0]) * multiplier
            except ValueError:
                continue
            path = parts[1].strip()
            if measured < DIRECTORY_MIN_BYTES and not any(
                path == prefix or path.startswith(prefix.rstrip("/") + "/")
                for prefix in keep_prefixes
            ):
                continue
            rows.append((measured, path))
            if len(rows) >= 20000:
                break
    finally:
        if process.poll() is None:
            terminate(process)
        try:
            process.stdout.close()
            process.stderr.close()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return rows, complete


def walk_budget():
    # A refresh exists to finish the walk, so it keeps most of the budget;
    # an ordinary deep pass leaves room for the other categories.
    remaining = DEADLINE - time.monotonic()
    share = 0.9 if DIRECTORY_CACHE_MODE == "refresh" else 0.7
    return max(min(remaining * share, remaining - 15), 1.0)


def directory_cache_read():
    try:
        payload = json.loads(DIRECTORY_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("mounts"), dict):
        return None
    return payload


def directory_cache_write(payload):
    try:
        DIRECTORY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        staging = DIRECTORY_CACHE_PATH.with_name(
            DIRECTORY_CACHE_PATH.name + ".staging",
        )
        staging.write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8",
        )
        os.replace(staging, DIRECTORY_CACHE_PATH)
    except (OSError, UnicodeError, ValueError):
        return False
    return True


def directory_index(mount, argv, multiplier, timeout, keep_prefixes):
    # Return a ranked directory index for one mount, cache-first.
    #
    # A full host walk cannot finish inside an interactive budget on a large
    # host, so the walk result is cached with its timestamp and completeness
    # and reused until it expires.  The caller always learns which one it got.
    # Always read the store so a refresh of one mount cannot drop another.
    cached = directory_cache_read()
    entry = (
        (cached or {}).get("mounts", {}).get(mount)
        if cached and DIRECTORY_CACHE_MODE != "refresh" else None
    )
    now = time.time()
    if isinstance(entry, dict) and isinstance(entry.get("rows"), list):
        age = max(now - float(entry.get("created_at") or 0), 0)
        fresh = age <= DIRECTORY_CACHE_TTL
        if fresh or DIRECTORY_CACHE_MODE == "cache_only":
            cached_rows = [
                (int(item[0]), str(item[1]))
                for item in entry["rows"]
                if isinstance(item, (list, tuple)) and len(item) == 2
            ]
            INDEX_ROWS.update({path: measured for measured, path in cached_rows})
            return {
                "rows": cached_rows,
                "complete": bool(entry.get("complete")),
                "created_at": float(entry.get("created_at") or 0),
                "age_seconds": int(age),
                "stale": not fresh,
                "source": "cache",
            }
    if DIRECTORY_CACHE_MODE == "cache_only":
        return {
            "rows": [], "complete": False, "created_at": None,
            "age_seconds": None, "stale": True, "source": "cache_missing",
        }
    rows, complete = walk_rows(argv, timeout, multiplier, keep_prefixes)
    INDEX_ROWS.update({path: measured for measured, path in rows})
    index = {
        "rows": rows, "complete": complete, "created_at": now,
        "age_seconds": 0, "stale": False, "source": "scan",
    }
    if rows:
        payload = cached if isinstance(cached, dict) else {"mounts": {}}
        if not isinstance(payload.get("mounts"), dict):
            payload["mounts"] = {}
        previous = payload["mounts"].get(mount)
        # Never replace a complete walk with a shorter truncated one.
        if not (
            isinstance(previous, dict)
            and previous.get("complete")
            and not complete
            and max(now - float(previous.get("created_at") or 0), 0)
            <= DIRECTORY_CACHE_TTL
        ):
            payload["mounts"][mount] = {
                "created_at": now, "complete": complete,
                "rows": [[measured, path] for measured, path in rows],
            }
            payload["schema_version"] = 1
            index["cached"] = directory_cache_write(payload)
    return index

def size(path, thorough):
    indexed = indexed_size(path)
    if indexed is not None:
        return "measured", indexed, None
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

def _reference_counts(value):
    # Missing reference evidence is unknown, not an observed zero.
    names = ("leases", "containers", "jobs", "mounts")
    if not isinstance(value, dict):
        return tuple((name, None) for name in names), True
    normalized = {}
    for name in names:
        count = value.get(name)
        if count is None:
            normalized[name] = None
        elif isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            normalized[name] = count
        else:
            return (), False
    return tuple(sorted(normalized.items())), True

def workspace_owner_details(projection, resource_type, resource_id):
    # Resolve one exact binding; never infer ownership from a label/path.
    unavailable = {
        "owner_kind": "unknown", "owner_id": None,
        "evidence": ("workspace_index_unavailable",), "protected": False,
        "lifecycle": "indeterminate", "active_references": {},
        "observed_at": None, "active": False,
    }
    if not isinstance(projection, dict):
        return unavailable
    records = projection.get("records", projection.get("workspaces"))
    if not isinstance(records, list):
        return unavailable
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
            "tombstoned",
        } or status.lower() in {
            "invalid", "incomplete", "unresolved", "conflict", "indeterminate",
            "tombstoned",
        }:
            incomplete = True
            continue
        reference_counts, references_valid = _reference_counts(
            record.get("active_references"),
        )
        if not references_valid:
            incomplete = True
            continue
        reference_active = any(
            count is not None and count > 0 for _name, count in reference_counts
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
            matches.add((
                workspace_id, binding_status, reference_active,
                lifecycle.lower(), reference_counts, observed_at,
            ))
    if len(matches) == 1:
        workspace_id, _status, reference_active, lifecycle, reference_counts, observed_at = next(iter(matches))
        evidence = ("workspace_binding", resource_type)
        if reference_active:
            evidence += ("workspace_active_reference",)
        return {
            "owner_kind": "workspace", "owner_id": workspace_id,
            "evidence": evidence, "protected": True,
            "lifecycle": lifecycle, "active_references": dict(reference_counts),
            "observed_at": observed_at, "active": reference_active,
        }
    if len(matches) > 1:
        return {
            **unavailable,
            "evidence": ("workspace_alias_collision", resource_type),
        }
    return {
        **unavailable,
        "evidence": (
            "workspace_index_incomplete" if incomplete else "workspace_binding_missing",
        ),
    }

def workspace_owner(projection, resource_type, resource_id):
    # Backward-compatible owner tuple for existing remote consumers.
    details = workspace_owner_details(projection, resource_type, resource_id)
    return (
        details["owner_kind"], details["owner_id"],
        tuple(details["evidence"]), bool(details["protected"]),
    )

def _json_rows(text):
    try:
        value = json.loads(text or "[]")
    except (TypeError, json.JSONDecodeError):
        return None
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        return None
    return value

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
        category = "docker_" + key
        code, out, _err = run(list_argv, 10)
        identifiers = out.split()
        list_failed = code != 0
        if not identifiers:
            outcomes.append({
                "category": category,
                "status": (
                    "timed_out" if code == 124 else
                    "unavailable" if list_failed else "complete"
                ),
            })
            continue
        rows = []
        inspect_failed = False
        inspect_timed_out = False
        # Keep argv bounded and retain rows from successful batches when a
        # container disappears during inspection or one batch times out.
        for offset in range(0, len(identifiers), DOCKER_INSPECT_BATCH_SIZE):
            batch = identifiers[
                offset:offset + DOCKER_INSPECT_BATCH_SIZE
            ]
            code, inspected, _err = run(inspect_argv + batch, 20)
            parsed = _json_rows(inspected)
            if parsed is not None:
                rows.extend(parsed)
            if code != 0 or parsed is None or len(parsed) < len(batch):
                inspect_failed = True
            if code == 124:
                inspect_timed_out = True
        inventory[key] = rows
        if not inspect_failed and not list_failed:
            status = "complete"
        elif rows:
            status = "timed_out" if inspect_timed_out else "partial"
        else:
            status = (
                "timed_out" if inspect_timed_out or code == 124
                else "unavailable"
            )
        outcomes.append({"category": category, "status": status})
    code, out, _err = run(
        ["docker", "buildx", "du", "--format=json"], 20,
    )
    build_rows = []
    build_failed = code != 0
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            build_failed = True
            continue
        if isinstance(value, dict):
            build_rows.append(value)
        else:
            build_failed = True
    inventory["build_cache"] = build_rows
    if not build_failed:
        status = "complete"
    elif build_rows:
        status = "timed_out" if code == 124 else "partial"
    else:
        status = "timed_out" if code == 124 else "unavailable"
    outcomes.append({"category": "docker_build_cache", "status": status})
    return inventory, outcomes

def observation(kind, locator, display, owner_kind, owner_id, classification,
                size_state, size_bytes, reclaimable, references=(), evidence=(),
                errors=(), capacity_accounted=False, lifecycle=None,
                active_references=(), allocation_state=None,
                allocation_pool=None, cleanup_eligible=False,
                last_observed=None):
    value = {
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
    if kind == "network":
        value.update({
            "lifecycle": lifecycle or "indeterminate",
            "active_references": {
                str(key): count for key, count in active_references
            } if not isinstance(active_references, dict) else dict(active_references),
            "allocation": {
                "state": allocation_state or "unknown",
                "pool": allocation_pool,
            },
            "cleanup_eligible": bool(cleanup_eligible),
            "last_observed": last_observed,
        })
    return value

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

def managed_label(path, docker_root):
    # Name a sandbox-managed path; leave unmanaged host paths unnamed.
    #
    # Managed roots are named by the tool itself, so echoing their relative
    # path discloses nothing new while making the report actionable.
    for root, prefix in (
        (HOME, "Sandbox home"),
        (HOME.parent, "Sandbox host account"),
        (docker_root, "Docker data"),
    ):
        if root is None:
            continue
        root_text = str(root).rstrip("/")
        if not root_text:
            continue
        if path == root_text:
            return prefix
        if path.startswith(root_text + "/"):
            return prefix + "/" + path[len(root_text) + 1:]
    return None


def rank_directory_rows(
    rows, filesystem_id, root, safe_labels=None, docker_root=None,
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
        "/var/lib/containerd": "containerd content store",
    }
    safe_roots.update(safe_labels or {})
    ranked = []
    total = None
    for measured, path in rows:
        if path.rstrip("/") == str(root).rstrip("/"):
            total = measured
        else:
            ranked.append((measured, path))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    findings = [
        deep_finding(
            "directory", filesystem_id + ":" + path,
            safe_roots.get(path)
            or managed_label(path, docker_root)
            or ("entry " + str(index + 1)),
            measured,
            filesystem_id=filesystem_id,
            owner_kind="host",
            capacity_accounted=False,
            overlap="directory_root",
            guidance="monitoring_only",
            evidence=("allocated_blocks", "one_filesystem"),
        )
        for index, (measured, path) in enumerate(ranked[:300])
    ]
    paths = {os.path.normpath(path) for _measured, path in ranked}
    frontier_total = sum(
        measured for measured, path in ranked
        if not any(
            parent != os.path.normpath(path)
            and os.path.normpath(path).startswith(parent.rstrip("/") + os.sep)
            for parent in paths
        )
    )
    return findings, total if total is not None else frontier_total


def parse_ranked_sizes(
    output, filesystem_id, root, multiplier, safe_labels=None,
    docker_root=None,
):
    rows = []
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            measured = int(parts[0]) * multiplier
        except ValueError:
            continue
        rows.append((measured, parts[1].strip()))
    return rank_directory_rows(
        rows, filesystem_id, root, safe_labels, docker_root,
    )

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
    directory_indexes = {}
    # Managed roots stay in the index at any size so workspace-level and
    # runtime-level attribution survives the noise filter.
    keep_prefixes = tuple(str(path) for path in (
        HOME, DEPLOY, RUNTIME, Path("/var/lib/containerd"),
    ))
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
        if selected_mount and (
            time.monotonic() < DEADLINE or DIRECTORY_CACHE_MODE == "cache_only"
        ):
            if gdu:
                argv = prefix + [
                    gdu, "-n", "-p", "-c", "--no-prefix", "-x",
                    "--depth", str(DIRECTORY_DEPTH),
                    "--no-delete", "--no-spawn-shell", "--no-view-file",
                ]
                multiplier = 1
            else:
                argv = prefix + ["du", "-x", "-k", "-d", str(DIRECTORY_DEPTH)]
                multiplier = 1024
            argv.extend("--exclude=" + path for path in nested_mounts)
            argv.append(mount)
            # Leave the rest of the deep pass a share of the budget; a host
            # walk that consumes all of it starves every other category.
            # A refresh exists to finish the walk, so it keeps most of it.
            directory_timeout = walk_budget()
            index = directory_index(
                mount, argv, multiplier, directory_timeout, keep_prefixes,
            )
            if not index["rows"] and gdu and index["source"] == "scan":
                scanner = "du"
                scanner_version = None
                scanner_fallback = True
                scanner_limitations.append("gdu_failed_fell_back_to_du")
                argv = prefix + ["du", "-x", "-k", "-d", str(DIRECTORY_DEPTH)]
                argv.extend("--exclude=" + path for path in nested_mounts)
                argv.append(mount)
                multiplier = 1024
                index = directory_index(
                    mount, argv, multiplier, walk_budget(), keep_prefixes,
                )
            directory_indexes[mount] = index
            if index["rows"]:
                try:
                    ranked, observed = rank_directory_rows(
                        index["rows"], filesystem_id, mount, {
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
                        }, docker_root,
                    )
                except Exception:
                    ranked, observed = [], None
                if observed is None:
                    state = "unavailable"
                    reason = "directory_parser_failure"
                else:
                    findings.extend(ranked)
                    state = "complete" if index["complete"] else "partial"
                    hardlinks = "confirmed" if index["complete"] else "partial"
                    if state == "partial":
                        reason = "directory_measurement_timed_out_with_partial"
                    if index["source"] == "cache":
                        state = "complete" if (
                            index["complete"] and not index["stale"]
                        ) else "partial"
                        reason = (
                            "directory_index_cache_stale" if index["stale"]
                            else None if index["complete"]
                            else "directory_index_cache_partial"
                        )
                    directory_allocated += observed
                    if index["source"] == "scan" and index["complete"]:
                        attribution_rechecks.append((
                            mount, observed,
                            "gdu" if multiplier == 1 else "du",
                            tuple(nested_mounts),
                        ))
            else:
                state = (
                    "unavailable" if index["source"] == "cache_missing"
                    else "timed_out"
                )
                reason = (
                    "directory_index_cache_missing"
                    if index["source"] == "cache_missing"
                    else "directory_measurement_timed_out"
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
            "directory_index": ({
                "source": directory_indexes[mount]["source"],
                "complete": directory_indexes[mount]["complete"],
                "stale": directory_indexes[mount]["stale"],
                "age_seconds": directory_indexes[mount]["age_seconds"],
                "row_count": len(directory_indexes[mount]["rows"]),
                "depth": DIRECTORY_DEPTH,
                "minimum_row_bytes": DIRECTORY_MIN_BYTES,
            } if mount in directory_indexes else None),
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
    if lsof and not FAST and time.monotonic() < DEADLINE:
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
            "fast_mode_skipped" if FAST
            else "overall_budget_exhausted"
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
        code, out, _err = (
            (127, "", "") if FAST else run(
                ["docker", "system", "df", "-v", "--format", "json"], 30,
            )
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
        docker_reason = (
            "fast_mode_skipped" if FAST else "docker_accounting_unavailable"
        )
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
        "directory_index": ({
            "mount": next(iter(directory_indexes)),
            "source": next(iter(directory_indexes.values()))["source"],
            "complete": next(iter(directory_indexes.values()))["complete"],
            "stale": next(iter(directory_indexes.values()))["stale"],
            "age_seconds": next(iter(directory_indexes.values()))["age_seconds"],
            "depth": DIRECTORY_DEPTH,
            "minimum_row_bytes": DIRECTORY_MIN_BYTES,
            "ttl_seconds": int(DIRECTORY_CACHE_TTL),
            "mode": DIRECTORY_CACHE_MODE,
        } if directory_indexes else {
            "mount": None, "source": "not_measured", "complete": False,
            "stale": True, "age_seconds": None, "depth": DIRECTORY_DEPTH,
            "minimum_row_bytes": DIRECTORY_MIN_BYTES,
            "ttl_seconds": int(DIRECTORY_CACHE_TTL),
            "mode": DIRECTORY_CACHE_MODE,
        }),
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
    # Lifecycle evidence comes from the typed workspace projection.  The old
    # JsonRegistryRepository/registry.json path is intentionally not opened by
    # the resource probe; these names remain only as compatibility markers for
    # source-level boundary tests.  ``registry.sqlite3`` and read_resource_index
    # below belong to the feature-owned job service decoder, not this boundary.
    workspace_projection = load_workspace_projection()
    registry_records = (
        workspace_projection.get("records")
        if isinstance(workspace_projection, dict) else None
    )
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
            if not isinstance(instance, str) or not instance:
                instance = record.get("label")
            if isinstance(instance, str) and instance:
                protected_projects.update((instance, "sandbox-" + instance))
            project = record.get("project") or record.get("project_identity")
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
        if not path.exists():
            continue
        indexed = indexed_size(path)
        if indexed is not None:
            resources.append(observation(
                "host_root", str(path), display, "host",
                REQUEST.get("remote_name"), "retained", "measured", indexed, 0,
                ("monitoring_only",),
                ("filesystem_capacity_root", "directory_index"), (),
                capacity_accounted=True,
            ))
            continue
        if FAST:
            # The fast path answers from the cached index or not at all.
            status = "partial"
            continue
        if time.monotonic() >= DEADLINE:
            status = "timed_out"
            break
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
        # containerd keeps its own content store; `docker system df` never
        # reports it, so a docker-only report silently loses that space.
        (Path("/var/lib/containerd"), "containerd content store"),
    ):
        indexed = indexed_size(path)
        if indexed is not None:
            resources.append(observation(
                "engine_storage", str(path), display, "host",
                REQUEST.get("remote_name"), "retained", "measured", indexed, 0,
                ("monitoring_only",),
                ("docker_storage_root", "directory_index"), (),
            ))
            continue
        if FAST:
            status = "partial"
            continue
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

LEASE_DIR = RUNTIME / "resources" / "leases"
DELETION_DIR = RUNTIME / "resources" / "deletions"
LEASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# Kept in lockstep with sandbox/resources/reclaim.WORKSPACE_VOLUME_PATTERN.
# The probe re-asserts it independently so a malformed or hostile request can
# never reach `docker volume rm` for a volume holding live site data.
WORKSPACE_VOLUME = re.compile(
    r"^sandbox-(?P<workspace>.+)_[A-Za-z0-9.-]*node[-_]?modules$",
)
WORKSPACE_MARKERS = ("-workspace-", ".workspace-")


def hosted_site_names():
    names = set()
    for root in (RUNTIME / "hosts", DEPLOY / "hosts"):
        try:
            for child in root.iterdir():
                if child.is_dir():
                    names.add(child.name)
        except OSError:
            continue
    return names


def read_leases():
    leases = {}
    try:
        children = sorted(LEASE_DIR.iterdir(), key=lambda item: item.name)
    except OSError:
        return leases
    for child in children:
        if not child.name.endswith(".json"):
            continue
        try:
            payload = json.loads(child.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("name"), str):
            leases[payload["name"]] = payload
    return leases


def write_lease(name, payload):
    LEASE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = LEASE_DIR / ("." + name + ".staging")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(
        str(staging), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600,
    )
    try:
        os.write(descriptor, body.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(str(staging), str(LEASE_DIR / (name + ".json")))
    return payload


def lease_action():
    op = str(REQUEST.get("op") or "list")
    if op == "list":
        return {"ok": True, "op": op, "leases": read_leases()}
    name = REQUEST.get("name")
    if not isinstance(name, str) or not LEASE_NAME.fullmatch(name):
        return {"ok": False, "op": op, "reason": "invalid_lease_name"}
    leases = read_leases()
    if op == "get":
        return {"ok": True, "op": op, "leases": {
            name: leases[name],
        } if name in leases else {}}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = dict(leases.get(name) or {})
    record.update({"schema": 1, "name": name, "updated_at": now})
    if op == "release":
        references = REQUEST.get("active_references")
        if isinstance(references, dict) and any(
            value is None or (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
            ) for value in references.values()
        ):
            return {
                "ok": False, "op": op, "reason": "active_references",
            }
        record["released"] = True
        record["released_at"] = now
    elif op == "set":
        expires_at = REQUEST.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at:
            return {"ok": False, "op": op, "reason": "invalid_expiry"}
        record["expires_at"] = expires_at
        record["released"] = False
        record["released_at"] = None
    else:
        return {"ok": False, "op": op, "reason": "unsupported_lease_operation"}
    try:
        write_lease(name, record)
    except OSError:
        return {"ok": False, "op": op, "reason": "lease_write_failed"}
    return {"ok": True, "op": op, "leases": {name: record}}


def indexed_workspace_names(projection):
    # Return indexed workspace names and their workspace IDs.
    #
    # The IDs are what index reconciliation needs after a removal: the sanctioned
    # lifecycle command addresses a workspace by ID, never by directory name.
    names = {}
    if not isinstance(projection, dict):
        return names, False
    records = projection.get("records", projection.get("workspaces"))
    if not isinstance(records, list):
        return names, False
    for record in records:
        if not isinstance(record, dict):
            continue
        workspace_id = record.get("workspace_id")
        workspace_id = workspace_id if isinstance(workspace_id, str) else None
        label = record.get("label")
        if isinstance(label, str) and label:
            names.setdefault(label, workspace_id)
        for binding in record.get("bindings") or ():
            if not isinstance(binding, dict):
                continue
            if binding.get("resource_type", binding.get("type")) != (
                "runtime_instance"
            ):
                continue
            value = binding.get("resource_id", binding.get("id"))
            if isinstance(value, str) and value:
                names.setdefault(value, workspace_id)
    return names, True


def entry_mtime(path):
    # Use the newest of the entry root and its immediate children.
    #
    # A directory's own mtime does not move when a file changes deeper inside,
    # and reclamation needs activity, not just structural change.  One extra
    # scandir is cheap and catches the common "a build is writing in here" case.
    newest = None
    try:
        newest = path.lstat().st_mtime
    except OSError:
        return None
    try:
        with os.scandir(str(path)) as children:
            for child in children:
                try:
                    stamp = child.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if newest is None or stamp > newest:
                    newest = stamp
    except OSError:
        pass
    return newest


def bounded_entry_size(path):
    indexed = indexed_size(path)
    if indexed is not None:
        return "measured", indexed
    if FAST or DEADLINE - time.monotonic() < 4:
        return "not_measured", None
    code, out, _err = run(["du", "-sx", "-sk", str(path)], 5)
    if code:
        return ("timed_out" if code == 124 else "unavailable"), None
    try:
        return "measured", int(out.split()[0]) * 1024
    except (ValueError, IndexError):
        return "unavailable", None


def reclaim_inventory(inventory, protected_paths, projection, engine_complete):
    hosted = hosted_site_names()
    index_names, index_ok = indexed_workspace_names(projection)
    binds = {}
    running_volumes = set()
    for container in inventory.get("containers") or ():
        running = bool((container.get("State") or {}).get("Running"))
        identity = str(container.get("Id") or "")
        display = str(container.get("Name") or "").lstrip("/")
        for mount in container.get("Mounts") or ():
            if mount.get("Type") == "volume" and running and mount.get("Name"):
                running_volumes.add(mount["Name"])
            if mount.get("Type") != "bind":
                continue
            source = str(mount.get("Source") or "")
            if source:
                binds.setdefault(source, []).append({
                    "id": identity, "name": display, "running": running,
                })
    block = {
        "deployment_root": str(DEPLOY),
        "runtime_root": str(RUNTIME),
        "entries": [],
        "volumes": [],
        "scratch": [],
        "leases": read_leases(),
        "hosted_sites": sorted(hosted),
        "index_names": sorted(index_names),
        "workspace_ids": {
            name: value for name, value in index_names.items() if value
        },
        "index_available": bool(index_ok),
        "truncated": False,
        "unmeasured_count": 0,
        # Whether the *container* inventory is trustworthy is a different
        # question from whether every directory could be measured.  One
        # unmeasured directory must not turn every classification into
        # UNKNOWN, so the two are reported separately.
        "engine_complete": bool(engine_complete),
        "status": "complete" if engine_complete else "partial",
    }
    if not engine_complete:
        block["reason"] = "container_inventory_unavailable"
    # A fast status skips the engine inventory, so the entry walk is the only
    # work left; give it a small floor rather than returning an empty listing
    # when the shared deadline has already been consumed by lifecycle reads.
    deadline = DEADLINE
    if FAST:
        deadline = max(DEADLINE, time.monotonic() + min(3.0, BUDGET_SECONDS * 0.3))
    try:
        children = sorted(DEPLOY.iterdir(), key=lambda item: item.name)
    except OSError:
        block["status"] = "unavailable"
        block["reason"] = "deployment_root_unreadable"
        return block
    for child in children:
        if time.monotonic() >= deadline:
            block["truncated"] = True
            if block["status"] == "complete":
                block["status"] = "partial"
            break
        try:
            is_symlink = child.is_symlink()
            if not child.is_dir():
                continue
        except OSError:
            continue
        path = str(child)
        containers = []
        for source, records in binds.items():
            if source == path or source.startswith(path + os.sep):
                containers.extend(records)
        try:
            canonical = str(child.resolve(strict=False))
        except OSError:
            canonical = path
        references = protected_paths.get(canonical, ())
        state, measured = bounded_entry_size(child)
        if state != "measured":
            block["unmeasured_count"] += 1
            if block["status"] == "complete":
                block["status"] = "partial"
        block["entries"].append({
            "name": child.name,
            "path": path,
            "size_bytes": measured,
            "size_state": state,
            "mtime": entry_mtime(child),
            "is_workspace": any(
                marker in child.name for marker in WORKSPACE_MARKERS
            ),
            "is_symlink": bool(is_symlink),
            "containers": containers,
            "registry": "instance_registry" in references,
            "active_job": "retained_job" in references,
            "indexed": child.name in index_names,
            "hosted": child.name in hosted or child.name == "hosts",
            "protections": [],
        })
    for volume in inventory.get("volumes") or ():
        name = volume.get("Name")
        if not isinstance(name, str) or not name:
            continue
        mountpoint = str(volume.get("Mountpoint") or "")
        block["volumes"].append({
            "name": name,
            "project": owner(volume.get("Labels")),
            "size_bytes": indexed_size(Path(mountpoint)) if mountpoint else None,
            "mounted_running": name in running_volumes,
        })
    try:
        for child in sorted(RUNTIME.iterdir(), key=lambda item: item.name):
            if not child.name.startswith(".drive-volume-fallbacks-"):
                continue
            state, measured = bounded_entry_size(child)
            block["scratch"].append({
                "name": child.name,
                "path": str(child),
                "size_bytes": measured if state == "measured" else None,
                "mtime": entry_mtime(child),
            })
    except OSError:
        pass
    return block


def manifest_append(path, record):
    # Append one durable record.
    #
    # ``O_APPEND`` plus ``fsync`` and no temporary file: the manifest has to be
    # writable on a host with zero free bytes, where ``mkstemp`` + ``replace``
    # fails.  Appending to an already-created file reuses the last block until it
    # fills, which is what makes the record survive the exact situation that
    # produces it.
    descriptor = os.open(
        str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600,
    )
    try:
        os.write(
            descriptor,
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def refuse_target(kind, locator):
    # Re-assert every protection host-side; never trust the request.
    if kind == "volume":
        match = WORKSPACE_VOLUME.fullmatch(locator)
        if match is None or not any(
            marker in match.group("workspace") for marker in WORKSPACE_MARKERS
        ):
            return "volume_not_workspace_scoped"
        return None
    if kind not in {"worktree", "runtime", "download_cache", "job_artifact"}:
        return "unsupported_resource_kind"
    path = Path(locator)
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return "path_unresolvable"
    if resolved in {RUNTIME, DEPLOY, HOME, Path("/")}:
        return "managed_root"
    if not (inside(resolved, RUNTIME) or inside(resolved, DEPLOY)):
        return "path_escape"
    hosts_root = (DEPLOY / "hosts").resolve(strict=False)
    if resolved == hosts_root or inside(resolved, hosts_root):
        return "hosted_site"
    for name in hosted_site_names():
        if resolved.name == name or inside(resolved, (DEPLOY / name).resolve(strict=False)):
            return "hosted_site"
    if path.is_symlink():
        return "symlink"
    return None


def remove_path(path):
    # Remove a tree, escalating once, and verify it is actually gone.
    #
    # Containers run as root, so ``.pnpm-store`` subtrees are root-owned and an
    # unprivileged ``rmtree`` fails part way through.  Reporting that as success
    # would both corrupt the byte accounting and hide a real failure, so absence
    # is verified before any success is claimed.
    elevated = False
    failure = None
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(str(path))
    except OSError as exc:
        failure = exc
    if path.exists() or path.is_symlink():
        elevated = True
        run(["sudo", "-n", "rm", "-rf", "--", str(path)], 120)
    absent = not (path.exists() or path.is_symlink())
    return absent, elevated, failure


def reclaim_action():
    run_id = str(REQUEST.get("run_id") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        return {"stage": "final", "ok": False, "reason": "invalid_run_id"}
    trigger = str(REQUEST.get("trigger") or "manual")
    candidates = REQUEST.get("candidates")
    if not isinstance(candidates, list):
        return {"stage": "final", "ok": False, "reason": "invalid_candidates"}
    try:
        DELETION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        manifest = DELETION_DIR / (run_id + ".jsonl")
        manifest_append(manifest, {
            "schema": 1, "run_id": run_id, "phase": "run_start",
            "trigger": trigger, "candidates": len(candidates),
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
    except OSError:
        # Without a durable record we do not delete anything at all.
        return {"stage": "final", "ok": False, "reason": "manifest_unavailable"}
    before = shutil.disk_usage("/")
    outcomes = []
    budget_exhausted = False
    stopped = set()
    for candidate in candidates:
        if time.monotonic() >= DEADLINE:
            budget_exhausted = True
            break
        if not isinstance(candidate, dict):
            continue
        seq = candidate.get("seq")
        kind = str(candidate.get("kind") or "")
        locator = str(candidate.get("locator") or "")
        planned_bytes = candidate.get("bytes")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        refusal = refuse_target(kind, locator)
        if refusal is not None:
            outcomes.append({
                "seq": seq, "locator": locator, "status": "skipped",
                "reason": refusal, "bytes": planned_bytes,
                "elevated": False, "verified_absent": False,
            })
            manifest_append(manifest, {
                "schema": 1, "run_id": run_id, "seq": seq, "phase": "outcome",
                "path": locator, "status": "skipped", "reason": refusal,
                "elevated": False, "verified_absent": False, "bytes": None,
                "at": now,
            })
            continue
        if kind == "volume":
            expected = None
        else:
            path = Path(locator)
            if not (path.exists() or path.is_symlink()):
                outcomes.append({
                    "seq": seq, "locator": locator, "status": "already_absent",
                    "reason": "already_absent", "bytes": 0,
                    "elevated": False, "verified_absent": True,
                })
                manifest_append(manifest, {
                    "schema": 1, "run_id": run_id, "seq": seq,
                    "phase": "outcome", "path": locator,
                    "status": "already_absent", "reason": "already_absent",
                    "elevated": False, "verified_absent": True, "bytes": 0,
                    "at": now,
                })
                continue
            expected = candidate.get("expected_mtime")
            if expected is not None:
                observed = entry_mtime(path)
                if observed is None or abs(float(observed) - float(expected)) > 1.0:
                    reason = "candidate_modified_since_plan"
                    outcomes.append({
                        "seq": seq, "locator": locator, "status": "skipped",
                        "reason": reason, "bytes": planned_bytes,
                        "elevated": False, "verified_absent": False,
                    })
                    manifest_append(manifest, {
                        "schema": 1, "run_id": run_id, "seq": seq,
                        "phase": "outcome", "path": locator,
                        "status": "skipped", "reason": reason,
                        "elevated": False, "verified_absent": False,
                        "bytes": None, "at": now,
                    })
                    continue
        manifest_append(manifest, {
            "schema": 1, "run_id": run_id, "seq": seq, "phase": "intent",
            "path": locator, "kind": kind, "bytes": planned_bytes,
            "class": candidate.get("class"), "tier": candidate.get("tier"),
            "reason": candidate.get("reason"), "trigger": trigger, "at": now,
        })
        if kind == "volume":
            for container in candidate.get("stop_containers") or ():
                if isinstance(container, str) and container and container not in stopped:
                    run(["docker", "stop", "-t", "5", container], 30)
                    stopped.add(container)
            code, _out, err = run(["docker", "volume", "rm", locator], 60)
            if code == 0:
                status, reason, absent = "removed", "removed", True
            elif code == 124:
                status, reason, absent = "timed_out", "cleanup_timed_out", False
            elif "no such volume" in str(err).lower():
                status, reason, absent = "already_absent", "already_absent", True
            else:
                status, reason, absent = "failed", "cleanup_failed", False
            elevated = False
        else:
            for container in candidate.get("stop_containers") or ():
                if isinstance(container, str) and container and container not in stopped:
                    run(["docker", "stop", "-t", "5", container], 30)
                    run(["docker", "rm", "-f", container], 30)
                    stopped.add(container)
            absent, elevated, failure = remove_path(Path(locator))
            if absent:
                status, reason = "removed", "removed"
            elif failure is not None and isinstance(failure, PermissionError):
                status, reason = "failed", "partial_removal_detected"
            else:
                status, reason = "failed", "partial_removal_detected"
            if kind == "download_cache" and absent:
                try:
                    Path(locator).mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
        outcomes.append({
            "seq": seq, "locator": locator, "status": status, "reason": reason,
            "bytes": planned_bytes if status == "removed" else 0,
            "elevated": elevated, "verified_absent": bool(absent),
        })
        manifest_append(manifest, {
            "schema": 1, "run_id": run_id, "seq": seq, "phase": "outcome",
            "path": locator, "status": status, "reason": reason,
            "elevated": elevated, "verified_absent": bool(absent),
            "bytes": planned_bytes if status == "removed" else 0,
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
    removed_paths = {
        item["locator"] for item in outcomes
        if item["status"] in {"removed", "already_absent"}
    }
    workspace_ids = REQUEST.get("workspace_ids")
    reconciled = reconcile_after_removal(
        removed_paths, workspace_ids if isinstance(workspace_ids, dict) else {},
    )
    after = shutil.disk_usage("/")
    return {
        "stage": "final",
        "ok": True,
        "run_id": run_id,
        "manifest_path": str(manifest),
        "outcomes": outcomes,
        "reconciled": reconciled,
        "budget_exhausted": budget_exhausted,
        "capacity_before": {
            "total_bytes": int(before.total), "used_bytes": int(before.used),
            "available_bytes": int(before.free),
            "reserved_bytes": max(
                int(before.total) - int(before.used) - int(before.free), 0,
            ),
        },
        "capacity_after": {
            "total_bytes": int(after.total), "used_bytes": int(after.used),
            "available_bytes": int(after.free),
            "reserved_bytes": max(
                int(after.total) - int(after.used) - int(after.free), 0,
            ),
        },
    }


def reconcile_after_removal(removed_paths, workspace_ids):
    # Drop records whose storage is gone, and name what could not be dropped.
    #
    # The index and the disk disagreed in both directions, so removal is the only
    # moment either side can be corrected without guessing.  Registry records go
    # through the typed repository; index records go through the sanctioned
    # workspace lifecycle command, which a host running an older runtime may not
    # have — in which case the count is reported as pending rather than implied
    # to be clean.
    result = {
        "registry_removed": 0, "index_removed": 0, "index_pending": 0,
        "leases_removed": 0, "status": "complete",
    }
    names = {Path(item).name for item in removed_paths if item}
    if not names:
        return result
    try:
        from sandbox.project_registry import JsonRegistryRepository

        repository = JsonRegistryRepository(RUNTIME / "registry.json")
        for root, record in list(repository.read_only_all().items()):
            key = root if isinstance(root, str) else getattr(record, "root", "")
            if not isinstance(key, str) or not key:
                continue
            if Path(key).name in names and not Path(key).exists():
                if repository.remove(key):
                    result["registry_removed"] += 1
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError):
        result["status"] = "partial"
        result["reason"] = "registry_unavailable"
    for name in sorted(names):
        workspace_id = (workspace_ids or {}).get(name)
        if not isinstance(workspace_id, str) or not workspace_id:
            continue
        code, _out, _err = run(
            [str(SB), "workspace", "destroy", "--workspace-id", workspace_id,
             "--confirm", "--json"], 20,
        )
        if code == 0:
            result["index_removed"] += 1
        else:
            result["index_pending"] += 1
    if result["index_pending"]:
        result["status"] = "partial"
        result.setdefault("reason", "index_reconciliation_unavailable")
    for name in sorted(names):
        if not LEASE_NAME.fullmatch(name):
            continue
        try:
            (LEASE_DIR / (name + ".json")).unlink()
            result["leases_removed"] += 1
        except OSError:
            pass
    return result


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
    # Capacity costs microseconds and is the one answer a full disk always
    # needs.  Publish it before any bounded work so a probe that is killed
    # later still reports it instead of reporting nothing at all.
    global ENVELOPE
    ENVELOPE = {
        "stage": "envelope",
        "identity": identity,
        "capacity": capacity,
        "capacity_scope_id": capacity_scope_id,
        "resources": [],
        "category_outcomes": [{
            "category": "remote_probe", "status": "partial",
            "reason": "probe_incomplete_capacity_only",
        }],
        "drift": None,
        "deep_attribution": None,
    }
    emit(ENVELOPE)
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
    if FAST:
        inventory = {
            "containers": [], "volumes": [], "networks": [], "images": [],
            "build_cache": [],
        }
        outcomes = [
            {"category": name, "status": "not_measured",
             "reason": "fast_mode_engine_inventory_skipped"}
            for name in (
                "docker_containers", "docker_volumes", "docker_networks",
                "docker_images", "docker_build_cache",
            )
        ]
    else:
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
    if not targeted and focus is None:
        # In deep mode these are answered from the directory index, so the
        # engine and host breakdown costs nothing extra and one command
        # reports the whole host.
        PHASE = "docker_storage"
        storage_resources, storage_outcomes = docker_storage_resources(
            thorough or deep_requested,
        )
        resources.extend(storage_resources)
        outcomes.extend(storage_outcomes)
        PHASE = "host_filesystem"
        host_resources, host_outcomes = host_capacity_resources(
            thorough or deep_requested,
        )
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
            owner_details = workspace_owner_details(
                workspace_projection, "compose_project", project,
            )
            owner_kind = owner_details["owner_kind"]
            owner_id = owner_details["owner_id"]
            owner_evidence = tuple(owner_details["evidence"])
            owner_protected = bool(owner_details["protected"])
            owner_lifecycle = owner_details["lifecycle"]
            active_references = tuple(owner_details["active_references"].items())
            owner_active = bool(owner_details["active"])
            classification = (
                "active" if active or owner_active else
                "retained" if project in active_projects else
                "retained" if owner_protected or project in protected_projects else
                # A stopped job is not sufficient evidence that its network
                # is stale; require an explicit lifecycle release signal.
                "unverified"
            )
            evidence = owner_evidence
            if not active and not owner_active \
                    and classification == "unverified":
                evidence += ("network_liveness_unverified",)
            references = ("connected_container",) if active else (
                ("workspace_active_reference",)
                if owner_active else (
                "live_compose_project",) if project in active_projects else (
                "instance_or_job_registry",) if owner_protected or project in protected_projects else ())
            refs_map = dict(active_references)
            if active and (refs_map.get("containers") is None or refs_map.get("containers", 0) < 1):
                refs_map["containers"] = 1
            active_references = tuple(sorted(refs_map.items()))
            references_unknown = any(
                count is None for _name, count in active_references
            )
            if references_unknown and not active and not owner_active:
                network_lifecycle = "indeterminate"
            elif owner_lifecycle in {"destroyed", "destroying"} and not active and not owner_active:
                network_lifecycle = "orphaned"
            elif active or owner_active:
                network_lifecycle = "active"
            elif owner_lifecycle in {"ready", "resetting", "provisioning"}:
                network_lifecycle = "idle"
            else:
                network_lifecycle = "indeterminate"
            allocation_state = "allocated" if owner_kind == "workspace" and owner_id else "unknown"
            last_observed = owner_details["observed_at"]
        else:
            labels = network.get("Labels")
            owner_kind = "foreign" if isinstance(labels, dict) and labels.get(
                "com.docker.compose.project"
            ) else "unmanaged"
            owner_id = None
            classification = "active" if active else "unmanaged"
            evidence = ("ownership_unverified",)
            references = ("connected_container",) if active else ()
            active_references = (("containers", 1),) if active else (("containers", 0),)
            network_lifecycle = "active" if active else "indeterminate"
            allocation_state = "unknown"
            last_observed = None
        resources.append(observation(
            "network", network_id, network_name,
            owner_kind, owner_id, classification,
            "measured", 0, 0, references, evidence,
            capacity_accounted=False,
            lifecycle=network_lifecycle,
            active_references=active_references,
            allocation_state=allocation_state,
            cleanup_eligible=False,
            last_observed=last_observed,
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
                state, measured_size, error = size(
                    path, (thorough or is_cache) and not FAST,
                )
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
    reclaim = None
    if not targeted and REQUEST.get("reclaim") is not False:
        PHASE = "reclaim_inventory"
        engine_complete = not FAST and all(
            item.get("status") == "complete"
            for item in outcomes
            if isinstance(item, dict)
            and item.get("category") == "docker_containers"
        )
        try:
            reclaim = reclaim_inventory(
                inventory, protected_paths, workspace_projection,
                engine_complete,
            )
        except Exception:
            reclaim = {
                "deployment_root": str(DEPLOY), "runtime_root": str(RUNTIME),
                "entries": [], "volumes": [], "scratch": [], "leases": {},
                "hosted_sites": [], "index_names": [], "status": "unavailable",
                "reason": "reclaim_inventory_failed", "truncated": False,
                "unmeasured_count": 0,
            }
        outcomes.append({
            "category": "reclaim_inventory",
            "status": reclaim.get("status", "unavailable"),
            "reason": reclaim.get("reason"),
        })
    PHASE = "serialize"
    return {
        "identity": identity,
        "capacity": capacity,
        "capacity_scope_id": capacity_scope_id,
        "resources": resources,
        "category_outcomes": outcomes,
        "drift": None,
        "deep_attribution": deep,
        "reclaim": reclaim,
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

ACTIONS = {
    "remove": remove,
    "reclaim": reclaim_action,
    "lease": lease_action,
}

try:
    handler = ACTIONS.get(REQUEST.get("action"))
    output = handler() if handler is not None else scan()
    if handler is None:
        output["stage"] = "final"
    emit(output)
except Exception as exc:
    failure = dict(ENVELOPE or {})
    failure.update({
        "stage": "error",
        "error": "resource probe failed",
        "error_phase": PHASE,
        "error_type": type(exc).__name__,
    })
    failure["category_outcomes"] = [{
        "category": "remote_probe", "status": "unavailable",
        "reason": "probe_failed_in_" + str(PHASE),
    }]
    emit(failure)
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


_STAGE_RANK = {"envelope": 0, "error": 1, "final": 2}


def _salvage_payload(stdout: str) -> dict | None:
    """Keep the richest complete probe record the transport delivered.

    The probe publishes a capacity envelope before it starts bounded work and
    the full record last.  A probe that is killed mid-write therefore still
    yields capacity, so a full disk never degrades to "unmeasurable".
    """
    best = None
    best_rank = -1
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, dict) or not candidate.get("identity"):
            continue
        rank = _STAGE_RANK.get(str(candidate.get("stage") or ""), 0)
        if rank >= best_rank:
            best, best_rank = candidate, rank
    return best


def _observation(value: dict) -> ResourceObservation:
    owner = value.get("owner") or {}
    allocation = value.get("allocation") or {}
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
        lifecycle=value.get("lifecycle"),
        active_references=value.get("active_references") or (),
        allocation_state=allocation.get("state"),
        allocation_pool=allocation.get("pool"),
        cleanup_eligible=bool(value.get("cleanup_eligible", False)),
        last_observed=value.get("last_observed"),
    )


class RemoteResourceAdapter:
    """Named-remote provider using the authenticated control-plane service."""

    def __init__(
        self,
        remote_name: str,
        *,
        remote_lookup: Callable | None = None,
        service_request: Callable | None = None,
        clock=utc_now,
    ) -> None:
        self.remote_name = remote_name
        self._remote_lookup = remote_lookup
        self._service_request = service_request
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

    def authoritative_target(self, *, budget_seconds: float = 10) -> StorageTarget:
        """Resolve the host identity used by persisted remote cleanup plans.

        ``target()`` must remain a cheap, side-effect-free fallback for
        transport setup.  A cleanup plan, however, is bound to the identity
        emitted by the host probe (which includes the host's device and
        Sandbox home).  Refresh that identity through the cache-only probe
        when a new client process has not observed the host yet.
        """
        if self._target is not None:
            return self._target
        from .service import ResourceError

        try:
            snapshot = self.observe(
                thorough=False,
                budget_seconds=max(float(budget_seconds), 1.0),
                progress=None,
                deep=False,
                directory_cache="cache_only",
            )
        except (
            ResourceError, OSError, RuntimeError, KeyError, TypeError, ValueError,
        ) as exc:
            raise ResourceError(
                "remote target identity could not be measured",
                "remote_target_unavailable",
                retryable=True,
            ) from exc
        if snapshot.capacity is None:
            raise ResourceError(
                "remote target identity could not be measured",
                "remote_target_unavailable",
                retryable=True,
            )
        return snapshot.target

    def _request(self, entry: dict, request: dict, timeout: float) -> ProcessResult:
        """Submit a typed service request; never open SSH in production.

        ``service_request`` is an injected HTTP transport seam for tests and
        downstream adapters. It receives a protocol marker, never executable source.
        """
        if self._service_request is not None:
            execute = self._service_request
            try:
                result = execute(
                    entry, "POST /resources",
                    input_data=json.dumps(request, separators=(",", ":")),
                    timeout=max(int(timeout), 1),
                )
            except subprocess.TimeoutExpired as exc:
                return ProcessResult(("control-http",), 124, "", str(exc))
            return ProcessResult(
                tuple(getattr(result, "args", getattr(result, "argv", ("control-http",)))),
                int(result.returncode), str(result.stdout or ""), str(result.stderr or ""),
            )
        from sandbox.core._remote import remote_resource_request
        try:
            envelope = remote_resource_request(entry, request, timeout=max(int(timeout), 1))
        except RuntimeError as exc:
            return ProcessResult(("control-http",), 1, "", str(exc))
        result = envelope.get("result")
        if not isinstance(result, dict):
            return ProcessResult(("control-http",), 1, "", "invalid resource response")
        return ProcessResult(("control-http",), 0,
                             json.dumps(result, separators=(",", ":")), "")

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
        cancelled=False, directory_cache: str | None = None,
    ) -> ProviderSnapshot:
        entry = self._entry()
        if self._cancelled(cancelled):
            return ProviderSnapshot(
                self.target(), None, (),
                ({"category": "remote_probe", "status": "cancelled"},),
            )
        if progress:
            progress("remote_probe")
        response = self._request(entry, {
            "action": "observe",
            "thorough": bool(thorough),
            "budget_seconds": float(budget_seconds),
            "managed_host": bool(entry.get("provisioned")),
            "remote_name": self.remote_name,
            "focus": focus,
            "deep": bool(deep),
            "cancelled": self._cancelled(cancelled),
            "directory_cache": directory_cache or "auto",
        }, budget_seconds + 5)
        payload = _salvage_payload(response.stdout)
        if payload is None:
            status = (
                "timed_out" if response.returncode == 124 else "unavailable"
            )
            return ProviderSnapshot(
                self.target(), None, (),
                ({"category": "remote_probe", "status": status},),
            )
        identity = payload["identity"]
        resources = tuple(
            _observation(item) for item in payload.get("resources") or ()
        )
        target = StorageTarget("remote", self.remote_name, identity)
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
            payload.get("reclaim"),
        )

    # -- reclamation ------------------------------------------------------

    def reclaim(self, candidates, *, run_id: str, trigger: str = "manual",
                workspace_ids: dict | None = None,
                budget_seconds: float = 900) -> dict:
        """Execute one reviewed candidate set in a single bounded session.

        One request per candidate would mean hundreds of transport calls and
        hundreds of chances to lose the connection mid-run, so the reviewed set
        travels together and the host-side probe re-asserts every protection.
        """
        response = self._request(self._entry(), {
            "action": "reclaim",
            "run_id": run_id,
            "trigger": trigger,
            "candidates": list(candidates),
            "workspace_ids": dict(workspace_ids or {}),
            "budget_seconds": float(budget_seconds),
        }, budget_seconds + 10)
        return self._decode(response, "reclaim")

    def lease(self, op: str, *, name: str | None = None,
              expires_at: str | None = None,
              active_references: dict | None = None) -> dict:
        response = self._request(self._entry(), {
            "action": "lease",
            "op": op,
            "name": name,
            "expires_at": expires_at,
            "active_references": dict(active_references or {}),
            "budget_seconds": 20.0,
        }, 25)
        return self._decode(response, "lease")

    def release_network(self, lifecycle: NetworkLifecycle | ResourceObservation | dict) -> dict:
        """Return a diagnostic release decision without opening SSH."""
        if isinstance(lifecycle, ResourceObservation):
            lifecycle = NetworkLifecycle.from_observation(lifecycle)
        elif isinstance(lifecycle, dict):
            lifecycle = NetworkLifecycle.from_dict(lifecycle)
        if not isinstance(lifecycle, NetworkLifecycle):
            raise TypeError("network lifecycle evidence is required")
        return lifecycle.release_decision(enabled=False)

    @staticmethod
    def _decode(response: ProcessResult, action: str) -> dict:
        if response.returncode == 124:
            return {"ok": False, "reason": f"{action}_timed_out"}
        for line in reversed((response.stdout or "").splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return payload
        return {"ok": False, "reason": f"{action}_response_invalid"}

    def revalidate(self, candidate: CleanupCandidate) -> ResourceObservation | None:
        entry = self._entry()
        response = self._request(entry, {
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
            payload = _salvage_payload(response.stdout) or {}
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
        response = self._request(self._entry(), {
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


class LocalProbeAdapter:
    """Run the shipped probe program against the local host.

    The probe is the single implementation of host-side reclaim evidence and
    mutation.  Executing the same source locally keeps local and remote targets
    on one code path instead of letting a second classifier drift.
    """

    def __init__(self, *, python: str | None = None, home: str | None = None,
                 runner: Callable | None = None, clock=utc_now) -> None:
        import sys

        self.python = python or sys.executable or "python3"
        self.home = home
        self._runner = runner
        self.clock = clock

    def _run(self, request: dict, timeout: float) -> ProcessResult:
        if self._runner is not None:
            return self._runner(request, timeout)
        import os
        import sys
        from pathlib import Path as _Path

        environment = dict(os.environ)
        root = str(_Path(__file__).resolve().parents[2])
        environment["PYTHONPATH"] = (
            root + os.pathsep + environment["PYTHONPATH"]
            if environment.get("PYTHONPATH") else root
        )
        if self.home:
            environment["SANDBOX_HOME"] = str(self.home)
        try:
            completed = subprocess.run(
                [self.python, "-"], input=_program(request), text=True,
                capture_output=True, timeout=max(int(timeout), 1),
                env=environment, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ProcessResult(
                (self.python, "-"), 124,
                exc.stdout if isinstance(exc.stdout, str) else "",
                exc.stderr if isinstance(exc.stderr, str) else "",
            )
        del sys
        return ProcessResult(
            (self.python, "-"), int(completed.returncode),
            completed.stdout or "", completed.stderr or "",
        )

    def observe_reclaim(self, *, budget_seconds: float = 30,
                        directory_cache: str = "auto") -> dict:
        response = self._run({
            "action": "observe",
            "thorough": False,
            "budget_seconds": float(budget_seconds),
            "managed_host": True,
            "remote_name": None,
            "focus": None,
            "deep": True,
            "directory_cache": directory_cache,
        }, budget_seconds + 5)
        payload = _salvage_payload(response.stdout) or {}
        return payload

    def reclaim(self, candidates, *, run_id: str, trigger: str = "manual",
                workspace_ids: dict | None = None,
                budget_seconds: float = 900) -> dict:
        response = self._run({
            "action": "reclaim",
            "run_id": run_id,
            "trigger": trigger,
            "candidates": list(candidates),
            "workspace_ids": dict(workspace_ids or {}),
            "budget_seconds": float(budget_seconds),
        }, budget_seconds + 10)
        return RemoteResourceAdapter._decode(response, "reclaim")

    def lease(self, op: str, *, name: str | None = None,
              expires_at: str | None = None,
              active_references: dict | None = None) -> dict:
        response = self._run({
            "action": "lease",
            "op": op,
            "name": name,
            "expires_at": expires_at,
            "active_references": dict(active_references or {}),
            "budget_seconds": 20.0,
        }, 25)
        return RemoteResourceAdapter._decode(response, "lease")

    def release_network(self, lifecycle: NetworkLifecycle | ResourceObservation | dict) -> dict:
        if isinstance(lifecycle, ResourceObservation):
            lifecycle = NetworkLifecycle.from_observation(lifecycle)
        elif isinstance(lifecycle, dict):
            lifecycle = NetworkLifecycle.from_dict(lifecycle)
        if not isinstance(lifecycle, NetworkLifecycle):
            raise TypeError("network lifecycle evidence is required")
        return lifecycle.release_decision(enabled=False)
