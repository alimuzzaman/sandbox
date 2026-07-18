"""Application boundary for durable job use cases.

Concrete submission, observation, cancellation, and retention behavior is added behind
this module so CLI and MCP adapters share one service contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from sandbox.jobs.models import JobSubmission, OutputQuery
from sandbox.jobs.output import JobOutputStore


_DETACHED_SUPERVISORS: list[subprocess.Popen] = []


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
    launcher: Any = None

    def submit(self, submission: JobSubmission):
        row, replay = self.repository.accept(submission)
        if replay:
            return self._accepted(row, replay=True)
        try:
            self.storage.job_dir(row["job_id"], create=True)
            descriptor = self._descriptor(row)
            descriptor_path = self.storage.write_json_atomic(row["job_id"], "descriptor.json", descriptor)
            self._launch(descriptor_path)
        except BaseException as exc:
            self.repository.transition(row["job_id"], "failed", termination_reason="supervisor_launch_failed")
            raise RuntimeError("supervisor_launch_failed") from exc
        return self._accepted(row, replay=False)

    def _descriptor(self, row: dict) -> dict:
        nonce = os.urandom(32)
        return {"job_id": row["job_id"], "registry_path": str(self.repository.path),
                "runtime_dir": str(self.storage.root.parent), "argv": __import__("json").loads(row["command_json"]),
                "cwd": str(Path(row["project_root"]) / row["cwd_relative"]),
                "deadline_seconds": row["deadline_seconds"], "cancel_grace_seconds": 20,
                "nonce_hash": hashlib.sha256(nonce).hexdigest(), "environment": None}

    def _launch(self, descriptor_path: Path) -> None:
        if self.launcher:
            self.launcher(descriptor_path)
            return
        _DETACHED_SUPERVISORS[:] = [process for process in _DETACHED_SUPERVISORS if process.poll() is None]
        _DETACHED_SUPERVISORS.append(subprocess.Popen([sys.executable, "-m", "sandbox.jobs.supervisor", str(descriptor_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True))

    @staticmethod
    def _accepted(row: dict, *, replay: bool) -> dict:
        return {"ok": True, "job_id": row["job_id"], "status": "accepted", "kind": row["kind"],
                "target": {"kind": row["target_kind"], "remote": row["remote_name"]},
                "workspace": row["workspace_label"], "output_profile": row["output_profile"],
                "idempotent_replay": replay}

    def get(self, job_id: str, *, reconcile: bool = True):
        del reconcile
        return self.repository.snapshot(job_id)

    def list(self, query=None):
        query = dict(query or {})
        return self.repository.list(**query)

    def read_output(self, job_id: str, query: OutputQuery | None = None):
        query = query or OutputQuery()
        return JobOutputStore(self.storage, self.repository, job_id).read(query)
