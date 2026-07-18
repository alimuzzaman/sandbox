"""Application boundary for local/remote/workspace target resolution."""

from __future__ import annotations

from typing import Protocol


class TargetServiceProtocol(Protocol):
    def resolve(self, request): ...
