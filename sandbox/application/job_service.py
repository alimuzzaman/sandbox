"""Application boundary for durable job use cases.

Concrete submission, observation, cancellation, and retention behavior is added behind
this module so CLI and MCP adapters share one service contract.
"""

from __future__ import annotations

from typing import Protocol


class JobServiceProtocol(Protocol):
    def submit(self, submission): ...
    def get(self, job_id: str, *, reconcile: bool = True): ...
    def list(self, query): ...
    def read_output(self, job_id: str, query): ...
