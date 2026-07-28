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
from typing import Callable, Protocol

from sandbox.services.process import BoundedProcessRunner

from .models import (
    CleanupCandidate,
    CleanupItemOutcome,
    ResourceObservation,
    StorageTarget,
    utc_now,
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


class ResourceAdapter(Protocol):
    def target(self) -> StorageTarget: ...

    def observe(
        self, *, thorough: bool, budget_seconds: float,
        progress=None, focus: str | None = None,
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
        self.clock = clock
        self.host_root = Path(host_root)

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

    def _run(self, argv, timeout: float):
        return self.runner.run(tuple(str(item) for item in argv), timeout=max(timeout, 0.01))

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
            return max(min(deadline - time.monotonic(), limit), 0.01)

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
                    resources.append(self._path_observation(
                        path, kind="worktree", classification=classification,
                        owner_kind="workspace" if is_workspace else "project",
                        owner_id=path.name, thorough=thorough,
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

    def _docker_resources(
        self, inventory: dict, *, thorough: bool, deadline: float,
        protected_projects: set[str],
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
                owner_kind="project", owner_id=owner,
                classification=(
                    "active" if running else
                    "retained" if owner in protected_projects else
                    "disposable_cache"
                ),
                size_state="measured" if size is not None else "unavailable",
                size_bytes=size,
                reclaimable_bytes=(
                    size or 0
                    if not running and owner not in protected_projects
                    and size is not None
                    else 0
                ),
                references=(
                    ("running_container",) if running else
                    ("instance_registry",) if owner in protected_projects else ()
                ),
                evidence=("compose_project_label", "stopped" if not running else "running"),
            ))
        for volume in inventory.get("volumes", ()):
            name = volume.get("Name")
            if not isinstance(name, str) or not name:
                continue
            owner = self._compose_owner(volume.get("Labels"))
            if not owner:
                classification, owner_kind = "unmanaged", "unmanaged"
            elif name in active_volumes:
                classification, owner_kind = "active", "project"
            elif owner in protected_projects:
                classification, owner_kind = "retained", "project"
            else:
                classification, owner_kind = "unverified", "project"
            size_state, size, error = "not_measured", None, None
            if (
                thorough and owner and name not in active_volumes
                and owner not in protected_projects
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
                owner_kind=owner_kind, owner_id=owner,
                classification=classification,
                size_state=size_state, size_bytes=size,
                reclaimable_bytes=size or 0 if classification == "stale_candidate" else 0,
                references=(
                    ("live_container_mount",) if name in active_volumes else
                    ("instance_registry",) if owner in protected_projects else ()
                ),
                evidence=("compose_project_label",) if owner else ("ownership_unverified",),
                errors=(error,) if error else (),
            ))
        for network in inventory.get("networks", ()):
            network_id = network.get("Id")
            if not isinstance(network_id, str) or not network_id:
                continue
            owner = self._compose_owner(network.get("Labels"))
            if not owner:
                continue
            active = bool(network.get("Containers"))
            resources.append(ResourceObservation(
                resource_id=_resource_id("network", network_id),
                kind="network", locator=network_id,
                display_name=str(network.get("Name") or network_id),
                owner_kind="project", owner_id=owner,
                classification=(
                    "active" if active else
                    "retained" if owner in protected_projects else
                    "disposable_cache"
                ),
                size_state="measured", size_bytes=0, reclaimable_bytes=0,
                references=(
                    ("connected_container",) if active else
                    ("instance_registry",) if owner in protected_projects else ()
                ),
                evidence=("compose_project_label",),
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
            used = locator in used_images
            size = image.get("Size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                size = None
            display = next(iter(image.get("RepoTags") or ()), locator)
            classification = (
                "active" if used else
                "retained" if image_owner in protected_projects else
                "disposable_cache"
            )
            resources.append(ResourceObservation(
                resource_id=_resource_id("image", locator),
                kind="image", locator=locator, display_name=str(display),
                owner_kind="project", owner_id=image_owner,
                classification=classification,
                size_state="measured" if size is not None else "unavailable",
                size_bytes=size,
                reclaimable_bytes=(
                    size or 0 if classification == "disposable_cache" else 0
                ),
                references=(
                    ("container_image",) if used else
                    ("instance_registry",)
                    if image_owner in protected_projects else ()
                ),
                evidence=("compose_project_label",),
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
                size_state, measured_size, error = self._du(
                    path, min(max(deadline - time.monotonic(), 0.01), 5),
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
        progress=None, focus: str | None = None,
    ) -> ProviderSnapshot:
        deadline = time.monotonic() + float(budget_seconds)
        capacity = self._capacity()
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
        return ProviderSnapshot(
            self.target(), capacity, tuple(resources),
            tuple((*docker_outcomes, *path_outcomes, job_outcome)),
            {
                "overlap_categories": ["job_storage_contains_job_artifacts"],
            } if job_resources else None,
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
            result = self._run(("docker", "network", "rm", candidate.locator), 60)
            status = "removed" if result.returncode == 0 else (
                "timed_out" if result.returncode == 124 else "failed"
            )
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
