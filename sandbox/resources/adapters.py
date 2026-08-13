from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import time
import re
from typing import Any, Callable, Mapping, Protocol

from sandbox.services.process import BoundedProcessRunner, ProcessResult

from .models import (
    CleanupCandidate,
    CleanupItemOutcome,
    ResourceRequest,
    ResourceObservation,
    StorageTarget,
    utc_now,
)
from .attribution import (
    CapabilityObservation,
    CoverageObservation,
    DeepAttribution,
    DeepAttributionCollector,
    parse_df_output,
    reconcile_attribution,
)

_BUILD_CACHE_ID = re.compile(r"^[a-z0-9]{12,128}$")
_BYTE_SIZE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?i?b)?$", re.I)


def _parse_byte_size(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    match = _BYTE_SIZE.fullmatch(value.strip())
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


@dataclass(frozen=True)
class ProviderSnapshot:
    target: StorageTarget
    capacity: dict | None
    resources: tuple[ResourceObservation, ...]
    category_outcomes: tuple[dict, ...] = ()
    drift: dict | None = None
    deep_attribution: DeepAttribution | None = None
    capacity_scope_id: str | None = None


@dataclass(frozen=True)
class _WorkspaceOwner:
    """One fail-closed result from the typed workspace ownership projection."""

    owner_kind: str
    owner_id: str | None
    evidence: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    protected: bool = False
    active: bool = False


class _WorkspaceOwnership:
    """Normalize an injected workspace projection without owning its storage.

    Resource providers receive the projection from the workspace service.  The
    adapter deliberately has no repository/path access: it only indexes the
    serialisable records and exact resource bindings in that projection.
    """

    _BINDING_TYPES = frozenset({
        "compose_project", "runtime_instance", "runtime", "instance",
    })
    _INVALID_STATUSES = frozenset({
        "invalid", "incomplete", "unresolved", "conflict", "indeterminate",
        "destroyed", "tombstoned",
    })
    _LIFECYCLES = frozenset({
        "provisioning", "ready", "resetting", "destroying", "destroyed",
        "indeterminate",
    })

    def __init__(self, payload: Any, *, configured: bool) -> None:
        self.configured = configured
        self.available = False
        self.incomplete = False
        self.bindings: dict[tuple[str, str], set[tuple[str, str, str, bool]]] = {}
        self.reason = "workspace_index_unavailable"
        if not configured:
            self.reason = "workspace_projection_not_configured"
            return
        if callable(payload):
            try:
                payload = payload()
            except Exception:
                payload = None
        if hasattr(payload, "ownership_projection"):
            try:
                payload = payload.ownership_projection()
            except Exception:
                payload = None
        if not isinstance(payload, Mapping):
            return
        records = payload.get("records", payload.get("workspaces"))
        if not isinstance(records, (list, tuple)):
            return
        self.available = True
        if not records:
            # An empty projection is not proof that a live resource is
            # unowned; callers must surface an incomplete index instead of a
            # false empty-success inventory.
            self.incomplete = True
        counts = payload.get("counts")
        projection_generation = payload.get(
            "index_generation", payload.get("generation"))
        if (isinstance(projection_generation, bool) or
                not isinstance(projection_generation, int)):
            self.incomplete = True
        if isinstance(counts, Mapping) and any(
            isinstance(counts.get(key), int) and counts.get(key) > 0
            for key in ("unresolved", "conflict", "incomplete")
        ):
            self.incomplete = True
        for record in records:
            if not isinstance(record, Mapping):
                self.incomplete = True
                continue
            if record.get("complete") is False:
                self.incomplete = True
                continue
            record_generation = record.get("index_generation")
            if (projection_generation is not None and
                    record_generation != projection_generation):
                self.incomplete = True
                continue
            workspace_id = record.get("workspace_id")
            if not isinstance(workspace_id, str) or not workspace_id:
                self.incomplete = True
                continue
            lifecycle_value = record.get("lifecycle")
            status_value = record.get("status")
            observed_at = record.get("observed_at")
            owner_class = record.get("owner_kind")
            if (owner_class != "workspace" or
                    not isinstance(lifecycle_value, str) or
                    lifecycle_value.lower() not in self._LIFECYCLES or
                    not isinstance(status_value, str) or
                    not isinstance(observed_at, str) or not observed_at):
                self.incomplete = True
                continue
            lifecycle = lifecycle_value.lower()
            status = status_value.lower()
            if lifecycle in self._INVALID_STATUSES or status in self._INVALID_STATUSES:
                self.incomplete = True
                continue
            bindings = record.get("bindings")
            if not isinstance(bindings, (list, tuple)):
                continue
            active_references = record.get("active_references")
            reference_active = False
            if isinstance(active_references, Mapping):
                reference_active = any(
                    isinstance(value, int) and not isinstance(value, bool) and value > 0
                    or value is True
                    for value in active_references.values()
                )
            for binding in bindings:
                if not isinstance(binding, Mapping):
                    self.incomplete = True
                    continue
                resource_type = binding.get("resource_type", binding.get("type"))
                resource_id = binding.get("resource_id", binding.get("id"))
                if (
                    not isinstance(resource_type, str)
                    or resource_type not in self._BINDING_TYPES
                    or not isinstance(resource_id, str)
                    or not resource_id
                ):
                    self.incomplete = True
                    continue
                binding_status = str(binding.get("status") or "owned").lower()
                if binding_status in self._INVALID_STATUSES:
                    self.incomplete = True
                    continue
                self.bindings.setdefault((resource_type, resource_id), set()).add(
                    (workspace_id, lifecycle, binding_status, reference_active),
                )
        self.reason = "workspace_index_incomplete" if self.incomplete else "workspace_projection"

    def resolve(self, resource_type: str, resource_id: str, *, legacy_owner: str | None = None,
                legacy_protected: bool = False) -> _WorkspaceOwner:
        """Resolve only an exact, unique typed binding.

        A missing/ambiguous binding never falls back to a path or Compose name
        once a projection is configured.  This keeps resource cleanup fail
        closed while retaining old behaviour for direct legacy adapter users
        that did not inject a projection.
        """
        if not self.configured:
            if legacy_owner:
                return _WorkspaceOwner(
                    "project", legacy_owner,
                    ("compose_project_label",),
                    ("instance_registry",) if legacy_protected else (),
                    legacy_protected,
                )
            return _WorkspaceOwner("unmanaged", None, ("ownership_unverified",))
        if not self.available:
            return _WorkspaceOwner("unknown", None, ("workspace_index_unavailable",))
        matches = self.bindings.get((resource_type, resource_id), set())
        if len(matches) == 1:
            workspace_id, _lifecycle, binding_status, active = next(iter(matches))
            if binding_status not in {"owned", "active", "retained", "ready"}:
                return _WorkspaceOwner(
                    "unknown", None,
                    ("workspace_binding_unverified", self.reason),
                )
            return _WorkspaceOwner(
                "workspace", workspace_id,
                ("workspace_binding", resource_type),
                ("workspace_index",),
                True,
                active,
            )
        if len(matches) > 1:
            return _WorkspaceOwner(
                "unknown", None,
                ("workspace_alias_collision", resource_type),
            )
        return _WorkspaceOwner("unknown", None, (self.reason, "workspace_binding_missing"))


def _is_unknown_workspace_owner(owner: _WorkspaceOwner) -> bool:
    return owner.owner_kind == "unknown"


class ResourceAdapter(Protocol):
    def target(self) -> StorageTarget: ...

    def observe(
        self, *, thorough: bool, budget_seconds: float,
        progress=None, focus: str | None = None, deep: bool = False,
        cancelled=False,
    ) -> ProviderSnapshot: ...

    def revalidate(self, candidate: CleanupCandidate) -> ResourceObservation | None: ...

    def remove(self, candidate: CleanupCandidate) -> CleanupItemOutcome: ...


def _resource_id(kind: str, locator: str) -> str:
    return f"{kind}-{hashlib.sha256(locator.encode()).hexdigest()[:20]}"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


class LocalResourceAdapter:
    """Bounded host provider with conservative ownership classification."""

    def __init__(
        self,
        sandbox_home: Path,
        *,
        runner=None,
        registry_records: Callable[[], object] | None = None,
        job_resource_records: Callable[[], object] | None = None,
        workspace_projection: Callable[[], object] | object | None = None,
        deep_collector_factory=DeepAttributionCollector,
        clock=utc_now,
        host_root: Path = Path("/"),
    ) -> None:
        self.sandbox_home = Path(sandbox_home).expanduser().resolve(strict=False)
        self.runtime_root = self.sandbox_home / "runtime"
        self.deploy_root = self.sandbox_home / "deploy-src"
        self.runner = runner or BoundedProcessRunner(max_output=4_000_000)
        self.registry_records = registry_records or (lambda: {})
        self.job_resource_records = job_resource_records or (
            lambda: {"jobs": [], "artifacts": []}
        )
        self.workspace_projection = workspace_projection
        self.deep_collector_factory = deep_collector_factory
        self.clock = clock
        self.host_root = Path(host_root)

    def _workspace_ownership(self) -> _WorkspaceOwnership:
        """Read the injected typed projection once per observation."""
        return _WorkspaceOwnership(
            self.workspace_projection,
            configured=self.workspace_projection is not None,
        )

    def target(self) -> StorageTarget:
        seed = f"{platform.node()}:{os.stat(self.host_root).st_dev}:{self.sandbox_home}"
        identity = hashlib.sha256(seed.encode()).hexdigest()[:24]
        return StorageTarget("local", "local", identity)

    def _capacity(self) -> dict:
        usage = shutil.disk_usage(self.host_root)
        reserved = max(int(usage.total) - int(usage.used) - int(usage.free), 0)
        return {
            "total_bytes": int(usage.total),
            "used_bytes": int(usage.used),
            "available_bytes": int(usage.free),
            "reserved_bytes": reserved,
            "measured_at": self.clock().astimezone(timezone.utc).isoformat(),
        }

    @staticmethod
    def _managed_root_id(kind: str, path: Path) -> str:
        """Return an opaque stable owner ID without retaining record labels."""
        return _resource_id("managed_root", f"{kind}:{path}")

    def _deep_managed_roots(self) -> tuple[dict[str, str], ...]:
        """Build the collector's typed, internal-only managed-root handoff.

        The collector uses paths solely to choose filesystem boundaries.  Root
        paths and the source record's labels are never part of a public deep
        attribution value, and owner IDs deliberately remain opaque.
        """
        roots: list[dict[str, str]] = []

        try:
            records = self.registry_records()
            values = records.values() if isinstance(records, dict) else records
        except Exception:
            values = ()
        for record in values or ():
            if not isinstance(record, dict):
                continue
            value = record.get("root")
            if not isinstance(value, str) or not value:
                continue
            try:
                path = Path(value).expanduser().resolve(strict=False)
            except (OSError, ValueError):
                continue
            roots.append({
                "path": str(path),
                "kind": "registry_root",
                "owner_id": self._managed_root_id("registry_root", path),
            })

        try:
            jobs = self.job_resource_records()
        except Exception:
            jobs = {"jobs": []}
        records = jobs.get("jobs", ()) if isinstance(jobs, dict) else ()
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("project_root")
            if not isinstance(value, str) or not value:
                continue
            try:
                path = Path(value).expanduser().resolve(strict=False)
            except (OSError, ValueError):
                continue
            roots.append({
                "path": str(path),
                "kind": "job_root",
                "owner_id": self._managed_root_id("job_root", path),
            })

        return tuple(sorted(roots, key=lambda item: (
            item["path"], item["kind"], item["owner_id"],
        )))

    def _deep_capacity_snapshots(self, deadline: float) -> dict[str, dict]:
        """Take a read-only, pre-scan capacity snapshot per mount boundary."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {}
        result = self._run(("df", "-Pk"), min(remaining, 5))
        if result.returncode != 0:
            return {}
        snapshots = {}
        for row in parse_df_output(result.stdout):
            mount = str(row["mount_point"])
            snapshots[mount] = {
                key: int(row[key])
                for key in ("total_bytes", "used_bytes", "available_bytes")
            }
        return snapshots

    @staticmethod
    def _deep_collector_failure(
        capacity: dict,
        *,
        status: str = "unavailable",
        reason: str = "deep_collector_failed",
    ) -> DeepAttribution:
        """Preserve ordinary completed evidence when deep collection stops."""
        return DeepAttribution(
            status="partial",
            filesystems=(),
            findings=(),
            capabilities=(CapabilityObservation(
                category="deep_collection",
                name="local_deep_collector",
                version=None,
                fallback=False,
                privilege="unavailable",
                status=status,
                limitations=(
                    "unexpected_collector_failure"
                    if status == "unavailable" else "overall_budget_exhausted",
                ),
            ),),
            coverage=(CoverageObservation(
                category="deep_collection",
                boundary_id=None,
                status=status,
                duration_ms=0,
                confidence="low",
                privilege_sufficient=False,
                reason=reason,
            ),),
            reconciliation=reconcile_attribution(
                used_bytes=int(capacity.get("used_bytes") or 0),
                directory_allocated_bytes=0,
            ),
        )

    def _run(self, argv, timeout: float):
        command = tuple(str(item) for item in argv)
        if timeout <= 0:
            return ProcessResult(command, 124, "", "overall budget exhausted")
        return self.runner.run(command, timeout=timeout)

    def _du(self, path: Path, timeout: float) -> tuple[str, int | None, str | None]:
        result = self._run(("du", "-sk", str(path)), timeout)
        if result.returncode == 124:
            return "timed_out", None, "measurement timed out"
        if result.returncode != 0:
            return "unavailable", None, "measurement unavailable"
        try:
            return "measured", int(result.stdout.split()[0]) * 1024, None
        except (ValueError, IndexError):
            return "unavailable", None, "measurement unavailable"

    def _age(self, path: Path) -> int | None:
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            return max(int((self.clock() - modified).total_seconds()), 0)
        except (OSError, ValueError, TypeError):
            return None

    def _docker_json(self, argv, timeout: float):
        result = self._run(("docker", *argv), timeout)
        if result.returncode == 124:
            return None, "timed_out"
        if result.returncode != 0:
            return None, "unavailable"
        try:
            return json.loads(result.stdout or "[]"), "complete"
        except json.JSONDecodeError:
            return None, "unavailable"

    def _docker_inventory(self, deadline: float) -> tuple[dict, tuple[dict, ...]]:
        outcomes = []

        def remaining(limit: float) -> float:
            return min(deadline - time.monotonic(), limit)

        container_ids_result = self._run(("docker", "ps", "-aq"), remaining(3))
        containers = []
        if container_ids_result.returncode == 0:
            ids = container_ids_result.stdout.split()
            if ids:
                containers, state = self._docker_json(
                    ("inspect", "--size", *ids), remaining(5),
                )
                outcomes.append({"category": "docker_containers", "status": state})
            else:
                outcomes.append({"category": "docker_containers", "status": "complete"})
        else:
            state = "timed_out" if container_ids_result.returncode == 124 else "unavailable"
            outcomes.append({"category": "docker_containers", "status": state})
        volume_ids_result = self._run(
            ("docker", "volume", "ls", "-q"), remaining(3),
        )
        volumes = []
        if volume_ids_result.returncode == 0:
            names = volume_ids_result.stdout.split()
            if names:
                volumes, state = self._docker_json(
                    ("volume", "inspect", *names), remaining(5),
                )
                outcomes.append({"category": "docker_volumes", "status": state})
            else:
                outcomes.append({"category": "docker_volumes", "status": "complete"})
        else:
            state = "timed_out" if volume_ids_result.returncode == 124 else "unavailable"
            outcomes.append({"category": "docker_volumes", "status": state})
        network_ids_result = self._run(
            ("docker", "network", "ls", "-q"), remaining(3),
        )
        networks = []
        if network_ids_result.returncode == 0:
            ids = network_ids_result.stdout.split()
            if ids:
                networks, state = self._docker_json(
                    ("network", "inspect", *ids), remaining(5),
                )
                outcomes.append({"category": "docker_networks", "status": state})
            else:
                outcomes.append({"category": "docker_networks", "status": "complete"})
        else:
            state = "timed_out" if network_ids_result.returncode == 124 else "unavailable"
            outcomes.append({"category": "docker_networks", "status": state})
        image_ids_result = self._run(
            ("docker", "image", "ls", "-q"), remaining(3),
        )
        images = []
        if image_ids_result.returncode == 0:
            ids = sorted(set(image_ids_result.stdout.split()))
            if ids:
                images, state = self._docker_json(
                    ("image", "inspect", *ids), remaining(5),
                )
                outcomes.append({"category": "docker_images", "status": state})
            else:
                outcomes.append({"category": "docker_images", "status": "complete"})
        else:
            state = "timed_out" if image_ids_result.returncode == 124 else "unavailable"
            outcomes.append({"category": "docker_images", "status": state})
        build_cache_result = self._run(
            ("docker", "buildx", "du", "--format=json"), remaining(12),
        )
        build_cache = []
        if build_cache_result.returncode == 0:
            try:
                build_cache = [
                    json.loads(line)
                    for line in build_cache_result.stdout.splitlines()
                    if line.strip()
                ]
                state = "complete"
            except json.JSONDecodeError:
                build_cache, state = [], "unavailable"
        else:
            state = (
                "timed_out"
                if build_cache_result.returncode == 124
                else "unavailable"
            )
        outcomes.append({"category": "docker_build_cache", "status": state})
        return {
            "containers": containers or [],
            "volumes": volumes or [],
            "networks": networks or [],
            "images": images or [],
            "build_cache": build_cache or [],
        }, tuple(outcomes)

    @staticmethod
    def _compose_owner(labels) -> str | None:
        if not isinstance(labels, dict):
            return None
        project = labels.get("com.docker.compose.project")
        working_dir = labels.get("com.docker.compose.project.working_dir", "")
        if (
            isinstance(project, str)
            and project
            and (
                project.startswith("sandbox-")
                or "/sandbox/" in str(working_dir)
                or str(working_dir).endswith("/sandbox")
            )
        ):
            return project
        return None

    def _volume_size(
        self, name: str, mountpoint: str, timeout: float,
    ) -> tuple[str, int | None, str | None]:
        pid_result = self._run(("pgrep", "-xo", "dockerd"), min(timeout, 2))
        pid = pid_result.stdout.strip() if pid_result.returncode == 0 else ""
        if pid and mountpoint:
            result = self._run((
                "sudo", "-n", "nsenter", "-t", pid, "-m", "--",
                "du", "-sk", mountpoint,
            ), timeout)
            if result.returncode == 0:
                try:
                    return "measured", int(result.stdout.split()[0]) * 1024, None
                except (ValueError, IndexError):
                    pass
            if result.returncode == 124:
                return "timed_out", None, "volume measurement timed out"
        return "unavailable", None, "private volume measurement unavailable"

    def _path_observation(
        self,
        path: Path,
        *,
        kind: str,
        classification: str,
        owner_kind: str,
        owner_id: str | None,
        thorough: bool,
        timeout: float,
        evidence=(),
        references=(),
    ) -> ResourceObservation:
        if thorough:
            size_state, size_bytes, error = self._du(path, timeout)
        else:
            size_state, size_bytes, error = "not_measured", None, None
        eligible = classification in {"disposable_cache", "stale_candidate"}
        return ResourceObservation(
            resource_id=_resource_id(kind, str(path)),
            kind=kind,
            locator=str(path),
            display_name=path.name,
            owner_kind=owner_kind,
            owner_id=owner_id,
            classification=classification if (not eligible or size_state == "measured") else "unverified",
            size_state=size_state,
            size_bytes=size_bytes,
            reclaimable_bytes=(size_bytes or 0) if eligible and size_state == "measured" else 0,
            age_seconds=self._age(path),
            references=tuple(references),
            evidence=tuple(evidence),
            errors=(error,) if error else (),
        )

    def _path_resources(
        self, *, thorough: bool, deadline: float,
        active_sources: set[str], protected_paths: dict[str, tuple[str, ...]],
        workspace_ownership: _WorkspaceOwnership | None = None,
    ) -> tuple[list[ResourceObservation], list[dict]]:
        resources = []
        outcomes = []
        for root, category in (
            (self.deploy_root, "deploy_worktrees"),
            (self.runtime_root, "sandbox_runtime"),
        ):
            if not root.is_dir():
                outcomes.append({"category": category, "status": "complete"})
                continue
            try:
                entries = sorted(root.iterdir(), key=lambda item: item.name)
            except OSError:
                outcomes.append({"category": category, "status": "unavailable"})
                continue
            category_state = "complete"
            for path in entries:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    category_state = "timed_out"
                    break
                if root == self.deploy_root:
                    active = any(
                        source == str(path) or source.startswith(str(path) + os.sep)
                        for source in active_sources
                    )
                    protections = protected_paths.get(
                        str(path.resolve(strict=False)),
                        (),
                    )
                    is_workspace = "-workspace-" in path.name or ".workspace-" in path.name
                    if active:
                        classification, refs = "active", ("live_container_mount",)
                    elif protections:
                        classification, refs = "retained", protections
                    elif path.name == "hosts" or not is_workspace:
                        classification, refs = "retained", ("permanent_or_base_deployment",)
                    else:
                        classification, refs = "stale_candidate", ()
                    workspace_owner = (
                        workspace_ownership.resolve("runtime_instance", path.name)
                        if is_workspace and workspace_ownership is not None else None
                    )
                    owner_state = getattr(workspace_owner, "owner_kind", None)
                    match owner_state:
                        case "workspace":
                            owner_kind, owner_id = workspace_owner.owner_kind, workspace_owner.owner_id
                        case "unknown":
                            owner_kind, owner_id = "unknown", None
                            if classification == "stale_candidate":
                                classification = "unverified"
                            refs = tuple(dict.fromkeys(
                                refs + workspace_owner.evidence,
                            ))
                        case _:
                            owner_kind, owner_id = (
                                ("workspace", path.name) if is_workspace else ("project", path.name)
                            )
                    resources.append(self._path_observation(
                        path, kind="worktree", classification=classification,
                        owner_kind=owner_kind, owner_id=owner_id, thorough=thorough,
                        timeout=min(remaining, 8),
                        evidence=("sandbox_deploy_root",),
                        references=refs,
                    ))
                else:
                    if path.name == "dl-cache":
                        classification = "disposable_cache"
                        evidence = ("sandbox_runtime_root", "download_cache")
                        kind = "download_cache"
                    elif path.name in {"hermes-backups", "backups", "recovery"}:
                        classification = "retained"
                        evidence = ("sandbox_runtime_root", "backup_retention")
                        kind = "backup"
                    elif path.name in {"snapshots", "snapshot"}:
                        classification = "retained"
                        evidence = ("sandbox_runtime_root", "snapshot_retention")
                        kind = "snapshot"
                    elif (
                        path.name in {
                            "staging", "backup-staging", "recovery-staging",
                        }
                        or path.name.startswith("backup-staging-")
                    ):
                        classification = "unverified"
                        evidence = ("sandbox_runtime_root", "retention_unknown")
                        kind = "backup"
                    else:
                        classification = "retained"
                        evidence = ("sandbox_runtime_root", "retention_unknown")
                        kind = "runtime"
                    resources.append(self._path_observation(
                        path, kind=kind,
                        classification=classification,
                        owner_kind="sandbox", owner_id="sandbox",
                        thorough=thorough or path.name == "dl-cache",
                        timeout=min(remaining, 5),
                        evidence=evidence,
                        references=("retention_policy",) if classification == "retained" else (),
                    ))
            outcomes.append({"category": category, "status": category_state})
        return resources, outcomes

    @staticmethod
    def _workspace_owner_for(
        ownership: _WorkspaceOwnership,
        owner: str | None,
        protected_projects: set[str],
    ) -> _WorkspaceOwner:
        if not owner:
            return _WorkspaceOwner("unmanaged", None, ("ownership_unverified",))
        return ownership.resolve(
            "compose_project", owner,
            legacy_owner=owner,
            legacy_protected=owner in protected_projects,
        )

    def _docker_resources(
        self, inventory: dict, *, thorough: bool, deadline: float,
        protected_projects: set[str],
        workspace_ownership: _WorkspaceOwnership,
    ) -> list[ResourceObservation]:
        resources = []
        active_volumes = {
            mount.get("Name")
            for container in inventory.get("containers", ())
            if container.get("State", {}).get("Running")
            for mount in container.get("Mounts", ())
            if mount.get("Type") == "volume" and mount.get("Name")
        }
        for container in inventory.get("containers", ()):
            owner = self._compose_owner(container.get("Config", {}).get("Labels"))
            if not owner:
                continue
            workspace_owner = self._workspace_owner_for(
                workspace_ownership, owner, protected_projects,
            )
            running = bool(container.get("State", {}).get("Running"))
            size = container.get("SizeRw")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                size = None
            locator = str(container.get("Id") or container.get("Name") or "")
            if not locator:
                continue
            resources.append(ResourceObservation(
                resource_id=_resource_id("container", locator),
                kind="container", locator=locator,
                display_name=str(container.get("Name") or locator).lstrip("/"),
                owner_kind=workspace_owner.owner_kind,
                owner_id=workspace_owner.owner_id,
                classification=(
                    "active" if running or workspace_owner.active else
                    "retained" if workspace_owner.protected else
                    "unverified" if _is_unknown_workspace_owner(workspace_owner) else
                    "disposable_cache"
                ),
                size_state="measured" if size is not None else "unavailable",
                size_bytes=size,
                reclaimable_bytes=(
                    size or 0
                    if not running and not _is_unknown_workspace_owner(workspace_owner)
                    and not workspace_owner.protected
                    and size is not None
                    else 0
                ),
                references=(
                    (("running_container",) if running else ())
                    + (("workspace_active_reference",) if workspace_owner.active else ())
                    + workspace_owner.references if running or workspace_owner.active else
                    workspace_owner.references if workspace_owner.protected else ()
                ),
                evidence=tuple(dict.fromkeys(
                    workspace_owner.evidence + ("stopped" if not running else "running",)
                )),
            ))
        for volume in inventory.get("volumes", ()):
            name = volume.get("Name")
            if not isinstance(name, str) or not name:
                continue
            owner = self._compose_owner(volume.get("Labels"))
            workspace_owner = self._workspace_owner_for(
                workspace_ownership, owner, protected_projects,
            )
            if not owner:
                classification, owner_kind, owner_id = "unmanaged", "unmanaged", None
            elif name in active_volumes:
                classification = "active"
                owner_kind, owner_id = workspace_owner.owner_kind, workspace_owner.owner_id
            elif workspace_owner.active:
                classification = "active"
                owner_kind, owner_id = workspace_owner.owner_kind, workspace_owner.owner_id
            elif workspace_owner.protected:
                classification = "retained"
                owner_kind, owner_id = workspace_owner.owner_kind, workspace_owner.owner_id
            elif _is_unknown_workspace_owner(workspace_owner):
                classification = "unverified"
                owner_kind, owner_id = "unknown", None
            else:
                classification = "unverified"
                owner_kind, owner_id = workspace_owner.owner_kind, workspace_owner.owner_id
            size_state, size, error = "not_measured", None, None
            if (
                thorough and owner and name not in active_volumes
                and not workspace_owner.protected
                and not _is_unknown_workspace_owner(workspace_owner)
            ):
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    size_state, size, error = self._volume_size(
                        name, str(volume.get("Mountpoint") or ""),
                        min(remaining, 12),
                    )
                    if size_state == "measured":
                        classification = "stale_candidate"
            resources.append(ResourceObservation(
                resource_id=_resource_id("volume", name),
                kind="volume", locator=name, display_name=name,
                owner_kind=owner_kind, owner_id=owner_id,
                classification=classification,
                size_state=size_state, size_bytes=size,
                reclaimable_bytes=size or 0 if classification == "stale_candidate" else 0,
                references=(
                    (("live_container_mount",) + workspace_owner.references)
                    if name in active_volumes else
                    (("workspace_active_reference",) + workspace_owner.references)
                    if workspace_owner.active else
                    workspace_owner.references if workspace_owner.protected else ()
                ),
                evidence=tuple(dict.fromkeys(
                    workspace_owner.evidence if owner else ("ownership_unverified",)
                )),
                errors=(error,) if error else (),
            ))
        for network in inventory.get("networks", ()):
            network_id = network.get("Id")
            if not isinstance(network_id, str) or not network_id:
                continue
            network_name = str(network.get("Name") or network_id)
            # Docker's predefined bridge/host/none networks are not
            # Sandbox-managed user-defined allocations.  Keep the inventory
            # focused on networks for which lifecycle evidence is meaningful.
            if network_name in {"bridge", "host", "none"}:
                continue
            owner = self._compose_owner(network.get("Labels"))
            active = bool(network.get("Containers"))
            if owner:
                workspace_owner = self._workspace_owner_for(
                    workspace_ownership, owner, protected_projects,
                )
                owner_kind, owner_id = workspace_owner.owner_kind, workspace_owner.owner_id
                classification = (
                    "active" if active or workspace_owner.active else
                    "retained" if workspace_owner.protected else
                    # A stopped job/container is not proof that a network is
                    # stale.  Keep inactive networks unverified until an
                    # explicit lifecycle reference confirms release.
                    "unverified"
                )
                evidence = workspace_owner.evidence
                if not active and classification == "unverified":
                    evidence += ("network_liveness_unverified",)
                references = (
                    (("connected_container",) if active else ())
                    + (("workspace_active_reference",) if workspace_owner.active else ())
                    + workspace_owner.references if active or workspace_owner.active else
                    workspace_owner.references if workspace_owner.protected else ()
                )
            else:
                labels = network.get("Labels")
                owner_kind = "foreign" if isinstance(labels, dict) and labels.get(
                    "com.docker.compose.project"
                ) else "unmanaged"
                owner_id = None
                classification = "active" if active else "unmanaged"
                evidence = ("ownership_unverified",)
                references = ("connected_container",) if active else ()
            resources.append(ResourceObservation(
                resource_id=_resource_id("network", network_id),
                kind="network", locator=network_id,
                display_name=network_name,
                owner_kind=owner_kind, owner_id=owner_id,
                classification=classification,
                size_state="measured", size_bytes=0, reclaimable_bytes=0,
                capacity_accounted=False,
                references=references,
                evidence=evidence,
            ))
        used_images = {
            str(container.get("Image"))
            for container in inventory.get("containers", ())
            if container.get("Image")
        }
        for image in inventory.get("images", ()):
            locator = image.get("Id")
            if not isinstance(locator, str) or not locator:
                continue
            image_owner = self._compose_owner(
                (image.get("Config") or {}).get("Labels"),
            )
            if not image_owner:
                continue
            workspace_owner = self._workspace_owner_for(
                workspace_ownership, image_owner, protected_projects,
            )
            used = locator in used_images
            size = image.get("Size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                size = None
            display = next(iter(image.get("RepoTags") or ()), locator)
            classification = (
                "active" if used or workspace_owner.active else
                "retained" if workspace_owner.protected else
                "unverified" if _is_unknown_workspace_owner(workspace_owner) else
                "disposable_cache"
            )
            resources.append(ResourceObservation(
                resource_id=_resource_id("image", locator),
                kind="image", locator=locator, display_name=str(display),
                owner_kind=workspace_owner.owner_kind,
                owner_id=workspace_owner.owner_id,
                classification=classification,
                size_state="measured" if size is not None else "unavailable",
                size_bytes=size,
                reclaimable_bytes=(
                    size or 0 if classification == "disposable_cache" else 0
                ),
                references=(
                    (("container_image",) if used else ())
                    + (("workspace_active_reference",) if workspace_owner.active else ())
                    + workspace_owner.references if used or workspace_owner.active else
                    workspace_owner.references if workspace_owner.protected else ()
                ),
                evidence=workspace_owner.evidence,
            ))
        for record in inventory.get("build_cache", ()):
            locator = record.get("ID")
            size = _parse_byte_size(record.get("Size"))
            if (
                not isinstance(locator, str)
                or not _BUILD_CACHE_ID.fullmatch(locator)
            ):
                continue
            reclaimable = record.get("Reclaimable") is True
            mutable = record.get("Mutable") is True
            resources.append(ResourceObservation(
                resource_id=_resource_id("build_cache", locator),
                kind="build_cache",
                locator=locator,
                display_name=f"build cache {locator[:12]}",
                owner_kind="unmanaged",
                owner_id=None,
                classification="unverified",
                size_state="measured" if size is not None and size >= 0 else "unavailable",
                size_bytes=size if size is not None and size >= 0 else None,
                reclaimable_bytes=0,
                references=("mutable_build_cache",) if mutable else (),
                evidence=(
                    "buildx_disk_usage",
                    "engine_reports_reclaimable" if reclaimable else "engine_retained",
                    "ownership_unverified",
                ),
            ))
        return resources

    def _ownership_index(self) -> tuple[
        dict[str, tuple[str, ...]], set[str], dict,
    ]:
        protected_paths: dict[str, list[str]] = {}
        protected_projects = set()
        try:
            records = self.registry_records()
            values = records.values() if isinstance(records, dict) else records
            for record in values or ():
                if not isinstance(record, dict):
                    continue
                root = record.get("root")
                if isinstance(root, str) and root:
                    canonical = str(Path(root).resolve(strict=False))
                    protected_paths.setdefault(canonical, []).append(
                        "instance_registry",
                    )
                instance = record.get("instance")
                if isinstance(instance, str) and instance:
                    protected_projects.update((instance, f"sandbox-{instance}"))
        except Exception:
            records = {}
        try:
            jobs = self.job_resource_records()
        except Exception:
            jobs = {"jobs": [], "artifacts": []}
        terminal = {
            "succeeded", "failed", "timed_out", "cancelled", "interrupted",
        }
        for job in jobs.get("jobs", ()) if isinstance(jobs, dict) else ():
            if not isinstance(job, dict):
                continue
            root = job.get("project_root")
            protect = (
                job.get("lifecycle") not in terminal
                or job.get("cleanup_policy") == "retain"
                or job.get("cleanup_state") == "retained"
            )
            if protect and isinstance(root, str) and root:
                canonical = str(Path(root).resolve(strict=False))
                protected_paths.setdefault(canonical, []).append(
                    "retained_job",
                )
                workspace_project = Path(canonical).name
                if workspace_project:
                    protected_projects.update((
                        workspace_project,
                        f"sandbox-{workspace_project}",
                    ))
        return (
            {key: tuple(sorted(set(value))) for key, value in protected_paths.items()},
            protected_projects,
            jobs if isinstance(jobs, dict) else {"jobs": [], "artifacts": []},
        )

    def _job_artifact_resources(
        self, records: dict, *, thorough: bool, deadline: float,
    ) -> tuple[list[ResourceObservation], dict]:
        resources = []
        state = "complete"
        terminal = {
            "succeeded", "failed", "timed_out", "cancelled", "interrupted",
        }
        for artifact in records.get("artifacts", ()):
            if time.monotonic() >= deadline:
                state = "timed_out"
                break
            if not isinstance(artifact, dict):
                continue
            job_id = artifact.get("job_id")
            artifact_id = artifact.get("artifact_id")
            relative = artifact.get("stored_relative_path")
            if not all(isinstance(value, str) and value for value in (
                job_id, artifact_id, relative,
            )):
                continue
            path = self.runtime_root / "jobs" / job_id / relative
            if not _inside(path, self.runtime_root / "jobs"):
                continue
            expires_at = artifact.get("expires_at")
            expired_by_time = False
            if isinstance(expires_at, str) and expires_at:
                try:
                    expired = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00"),
                    )
                    expired_by_time = expired <= self.clock()
                except (TypeError, ValueError):
                    pass
            expired = (
                artifact.get("job_lifecycle") in terminal
                and (
                    artifact.get("status") == "expired"
                    or expired_by_time
                )
            )
            if not path.exists() and not path.is_symlink():
                continue
            metadata_size = artifact.get("size_bytes")
            if (
                isinstance(metadata_size, bool)
                or not isinstance(metadata_size, int)
                or metadata_size < 0
            ):
                metadata_size = None
            if thorough:
                remaining = min(deadline - time.monotonic(), 5)
                if remaining <= 0:
                    size_state, measured_size, error = (
                        "timed_out", None, "measurement timed out",
                    )
                else:
                    size_state, measured_size, error = self._du(
                        path, remaining,
                    )
            else:
                size_state = "measured" if metadata_size is not None else "not_measured"
                measured_size, error = metadata_size, None
            classification = "disposable_cache" if expired else "retained"
            if classification == "disposable_cache" and size_state != "measured":
                classification = "unverified"
            resources.append(ResourceObservation(
                resource_id=_resource_id("job_artifact", str(path)),
                kind="job_artifact", locator=str(path),
                display_name=str(artifact.get("display_name") or artifact_id),
                owner_kind="job", owner_id=job_id,
                classification=classification,
                size_state=size_state, size_bytes=measured_size,
                reclaimable_bytes=(
                    measured_size or 0
                    if classification == "disposable_cache" else 0
                ),
                references=(
                    () if expired else ("job_retention",)
                ),
                evidence=("job_registry", "terminal", "expired")
                if expired else ("job_registry", "retained"),
                errors=(error,) if error else (),
            ))
        return resources, {"category": "job_artifacts", "status": state}

    def observe(
        self, *, thorough: bool, budget_seconds: float,
        progress=None, focus: str | None = None, deep: bool = False,
        cancelled=False,
    ) -> ProviderSnapshot:
        request = ResourceRequest(float(budget_seconds), cancelled)
        if request.is_cancelled():
            try:
                capacity = self._capacity()
            except OSError:
                capacity = None
            return ProviderSnapshot(
                self.target(), capacity, (), ({
                    "category": "resource_measurement",
                    "status": "cancelled",
                    "reason": "request_cancelled_before_collection",
                },),
            )
        deadline = time.monotonic() + float(budget_seconds)
        capacity = self._capacity()
        workspace_ownership = self._workspace_ownership()
        protected_paths, protected_projects, job_records = self._ownership_index()
        if progress:
            progress("docker")
        inventory, docker_outcomes = self._docker_inventory(deadline)
        active_sources = {
            str(mount.get("Source"))
            for container in inventory.get("containers", ())
            if container.get("State", {}).get("Running")
            for mount in container.get("Mounts", ())
            if mount.get("Type") == "bind" and mount.get("Source")
        }
        if progress:
            progress("sandbox_paths")
        path_resources, path_outcomes = self._path_resources(
            thorough=thorough, deadline=deadline, active_sources=active_sources,
            protected_paths=protected_paths,
            workspace_ownership=workspace_ownership,
        )
        job_resources, job_outcome = self._job_artifact_resources(
            job_records, thorough=thorough, deadline=deadline,
        )
        resources = [
            *path_resources,
            *job_resources,
            *self._docker_resources(
                inventory, thorough=thorough, deadline=deadline,
                protected_projects=protected_projects,
                workspace_ownership=workspace_ownership,
            ),
        ]
        if thorough:
            for path, name in ((Path("/var/log"), "host_logs"), (Path("/var/cache"), "host_package_cache")):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    path_outcomes.append({"category": name, "status": "timed_out"})
                    continue
                resources.append(self._path_observation(
                    path, kind="log" if name == "host_logs" else "package_cache",
                    classification="unmanaged", owner_kind="unmanaged", owner_id=None,
                    thorough=True, timeout=min(remaining, 5),
                    evidence=("monitoring_only",),
                ))
        deep_attribution = None
        deep_outcomes = []
        if deep:
            try:
                capacity_snapshots = self._deep_capacity_snapshots(deadline)
            except Exception:
                # The collector retains its own inventory fallback; a failed
                # pre-snapshot must not discard its independent evidence.
                capacity_snapshots = {}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                deep_attribution = self._deep_collector_failure(
                    capacity,
                    status="timed_out",
                    reason="overall_budget_exhausted",
                )
                deep_outcomes.append({
                    "category": "deep_attribution",
                    "status": "partial",
                    "reason": "overall_budget_exhausted",
                })
            else:
                try:
                    deep_attribution = self.deep_collector_factory(
                        self.runner,
                        host_root=self.host_root,
                        sandbox_home=self.sandbox_home,
                    ).collect(
                        capacity=capacity,
                        budget_seconds=remaining,
                        progress=progress,
                        managed_roots=self._deep_managed_roots(),
                        capacity_snapshots=capacity_snapshots,
                        cancelled=cancelled,
                    )
                    deep_outcomes.append({
                        "category": "deep_attribution",
                        "status": deep_attribution.status,
                    })
                except Exception:
                    deep_attribution = self._deep_collector_failure(capacity)
                    deep_outcomes.append({
                        "category": "deep_attribution",
                        "status": "partial",
                        "reason": "deep_collector_failed",
                    })
        capacity_scope_id = getattr(
            deep_attribution, "capacity_scope_id", None,
        )
        return ProviderSnapshot(
            self.target(), capacity, tuple(resources),
            tuple((
                *docker_outcomes,
                *path_outcomes,
                job_outcome,
                {
                    "category": "workspace_ownership",
                    "status": (
                        "complete" if workspace_ownership.available and not workspace_ownership.incomplete
                        else "partial" if workspace_ownership.available
                        else "unavailable"
                    ),
                    "reason": workspace_ownership.reason,
                },
                *deep_outcomes,
            )),
            {
                "overlap_categories": ["job_storage_contains_job_artifacts"],
            } if job_resources else None,
            deep_attribution,
            capacity_scope_id,
        )

    def _find_current(self, candidate: CleanupCandidate) -> ResourceObservation | None:
        snapshot = self.observe(thorough=True, budget_seconds=30)
        return next((
            item for item in snapshot.resources
            if item.resource_id == candidate.resource_id
        ), None)

    def revalidate(self, candidate: CleanupCandidate) -> ResourceObservation | None:
        return self._find_current(candidate)

    def _remove_path(self, path: Path, *, recreate: bool = False) -> str:
        if not _inside(path, self.sandbox_home) or path == self.sandbox_home:
            raise ValueError("cleanup path is outside the Sandbox home")
        if not path.exists():
            return "already_absent"
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        if recreate:
            path.mkdir(parents=True, exist_ok=True)
        return "removed"

    def remove(self, candidate: CleanupCandidate) -> CleanupItemOutcome:
        current = self._find_current(candidate)
        now = self.clock()
        if current is None:
            return CleanupItemOutcome(
                candidate.resource_id, "already_absent", "already_absent",
                candidate.expected_size_bytes, False, now,
            )
        refreshed = CleanupCandidate.from_observation(current)
        if (
            refreshed.locator_digest != candidate.locator_digest
            or refreshed.evidence_digest != candidate.evidence_digest
        ):
            return CleanupItemOutcome(
                candidate.resource_id, "skipped", "evidence_changed",
                current.size_bytes, True, now,
            )
        if candidate.kind in {"download_cache", "job_artifact", "worktree", "runtime"}:
            status = self._remove_path(
                Path(candidate.locator),
                recreate=candidate.kind == "download_cache",
            )
        elif candidate.kind == "volume":
            result = self._run(("docker", "volume", "rm", candidate.locator), 60)
            status = "removed" if result.returncode == 0 else (
                "timed_out" if result.returncode == 124 else "failed"
            )
        elif candidate.kind == "container":
            result = self._run(("docker", "container", "rm", candidate.locator), 60)
            status = "removed" if result.returncode == 0 else (
                "timed_out" if result.returncode == 124 else "failed"
            )
        elif candidate.kind == "network":
            # Network lifecycle state is intentionally diagnostic-only until
            # leases, containers, and jobs have an explicit release record.
            # Never turn a stale plan or a forged candidate into direct Docker
            # network deletion.
            status = "failed"
        elif candidate.kind == "image":
            result = self._run(("docker", "image", "rm", candidate.locator), 60)
            status = "removed" if result.returncode == 0 else (
                "timed_out" if result.returncode == 124 else "failed"
            )
        elif candidate.kind == "build_cache":
            if not _BUILD_CACHE_ID.fullmatch(candidate.locator):
                status = "failed"
            else:
                result = self._run((
                    "docker", "buildx", "prune", "--force", "--all",
                    "--filter", f"id={candidate.locator}",
                ), 60)
                status = "removed" if result.returncode == 0 else (
                    "timed_out" if result.returncode == 124 else "failed"
                )
        else:
            status = "failed"
        return CleanupItemOutcome(
            candidate.resource_id, status, status,
            current.size_bytes, False, now,
        )
