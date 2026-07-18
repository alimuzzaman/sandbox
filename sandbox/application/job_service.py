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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from sandbox.jobs.models import JobSubmission, OutputQuery, SourceIdentity
from sandbox.jobs.output import JobOutputStore
from sandbox.jobs.health import classify
from sandbox.jobs.models import Lifecycle
from sandbox.jobs.process import (ProcessIdentity, capture_process_identity,
                                  signal_owned_process_group, verify_process_identity)
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
        if snapshot["kind"] in {"matrix", "ci", "plan"}:
            return self._get_parent(snapshot)
        if snapshot["lifecycle"] == Lifecycle.QUEUED.value and self.scheduler is not None:
            try:
                self.scheduler.acquire(snapshot, parallel_safe=snapshot["workspace_mode"] == "isolated")
                self._launch(self.storage.job_dir(job_id) / "descriptor.json")
                snapshot = self.repository.snapshot(job_id)
            except WorkspaceBusy:
                pass
        if reconcile:
            if self.scheduler is not None and snapshot["lifecycle"] in {"accepted", "queued", "running", "cancelling"}:
                self.scheduler.renew(job_id, deadline_seconds=snapshot["deadline_seconds"])
            health, evidence = classify(snapshot)
            if snapshot["lifecycle"] not in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
                self.repository.set_health(job_id, health, evidence)
                snapshot = self.repository.snapshot(job_id)
            snapshot["health"] = health.value
            snapshot["health_evidence"] = evidence
        if self.scheduler is not None and snapshot["lifecycle"] in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            self.scheduler.release(job_id)
        return snapshot

    def _get_parent(self, snapshot: dict) -> dict:
        """Reconcile an aggregate without launching a fake parent process."""
        children = [self.get(child["job_id"]) for child in self.repository.children(snapshot["job_id"])]
        terminal = {item.value for item in (
            Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
            Lifecycle.CANCELLED, Lifecycle.INTERRUPTED,
        )}
        states = [child["lifecycle"] for child in children]
        if not states:
            lifecycle = snapshot["lifecycle"]
        elif all(state in terminal for state in states):
            if any(state == Lifecycle.FAILED.value for state in states):
                lifecycle = Lifecycle.FAILED.value
            elif any(state == Lifecycle.TIMED_OUT.value for state in states):
                lifecycle = Lifecycle.TIMED_OUT.value
            elif any(state == Lifecycle.CANCELLED.value for state in states):
                lifecycle = Lifecycle.CANCELLED.value
            elif any(state == Lifecycle.INTERRUPTED.value for state in states):
                lifecycle = Lifecycle.INTERRUPTED.value
            else:
                lifecycle = Lifecycle.SUCCEEDED.value
        elif any(state in {Lifecycle.RUNNING.value, Lifecycle.CANCELLING.value} for state in states):
            lifecycle = Lifecycle.RUNNING.value
        else:
            lifecycle = Lifecycle.QUEUED.value
        current = snapshot["lifecycle"]
        if lifecycle != current:
            try:
                snapshot = self.repository.transition(snapshot["job_id"], lifecycle,
                    result_json=__import__("json").dumps(self._aggregate_result(children), sort_keys=True))
            except ValueError:
                # A child can finish between the read and transition. The next
                # status request will reconcile from the authoritative children.
                snapshot = self.repository.snapshot(snapshot["job_id"])
        result = self._aggregate_result(children)
        return {**snapshot, "children": children, "aggregate": result,
                "health": "terminal" if lifecycle in terminal else ("active" if lifecycle == "running" else "quiet")}

    @staticmethod
    def _aggregate_result(children: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for child in children:
            state = child["lifecycle"]
            counts[state] = counts.get(state, 0) + 1
        return {"children": len(children), "counts": counts,
                "passed": counts.get(Lifecycle.SUCCEEDED.value, 0),
                "failed": sum(counts.get(state, 0) for state in (
                    Lifecycle.FAILED.value, Lifecycle.TIMED_OUT.value,
                    Lifecycle.CANCELLED.value, Lifecycle.INTERRUPTED.value,
                ))}

    def list(self, query=None):
        query = dict(query or {})
        return self.repository.list(**query)

    def reconcile_startup(self, *, limit: int = 200) -> dict:
        """Reconcile active jobs whose recorded supervisor identity is gone."""
        interrupted = []
        for row in self.repository.list(limit=limit):
            if row["lifecycle"] not in {Lifecycle.RUNNING.value, Lifecycle.CANCELLING.value}:
                continue
            process = self.repository.snapshot(row["job_id"]).get("process") or {}
            supervisor_pid = process.get("supervisor_pid")
            if not supervisor_pid or not process.get("supervisor_start_identity"):
                continue
            observed = capture_process_identity(int(supervisor_pid))
            if observed is not None:
                observed = ProcessIdentity(observed.host_boot_id, observed.pid,
                    observed.start_identity, process["supervisor_nonce_hash"], observed.process_group_id)
            expected = ProcessIdentity(process["host_boot_id"], int(supervisor_pid),
                process["supervisor_start_identity"], process["supervisor_nonce_hash"])
            if verify_process_identity(expected, observed):
                continue
            try:
                self.repository.transition(row["job_id"], Lifecycle.INTERRUPTED,
                    termination_reason="supervisor_lost", output_completeness="partial",
                    result_json=__import__("json").dumps({
                        "reconciled": True, "evidence": "recorded supervisor identity no longer matches"
                    }, sort_keys=True))
                if self.scheduler is not None:
                    self.scheduler.release(row["job_id"])
                interrupted.append(row["job_id"])
            except ValueError:
                pass
        stale = self.scheduler.reconcile_stale() if self.scheduler is not None else []
        return {"ok": True, "interrupted": interrupted, "released_leases": stale}

    def read_output(self, job_id: str, query: OutputQuery | None = None):
        query = query or OutputQuery()
        return JobOutputStore(self.storage, self.repository, job_id).read(query)

    def cancel(self, job_id: str, *, force: bool = False):
        snapshot = self.repository.snapshot(job_id)
        if snapshot["kind"] in {"matrix", "ci", "plan"}:
            children = self.repository.children(job_id)
            cancelled = []
            for child in children:
                if child["lifecycle"] not in {item.value for item in (
                    Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
                    Lifecycle.CANCELLED, Lifecycle.INTERRUPTED,
                )}:
                    cancelled.append(self.cancel(child["job_id"], force=force))
            return self._get_parent(self.repository.snapshot(job_id)) | {"cancelled_children": cancelled}
        if snapshot["lifecycle"] in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            raise RuntimeError("already_terminal")
        process = snapshot.get("process") or {}
        if not process.get("child_pid") or not process.get("child_pgid"):
            if snapshot["lifecycle"] in {Lifecycle.ACCEPTED.value, Lifecycle.QUEUED.value}:
                return self.repository.transition(job_id, Lifecycle.CANCELLED,
                    termination_reason="cancelled_before_process_start")
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

    def cleanup(self, job_id: str, *, logs: bool = True, artifacts: bool = True,
                metrics: bool = True) -> dict:
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
        if metrics:
            metric_dir = directory / "metrics"
            if metric_dir.exists(): shutil.rmtree(metric_dir); removed.append("metrics")
        self.repository.set_cleanup_state(job_id, "completed")
        return {"ok": True, "job_id": job_id, "removed": removed, "cleanup_state": "completed"}

    def retention_sweep(self, *, retention_days: int = 7, limit: int = 200) -> dict:
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 0:
            raise ValueError("retention_days must be a non-negative whole number")
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cleaned = []
        for row in self.repository.list(limit=limit):
            if row["lifecycle"] not in {item.value for item in (
                Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
                Lifecycle.CANCELLED, Lifecycle.INTERRUPTED,
            )} or not row.get("finished_at"):
                continue
            try:
                finished = datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if finished <= cutoff and row.get("cleanup_state") != "completed":
                cleaned.append(self.cleanup(row["job_id"]))
        return {"ok": True, "retention_days": retention_days, "cleaned": cleaned}

    def submit_matrix(self, submissions: list[JobSubmission]) -> dict:
        if not submissions:
            raise ValueError("matrix requires at least one child submission")
        first = submissions[0]
        if any(item.target_kind != first.target_kind or item.remote_name != first.remote_name or
               item.project_root != first.project_root for item in submissions):
            raise ValueError("matrix children must share one target and project")
        parent = JobSubmission(
            kind="matrix", project_root=first.project_root,
            project_identity=first.project_identity, target_kind=first.target_kind,
            remote_name=first.remote_name, workspace_label="matrix-parent",
            argv=("sandbox-matrix-parent",), deadline_seconds=max(item.deadline_seconds for item in submissions),
            source=first.source, workspace_mode="persistent", output_profile=first.output_profile,
            execution_profile=first.execution_profile, deadline_source=first.deadline_source,
        )
        parent_row, replay = self.repository.accept(parent)
        if replay:
            return self._get_parent(parent_row)
        self.storage.job_dir(parent_row["job_id"], create=True)
        accepted = []
        for submission in submissions:
            if submission.workspace_mode != "isolated":
                raise ValueError("matrix children require isolated workspaces")
            accepted.append(self.submit(replace(submission, parent_job_id=parent_row["job_id"])))
        return {"ok": True, "kind": "matrix", "parent_job_id": parent_row["job_id"],
                "children": accepted, "summary": {"submitted": len(accepted)}}
