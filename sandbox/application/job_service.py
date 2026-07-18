"""Application boundary for durable job use cases.

Concrete submission, observation, cancellation, and retention behavior is added behind
this module so CLI and MCP adapters share one service contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class JobServiceProtocol(Protocol):
    def submit(self, submission): ...
    def get(self, job_id: str, *, reconcile: bool = True): ...
    def list(self, query): ...
    def read_output(self, job_id: str, query): ...


@dataclass
class JobService:
    """Foundational service composition; execution use cases are added in US1."""

    repository: Any
    storage: Any
    components: Any

    def get(self, job_id: str, *, reconcile: bool = True):
        del reconcile
        return self.repository.snapshot(job_id)

    def list(self, query=None):
        query = dict(query or {})
        return self.repository.list(**query)
