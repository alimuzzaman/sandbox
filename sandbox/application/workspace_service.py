"""Application boundary for persistent and isolated workspace lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class WorkspaceServiceProtocol(Protocol):
    def create(self, request): ...
    def list(self, request): ...
    def status(self, request): ...
    def reset(self, request): ...
    def destroy(self, request): ...


@dataclass
class WorkspaceService:
    """Composition boundary; workspace lifecycle behavior is implemented in US3."""

    target_service: Any
