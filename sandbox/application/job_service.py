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
from sandbox.jobs.health import classify
from sandbox.jobs.models import Lifecycle
from sandbox.jobs.process import ProcessIdentity, signal_owned_process_group
from sandbox.jobs.scheduler import WorkspaceBusy


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
    scheduler: Any = None

    def submit(self, submission: JobSubmission):
        row, replay = self.repository.accept(submission)
        if replay:
            return self._accepted(row, replay=True)
        try:
            self.storage.job_dir(row["job_id"], create=True)
            descriptor = self._descriptor(row, submission)
            descriptor_path = self.storage.write_json_atomic(row["job_id"], "descriptor.json", descriptor)
            if self.scheduler is not None:
                try:
                    self.scheduler.acquire(row, parallel_safe=submission.workspace_mode == "isolated")
                except WorkspaceBusy:
                    self.repository.transition(row["job_id"], Lifecycle.QUEUED)
                    return {**self._accepted(row, replay=False), "queue": {"reason": "workspace_or_capacity_busy"}}
            self._launch(descriptor_path)
        except BaseException as exc:
            if self.scheduler is not None:
                self.scheduler.release(row["job_id"])
            self.repository.transition(row["job_id"], "failed", termination_reason="supervisor_launch_failed")
            raise RuntimeError("supervisor_launch_failed") from exc
        return self._accepted(row, replay=False)

    def _descriptor(self, row: dict, submission: JobSubmission) -> dict:
        nonce = os.urandom(32)
        return {"job_id": row["job_id"], "registry_path": str(self.repository.path),
                "runtime_dir": str(self.storage.root.parent), "argv": __import__("json").loads(row["command_json"]),
                "cwd": str(Path(row["project_root"]) / row["cwd_relative"]),
                "deadline_seconds": row["deadline_seconds"], "cancel_grace_seconds": 20,
                "nonce_hash": hashlib.sha256(nonce).hexdigest(), "environment": None,
                "artifact_paths": list(submission.artifact_paths)}

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
        snapshot = self.repository.snapshot(job_id)
        if snapshot["lifecycle"] == Lifecycle.QUEUED.value and self.scheduler is not None:
            try:
                self.scheduler.acquire(snapshot, parallel_safe=snapshot["workspace_mode"] == "isolated")
                self._launch(self.storage.job_dir(job_id) / "descriptor.json")
                snapshot = self.repository.snapshot(job_id)
            except WorkspaceBusy:
                pass
        if reconcile:
            health, evidence = classify(snapshot)
            if snapshot["lifecycle"] not in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
                self.repository.set_health(job_id, health, evidence)
                snapshot = self.repository.snapshot(job_id)
            snapshot["health"] = health.value
            snapshot["health_evidence"] = evidence
        if self.scheduler is not None and snapshot["lifecycle"] in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            self.scheduler.release(job_id)
        return snapshot

    def list(self, query=None):
        query = dict(query or {})
        return self.repository.list(**query)

    def read_output(self, job_id: str, query: OutputQuery | None = None):
        query = query or OutputQuery()
        return JobOutputStore(self.storage, self.repository, job_id).read(query)

    def cancel(self, job_id: str, *, force: bool = False):
        snapshot = self.repository.snapshot(job_id)
        if snapshot["lifecycle"] in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            raise RuntimeError("already_terminal")
        process = snapshot.get("process") or {}
        if not process.get("child_pid") or not process.get("child_pgid"):
            raise RuntimeError("process_identity_mismatch")
        identity = ProcessIdentity(process["host_boot_id"], int(process["child_pid"]),
            process["child_start_identity"], process["supervisor_nonce_hash"], int(process["child_pgid"]))
        if not signal_owned_process_group(identity, 9 if force else 15):
            raise RuntimeError("process_identity_mismatch")
        self.repository.transition(job_id, Lifecycle.CANCELLING)
        return self.repository.snapshot(job_id)

    def list_artifacts(self, job_id: str):
        return self.repository.snapshot(job_id)["artifacts"]

    def read_metrics(self, job_id: str, *, limit: int = 500):
        from sandbox.jobs.metrics import read
        return {"ok": True, "job_id": job_id, "samples": read(self.storage, job_id, limit=limit)}

    def get_artifact(self, job_id: str, artifact_id: str, *, offset: int = 0, max_bytes: int = 1_048_576) -> bytes:
        import base64
        for artifact in self.list_artifacts(job_id):
            if artifact["artifact_id"] == artifact_id:
                path = self.storage.job_dir(job_id) / artifact["stored_relative_path"]
                with path.open("rb") as handle:
                    handle.seek(offset)
                    return handle.read(max_bytes)
        raise RuntimeError("artifact_not_found")

    def retry(self, job_id: str, *, request_id: str | None = None):
        import json
        from sandbox.jobs.models import SourceIdentity
        previous = self.repository.get(job_id)
        if previous["lifecycle"] not in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            raise RuntimeError("job_not_terminal")
        return self.submit(JobSubmission(
            kind=previous["kind"], project_root=previous["project_root"], project_identity=previous["project_identity"],
            target_kind=previous["target_kind"], remote_name=previous["remote_name"], workspace_label=previous["workspace_label"],
            workspace_mode=previous["workspace_mode"], argv=tuple(json.loads(previous["command_json"])),
            deadline_seconds=previous["deadline_seconds"], source=SourceIdentity(previous["source_identity"], previous["source_commit"], previous["source_dirty_digest"]),
            request_id=request_id, retry_of_job_id=job_id, parent_job_id=previous["root_job_id"],
            attempt=int(previous["attempt"]) + 1, cwd_relative=previous["cwd_relative"],
            execution_profile=previous["execution_profile"], output_profile=previous["output_profile"],
            deadline_source=previous["deadline_source"], stall_seconds=previous["stall_seconds"],
            cancel_on_stall=bool(previous["cancel_on_stall"]), cleanup_policy=previous["cleanup_policy"],
            environment_keys=tuple(json.loads(previous["environment_keys_json"])),
        ))

    def cleanup(self, job_id: str, *, logs: bool = True, artifacts: bool = True) -> dict:
        import shutil
        state = self.repository.get(job_id)
        if state["lifecycle"] not in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            raise RuntimeError("active_job_protected")
        directory = self.storage.job_dir(job_id)
        removed = []
        if logs:
            output = directory / "output"
            if output.exists(): shutil.rmtree(output); removed.append("logs")
        if artifacts:
            artifact_dir = directory / "artifacts"
            if artifact_dir.exists(): shutil.rmtree(artifact_dir); removed.append("artifacts")
        self.repository.transition(job_id, state["lifecycle"], cleanup_state="completed") if False else None
        return {"ok": True, "job_id": job_id, "removed": removed}
