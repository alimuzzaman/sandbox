"""Managed runtime package and backend ownership records."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from sandbox.isolation.models import canonical_digest


def _frozen(value, label):
    if not isinstance(value, Mapping): raise ValueError(f"{label} must be an object")
    if any(str(key).lower() in {"password", "token", "secret"} for key in value):
        raise ValueError(f"{label} contains secret material")
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class PhpExtensionPackage:
    """One catalog-resolved PHP extension package in a managed image.

    The project asks for a capability (for example ``gd``), never an APT
    package.  This record is produced only after the managed adapter resolves
    that capability through its immutable catalog and the configured signed
    APT simulator.  Keeping the catalog digest and source on every row makes
    the package transaction self-describing without adding a new privileged
    verb or accepting package metadata from a project.
    """

    extension: str
    package: str
    package_version: str
    state: str = "enabled"
    version_constraint: str | None = None
    catalog_digest: str = ""
    source: str = "official-distribution"

    def __post_init__(self):
        if not isinstance(self.extension, str) or not self.extension:
            raise ValueError("managed PHP extension name is invalid")
        if (not isinstance(self.package, str)
                or not self.package.startswith("php")
                or not isinstance(self.package_version, str)
                or not self.package_version):
            raise ValueError("managed PHP extension package is invalid")
        if self.state != "enabled":
            raise ValueError("managed PHP extension package state is invalid")
        if self.source != "official-distribution":
            raise ValueError("managed PHP extension package source is invalid")
        if (not isinstance(self.catalog_digest, str)
                or not self.catalog_digest.startswith("sha256:")
                or len(self.catalog_digest) != 71):
            raise ValueError("managed PHP extension catalog digest is invalid")

    def to_dict(self):
        value = {
            "name": self.extension,
            "package": self.package,
            "package_version": self.package_version,
            "state": self.state,
            "catalog_digest": self.catalog_digest,
            "source": self.source,
        }
        if self.version_constraint is not None:
            value["version_constraint"] = self.version_constraint
        return value


@dataclass(frozen=True)
class ManagedPhpExtensionPlan:
    """Digest-bound, read-only resolution of normalized PHP requirements."""

    php_version: str
    profile: str | None
    requirements: tuple[Mapping[str, Any], ...]
    packages: tuple[PhpExtensionPackage, ...]
    catalog_digest: str
    digest: str = ""

    def __post_init__(self):
        if (not isinstance(self.php_version, str)
                or not self.php_version.startswith("8.")):
            raise ValueError("managed PHP version is unsupported")
        if (not isinstance(self.catalog_digest, str)
                or not self.catalog_digest.startswith("sha256:")
                or len(self.catalog_digest) != 71):
            raise ValueError("managed PHP extension catalog digest is invalid")
        object.__setattr__(self, "requirements", tuple(
            _frozen(value, "PHP extension requirement")
            for value in self.requirements
        ))
        object.__setattr__(self, "packages", tuple(self.packages))
        basis = {
            "php_version": self.php_version,
            "profile": self.profile,
            "requirements": [dict(value) for value in self.requirements],
            "packages": [value.to_dict() for value in self.packages],
            "catalog_digest": self.catalog_digest,
        }
        expected = canonical_digest(basis)
        if self.digest and self.digest != expected:
            raise ValueError("managed PHP extension plan digest is invalid")
        object.__setattr__(self, "digest", expected)

    def to_dict(self):
        return {
            "php_version": self.php_version,
            "profile": self.profile,
            "requirements": [dict(value) for value in self.requirements],
            "packages": [value.to_dict() for value in self.packages],
            "catalog_digest": self.catalog_digest,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class PackageTransactionPlan:
    matrix_id: str
    host_packages: tuple[Mapping[str, Any], ...]
    image_packages: tuple[Mapping[str, Any], ...]
    sources: tuple[Mapping[str, Any], ...]
    service_effects: tuple[Mapping[str, Any], ...]
    owned_roots: tuple[str, ...]
    privilege_actions: tuple[str, ...]
    simulation_digest: str = ""
    confirmation: Mapping[str, Any] | None = None

    def __post_init__(self):
        for name in ("host_packages", "image_packages", "sources", "service_effects"):
            object.__setattr__(self, name, tuple(_frozen(v, name) for v in getattr(self, name)))
        object.__setattr__(self, "owned_roots", tuple(self.owned_roots))
        object.__setattr__(self, "privilege_actions", tuple(self.privilege_actions))
        basis = {"matrix_id": self.matrix_id,
                 "host_packages": [dict(v) for v in self.host_packages],
                 "image_packages": [dict(v) for v in self.image_packages],
                 "sources": [dict(v) for v in self.sources],
                 "service_effects": [dict(v) for v in self.service_effects],
                 "owned_roots": list(self.owned_roots),
                 "privilege_actions": list(self.privilege_actions)}
        expected = canonical_digest(basis)
        if self.simulation_digest and self.simulation_digest != expected:
            raise ValueError("package simulation digest is invalid")
        object.__setattr__(self, "simulation_digest", expected)
        if self.confirmation is not None:
            object.__setattr__(self, "confirmation", _frozen(self.confirmation, "confirmation"))

    def to_dict(self):
        return {"matrix_id": self.matrix_id,
                "host_packages": [dict(value) for value in self.host_packages],
                "image_packages": [dict(value) for value in self.image_packages],
                "sources": [dict(value) for value in self.sources],
                "service_effects": [dict(value) for value in self.service_effects],
                "owned_roots": list(self.owned_roots),
                "privilege_actions": list(self.privilege_actions),
                "simulation_digest": self.simulation_digest}


@dataclass(frozen=True)
class NativeBackendRecord:
    owner: Mapping[str, str]
    mode: str
    adapter: str
    backend: Mapping[str, Any]
    machine: Mapping[str, Any]
    php: Mapping[str, Any]
    database: Mapping[str, Any]
    files: Mapping[str, Any]
    health: str = "pending"
    last_applied: str = ""

    def __post_init__(self):
        if self.mode not in {"managed_native", "incumbent_native"}:
            raise ValueError("native backend mode is invalid")
        if self.health not in {"pending", "ready", "unhealthy", "blocked", "drifted"}:
            raise ValueError("native backend health is invalid")
        for name in ("owner", "backend", "machine", "php", "database", "files"):
            object.__setattr__(self, name, _frozen(getattr(self, name), name))
