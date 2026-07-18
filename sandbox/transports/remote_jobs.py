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

    def read_output(self, remote_name: str, job_id: str, *, cursor: str | None = None,
                    max_bytes: int = 65536) -> dict:
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict):
            raise RemoteJobTransportError("unknown remote")
        args = ["sb", "job-output", job_id, "--max-bytes", str(max_bytes), "--json"]
        if cursor:
            args += ["--cursor", cursor]
        result = self.ssh_run(remote, shlex.join(args), timeout=25)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload:
            raise RemoteJobTransportError("remote output read failed")
        return payload
