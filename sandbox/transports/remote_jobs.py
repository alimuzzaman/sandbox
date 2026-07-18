"""Bounded transport contract for a co-located remote Sandbox job service."""

from __future__ import annotations

from typing import Protocol


class RemoteJobTransport(Protocol):
    def invoke(self, remote_name: str, operation: str, payload: dict,
               *, timeout: float): ...
