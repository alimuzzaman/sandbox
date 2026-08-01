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
