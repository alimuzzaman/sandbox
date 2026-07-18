"""Bounded control-plane transport for jobs hosted on a provisioned remote."""

from __future__ import annotations

import json
import shlex
from typing import Any, Callable


class RemoteJobTransportError(RuntimeError):
    pass


def _last_json(text: str) -> dict | None:
    for line in reversed((text or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


class RemoteJobTransport:
    """Deploy then exchange compact job-control JSON, never child stdio pipes."""

    def __init__(self, *, deploy: Callable, ssh_run: Callable, remote_lookup: Callable) -> None:
        self.deploy = deploy
        self.ssh_run = ssh_run
        self.remote_lookup = remote_lookup

    def submit(self, submission) -> dict:
        if submission.target_kind != "remote" or not submission.remote_name:
            raise RemoteJobTransportError("remote transport requires a remote submission")
        remote = self.remote_lookup(submission.remote_name)
        if not isinstance(remote, dict) or not remote.get("provisioned"):
            raise RemoteJobTransportError("remote is not provisioned")
        deployed = self.deploy(remote, submission.project_root)
        return self._submit_deployed(remote, deployed, submission)

    def submit_many(self, submissions: list) -> list[dict]:
        """Accept matrix children after one exact-tree deployment.

        A matrix is a control-plane fan-out, not a reason to rsync the same
        uncommitted tree repeatedly.  All children must deliberately target
        one provisioned remote and one project root; callers use separate
        batches for different remotes/projects.
        """
        if not submissions:
            return []
        first = submissions[0]
        if (first.target_kind != "remote" or not first.remote_name or
                any(item.target_kind != "remote" or item.remote_name != first.remote_name or
                    item.project_root != first.project_root for item in submissions)):
            raise RemoteJobTransportError("remote matrix children must share one remote and project")
        remote = self.remote_lookup(first.remote_name)
        if not isinstance(remote, dict) or not remote.get("provisioned"):
            raise RemoteJobTransportError("remote is not provisioned")
        deployed = self.deploy(remote, first.project_root)
        return [self._submit_deployed(remote, deployed, item) for item in submissions]

    def _submit_deployed(self, remote: dict, deployed: dict, submission) -> dict:
        # Stable request ID lets the remote durable repository replay an uncertain
        # SSH submission safely after a control-plane timeout.
        args = ["sb", "job-start", "--local", "--project-dir", deployed["target_path"],
                "--workspace", submission.workspace_label, "--timeout", str(submission.deadline_seconds),
                "--output-profile", submission.output_profile]
        if submission.request_id:
            args += ["--request-id", submission.request_id]
        args += ["--json", "--", *submission.argv]
        result = self.ssh_run(remote, shlex.join(args), timeout=30)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload or not payload.get("ok"):
            raise RemoteJobTransportError("remote job acceptance failed")
        return {**payload, "source": {"identity": deployed["identity"], "commit": deployed["commit"],
                 "dirty": deployed["dirty"], "dirty_digest": deployed["dirty_digest"]},
                "workspace_path": deployed["target_path"]}

    def read_output(self, remote_name: str, job_id: str, *, stream: str = "combined",
                    cursor: str | None = None, tail_bytes: int | None = None,
                    max_bytes: int = 65536, wait_seconds: int = 0) -> dict:
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict):
            raise RemoteJobTransportError("unknown remote")
        args = ["sb", "job-output", job_id, "--stream", stream,
                "--max-bytes", str(max_bytes), "--json"]
        if cursor:
            args += ["--cursor", cursor]
        if tail_bytes is not None:
            args += ["--tail-bytes", str(tail_bytes)]
        # The remote job-output command performs the bounded wait against its
        # retained output. SSH carries only the resulting page, never child IO.
        if wait_seconds:
            args += ["--wait-seconds", str(wait_seconds)]
        result = self.ssh_run(remote, shlex.join(args), timeout=25)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload:
            raise RemoteJobTransportError("remote output read failed")
        return payload

    def control(self, remote_name: str, argv: list[str], *, timeout: int = 25) -> dict:
        """Invoke a bounded JSON-only remote job control operation."""
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict) or not remote.get("provisioned"):
            raise RemoteJobTransportError("unknown or unprovisioned remote")
        result = self.ssh_run(remote, shlex.join(["sb", *argv, "--json"]), timeout=timeout)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload:
            raise RemoteJobTransportError("remote job control operation failed")
        return payload

    def status(self, remote_name: str, job_id: str) -> dict:
        return self.control(remote_name, ["job-status", job_id])

    def list(self, remote_name: str, *, limit: int = 50) -> dict:
        return self.control(remote_name, ["job-list", "--limit", str(limit)])

    def cancel(self, remote_name: str, job_id: str, *, force: bool = False) -> dict:
        args = ["job-cancel", job_id]
        if force: args.append("--force")
        return self.control(remote_name, args)

    def metrics(self, remote_name: str, job_id: str, *, limit: int = 500) -> dict:
        return self.control(remote_name, ["job-metrics", job_id, "--limit", str(limit)])
