"""Host-local durable-job transport contract."""

from __future__ import annotations

from typing import Protocol


class JobTransport(Protocol):
    def submit(self, submission): ...
    def get(self, job_id: str): ...
    def read_output(self, job_id: str, query): ...
