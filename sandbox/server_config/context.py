"""Typed, side-effect-free composition values for instance server config."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
import re
import time
from typing import Any, Protocol, runtime_checkable

from .adapters.base import AdapterRegistry
from .models import (
    InstanceConfigAuthority,
    InstanceIdentityProjection,
    RuntimeMode,
    ServerType,
)


MOUNT_LAYOUT_REVISION = "server-config-mount-v1"


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


@dataclass(frozen=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class InstanceRegistry(Protocol):
    def registry_get(
        self, root: str | Path, label: str | None = None,
    ) -> Mapping[str, Any] | None: ...


class ProjectIdentityResolver(Protocol):
    def __call__(
        self, root: str | Path, label: str | None = None,
    ) -> str: ...


class RepositoryFactory(Protocol):
    def __call__(self, instance_incarnation_id: str) -> Any: ...


@dataclass(frozen=True)
class ServerConfigMountProjection:
    """Expected host mount identity, without rendering a Compose volume."""

    instance_incarnation_id: str
    mount_id: str
    source_root: Path
    layout_revision: str = MOUNT_LAYOUT_REVISION
    read_only: bool = True

    def __post_init__(self) -> None:
        InstanceIdentityProjection(self.instance_incarnation_id, None)
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.mount_id) is None:
            raise ValueError("server-config mount ID is invalid")
        if (
            not isinstance(self.source_root, Path)
            or not self.source_root.is_absolute()
            or self.source_root.name != self.instance_incarnation_id
        ):
            raise ValueError("server-config mount root is invalid")
        if self.layout_revision != MOUNT_LAYOUT_REVISION or self.read_only is not True:
            raise ValueError("server-config mount layout is invalid")


@dataclass(frozen=True)
class ServerConfigInstanceContext:
    """One fail-closed projection from an authoritative instance record."""

    authority: InstanceConfigAuthority
    identity: InstanceIdentityProjection
    expected_mount: ServerConfigMountProjection | None
    mount: ServerConfigMountProjection | None


@dataclass(frozen=True)
class ServerConfigDependencies:
    """Dependencies assembled by an outer application composition root."""

    registry: InstanceRegistry
    server_config_root: Path
    project_identity_resolver: ProjectIdentityResolver
    repository_factory: RepositoryFactory
    adapters: AdapterRegistry
    clock: Clock


def project_mount(
    server_config_root: str | Path,
    instance_incarnation_id: str,
) -> ServerConfigMountProjection:
    """Derive the relocation-stable mount identity for one incarnation."""

    # Reuse the canonical model validator.  No record, repository, or runtime
    # is read while projecting the expected mount.
    InstanceIdentityProjection(instance_incarnation_id, None)
    encoded = json.dumps(
        {
            "instance_incarnation_id": instance_incarnation_id,
            "layout_revision": MOUNT_LAYOUT_REVISION,
            "schema": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ServerConfigMountProjection(
        instance_incarnation_id=instance_incarnation_id,
        mount_id="sha256:" + hashlib.sha256(encoded).hexdigest(),
        source_root=Path(server_config_root) / instance_incarnation_id,
    )


def project_instance_context(
    *,
    record: Mapping[str, Any],
    project_identity: str,
    server_config_root: str | Path,
) -> ServerConfigInstanceContext:
    """Project typed fragment authority without adopting a legacy record."""

    identity = InstanceIdentityProjection.from_existing_record(record)
    expected = (
        project_mount(server_config_root, identity.instance_incarnation_id)
        if identity.instance_incarnation_id is not None
        else None
    )
    attached = (
        expected
        if expected is not None
        and identity.server_config_mount_id == expected.mount_id
        else None
    )
    raw_server = record.get("server")
    try:
        server_type = ServerType(raw_server)
    except (TypeError, ValueError):
        server_type = None
    authority = InstanceConfigAuthority(
        instance_name=str(record.get("instance") or "unknown"),
        instance_incarnation_id=identity.instance_incarnation_id,
        project_identity=project_identity,
        server_type=server_type,
        runtime_mode=(
            RuntimeMode.LOCAL_COMPOSE
            if server_type is not None and raw_server != "herd"
            else None
        ),
        server_config_mount_id=(attached.mount_id if attached is not None else None),
        status=str(record.get("status") or "unknown"),
    )
    return ServerConfigInstanceContext(
        authority=authority,
        identity=identity,
        expected_mount=expected,
        mount=attached,
    )
