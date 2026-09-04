"""Typed server adapter registration boundary."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Protocol, Sequence, runtime_checkable

from ..models import (
    InstanceConfigAuthority,
    PhaseResult,
    RuntimeObservation,
    ServerConfigFragment,
    ValidationEvidence,
)


@dataclass(frozen=True)
class AdapterDescriptor:
    server_type: str
    adapter_id: str
    authority_versions: tuple[str, ...]
    renderer_revision: str
    active_image_families: tuple[str, ...]
    web_service: str
    mount_layout: str
    readiness_contract: str

    def __post_init__(self) -> None:
        if self.server_type not in {"nginx", "litespeed"}:
            raise ValueError("server_unsupported")
        if (
            not self.adapter_id
            or not self.authority_versions
            or not self.renderer_revision
            or not self.active_image_families
            or not self.web_service
            or not self.mount_layout
            or not self.readiness_contract
        ):
            raise ValueError("adapter_invalid")


@dataclass(frozen=True)
class RenderedFile:
    name: str
    content: bytes
    mode: int = 0o400

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", self.name) is None:
            raise ValueError("rendered file name is invalid")
        if not isinstance(self.content, bytes) or not 1 <= len(self.content) <= 16_777_216:
            raise ValueError("rendered file content is invalid")
        if self.mode != 0o400:
            raise ValueError("rendered file mode is invalid")


@dataclass(frozen=True)
class RenderedGeneration:
    generation_id: str
    files: tuple[RenderedFile, ...]
    manifest_digest: str

    def __post_init__(self) -> None:
        digest = re.compile(r"sha256:[0-9a-f]{64}\Z")
        if digest.fullmatch(self.generation_id) is None:
            raise ValueError("generation ID is invalid")
        if digest.fullmatch(self.manifest_digest) is None:
            raise ValueError("manifest digest is invalid")
        if not isinstance(self.files, tuple) or any(
            not isinstance(item, RenderedFile) for item in self.files
        ):
            raise ValueError("rendered files are invalid")
        names = tuple(item.name for item in self.files)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("rendered files must be sorted and duplicate-free")


@runtime_checkable
class ServerConfigAdapter(Protocol):
    descriptor: AdapterDescriptor

    def policy(
        self, fragment: ServerConfigFragment, instance: InstanceConfigAuthority
    ) -> PhaseResult: ...

    def render(
        self, fragments: Sequence[ServerConfigFragment], instance: InstanceConfigAuthority
    ) -> RenderedGeneration: ...

    def observe_runtime(
        self, instance: InstanceConfigAuthority, deadline: float
    ) -> RuntimeObservation: ...

    def validate(
        self, generation: RenderedGeneration, observation: RuntimeObservation,
        deadline: float,
    ) -> ValidationEvidence: ...

    def activate(
        self, generation_id: str, observation: RuntimeObservation, deadline: float
    ) -> PhaseResult: ...

    def reload(self, observation: RuntimeObservation, deadline: float) -> PhaseResult: ...

    def observe_ready(
        self, generation_id: str, observation: RuntimeObservation, deadline: float
    ) -> PhaseResult: ...

    def restore(
        self, generation_id: str, observation: RuntimeObservation, deadline: float
    ) -> PhaseResult: ...


class AdapterRegistry:
    def __init__(self, descriptors: Iterable[AdapterDescriptor]):
        by_type: dict[str, AdapterDescriptor] = {}
        ids: set[str] = set()
        for descriptor in descriptors:
            if descriptor.server_type in by_type or descriptor.adapter_id in ids:
                raise ValueError("adapter_duplicate")
            by_type[descriptor.server_type] = descriptor
            ids.add(descriptor.adapter_id)
        self._by_type = by_type

    def server_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_type))

    def require(self, server_type: str) -> AdapterDescriptor:
        try:
            return self._by_type[server_type]
        except KeyError:
            raise ValueError("server_unsupported") from None
