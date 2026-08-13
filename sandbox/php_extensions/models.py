"""Immutable PHP extension requirement models.

The config layer intentionally deals in requirements, not package names or
build instructions.  Runtime adapters may resolve these requirements through
their own allow-listed catalogs, but the project descriptor never gets to
choose an artifact, repository, or shell fragment.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class PhpExtensionRequirement:
    """One immutable assertion about a PHP extension."""

    name: str
    state: str = "enabled"
    version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("PHP extension name must be a non-empty string")
        if self.state not in {"enabled", "disabled"}:
            raise ValueError("PHP extension state must be enabled or disabled")
        if self.state == "disabled" and self.version is not None:
            raise ValueError("disabled PHP extensions cannot specify a version")
        if self.version is not None and (
                not isinstance(self.version, str) or not self.version):
            raise ValueError("PHP extension version must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        value: dict[str, str] = {"state": self.state}
        if self.version is not None:
            value["version"] = self.version
        return value


@dataclass(frozen=True)
class PhpExtensionsConfig(Mapping[str, Any]):
    """Canonical immutable ``phpExtensions`` configuration.

    It implements the read-only mapping protocol so existing descriptor
    consumers can inspect ``config["extensions"]`` without having to know
    about the model class.  ``to_dict`` is the explicit serialization boundary
    for JSON or state persistence; no mutable object is retained internally.
    """

    profile: str | None
    requirements: tuple[PhpExtensionRequirement, ...]
    capabilities: tuple[tuple[str, tuple[str, ...]], ...] = ()
    profile_required: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirements", tuple(self.requirements))
        object.__setattr__(self, "capabilities", tuple(
            (str(name), tuple(values)) for name, values in self.capabilities
        ))
        object.__setattr__(self, "profile_required", tuple(self.profile_required))
        names = [item.name for item in self.requirements]
        if len(names) != len(set(names)):
            raise ValueError("PHP extension requirements must have unique names")
        if names != sorted(names):
            raise ValueError("PHP extension requirements must be canonicalized")

    @property
    def by_name(self) -> Mapping[str, PhpExtensionRequirement]:
        return MappingProxyType({item.name: item for item in self.requirements})

    @property
    def extension_values(self) -> Mapping[str, Mapping[str, str]]:
        return MappingProxyType({
            item.name: MappingProxyType(item.to_dict())
            for item in self.requirements
        })

    def _mapping(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "profile": self.profile,
            "extensions": self.extension_values,
            "required": self.profile_required,
            "capabilities": MappingProxyType({
                name: tuple(values) for name, values in self.capabilities
            }),
        })

    def __getitem__(self, key: str) -> Any:
        return self._mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(("profile", "extensions", "required", "capabilities"))

    def __len__(self) -> int:
        return 4

    def to_dict(self) -> dict[str, Any]:
        """Return the stable public ``{profile, extensions}`` form.

        Profile requirements and capability alternatives are immutable catalog
        metadata exposed through their dedicated attributes.  They are not
        repeated in this input-shaped mapping, which keeps downstream runtime
        planners' accepted-key boundary narrow and prevents derived metadata
        from being mistaken for project-supplied package configuration.
        """
        return {
            "profile": self.profile,
            "extensions": {
                item.name: item.to_dict() for item in self.requirements
            },
        }
