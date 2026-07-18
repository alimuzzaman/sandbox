"""Bounded control-plane transport for jobs hosted on a provisioned remote."""

from __future__ import annotations

import json
import base64
import hashlib
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

    def __init__(self, *, deploy: Callable, ssh_run: Callable, remote_lookup: Callable,
                 remote_sb_path: Callable | None = None) -> None:
        self.deploy = deploy
        self.ssh_run = ssh_run
        self.remote_lookup = remote_lookup
        # The VPS runtime is staged under SANDBOX_HOME; its CLI is not
        # necessarily on PATH.  Keep the path policy injected by the remote
        # adapter so this transport remains runtime-neutral and testable.
        self.remote_sb_path = remote_sb_path or (lambda _remote: "sb")

    def _remote_command(self, remote: dict, argv: list[str]) -> str:
        return shlex.join([self.remote_sb_path(remote), *argv])

    def submit(self, submission) -> dict:
        if submission.target_kind != "remote" or not submission.remote_name:
            raise RemoteJobTransportError("remote transport requires a remote submission")
        remote = self.remote_lookup(submission.remote_name)
        if not isinstance(remote, dict) or not remote.get("provisioned"):
            raise RemoteJobTransportError("remote is not provisioned")
        deployed = self.deploy(remote, submission.project_root)
        return self._submit_deployed(remote, deployed, submission)

    def submit_many(self, submissions: list) -> dict:
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
        plan = []
        for item in submissions:
            workspace_path = self._prepare_workspace(remote, deployed["target_path"], item.workspace_label)
            argv = list(item.argv)
            # Matrix coordinators execute their explicit child argv on the
            # VPS. Match single-job submission and bind nested Sandbox CLI
            # invocations to the staged runtime rather than the host PATH.
            if argv[:1] == ["sb"]:
                argv[0] = self.remote_sb_path(remote)
            plan.append({"kind": item.kind, "workspace": item.workspace_label, "project_dir": workspace_path,
                 "argv": argv,
                 "timeout": item.deadline_seconds, "workspace_mode": item.workspace_mode,
                 "output_profile": item.output_profile, "deadline_source": item.deadline_source,
                 "request_id": item.request_id, "cleanup_policy": item.cleanup_policy,
                 "depends_on": list(item.depends_on), "failure_policy": item.failure_policy,
                 "compatibility_differences": list(item.compatibility_differences),
                 "artifact_paths": list(item.artifact_paths),
                 "source": {"identity": deployed["identity"], "commit": deployed.get("commit"),
                            "dirty_digest": deployed.get("dirty_digest")}})
        encoded = base64.b64encode(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).decode()
        args = ["job-matrix", "--local", "--project-dir", deployed["target_path"],
                "--timeout", str(max(item.deadline_seconds for item in submissions)),
                "--output-profile", first.output_profile, "--spec-json", encoded, "--json"]
        result = self.ssh_run(remote, self._remote_command(remote, args), timeout=30)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload or not payload.get("ok"):
            raise RemoteJobTransportError("remote matrix acceptance failed")
        return {**payload, "target": {"kind": "remote", "remote": first.remote_name,
                                        "workspace": first.workspace_label},
                "source": {"identity": deployed["identity"], "commit": deployed["commit"],
                 "dirty": deployed["dirty"], "dirty_digest": deployed["dirty_digest"]},
                "workspace_path": deployed["target_path"]}

    def _prepare_workspace(self, remote: dict, source_path: str, label: str) -> str:
        suffix = hashlib.sha256(label.encode()).hexdigest()[:14]
        # Project resolution derives a slug from the deployed workspace name;
        # use hyphens so the copied path remains a valid project root.
        workspace_path = f"{source_path}-workspace-{suffix}"
        # A prior generic Compose run may have written dependency files as
        # container root into its bind-mounted checkout.  First use the normal
        # unprivileged cleanup; only when that fails, use a narrowly mounted
        # disposable root cleaner for this one deterministic workspace.  This
        # keeps persistent labels reusable without broad host deletion or
        # requiring the remote account to have passwordless sudo.
        clean = shlex.join(["rm", "-rf", workspace_path])
        root_clean = shlex.join([
            "docker", "run", "--rm", "--user", "0:0", "--volume",
            f"{workspace_path}:/workspace", "alpine:3.20", "sh", "-c",
            "find /workspace -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +",
        ])
        command = (
            f"if [ -e {shlex.quote(workspace_path)} ] && ! {clean} 2>/dev/null; then "
            f"{root_clean} && {clean}; fi && "
            + shlex.join(
            ["mkdir", "-p", workspace_path]) + " && " + shlex.join(
            ["cp", "-a", f"{source_path}/.", workspace_path])
        )
        result = self.ssh_run(remote, command, timeout=120)
        if getattr(result, "returncode", 1) != 0:
            raise RemoteJobTransportError("remote workspace preparation failed")
        return workspace_path

    def _submit_deployed(self, remote: dict, deployed: dict, submission) -> dict:
        # Stable request ID lets the remote durable repository replay an uncertain
        # SSH submission safely after a control-plane timeout.
        workspace_path = self._prepare_workspace(remote, deployed["target_path"], submission.workspace_label)
        args = ["job-start", "--local", "--project-dir", workspace_path,
                "--workspace", submission.workspace_label, "--timeout", str(submission.deadline_seconds),
                "--output-profile", submission.output_profile, "--source-identity", deployed["identity"]]
        if submission.request_id:
            args += ["--request-id", submission.request_id]
        argv = list(submission.argv)
        if submission.kind == "runtime-exec":
            # Generic Compose commands belong in the selected remote project
            # instance, not in the VPS host environment.  The outer durable
            # job owns all output and deadline handling while this controller
            # ensures the deployed instance and performs the explicit argv
            # execution in its declared public service.
            sb = self.remote_sb_path(remote)
            controller = " && ".join((
                f"cd {shlex.quote(workspace_path)}",
                # The deployed project can itself be remote-first.  This
                # controller is already running on its selected VPS, so it
                # must explicitly select the co-located runtime rather than
                # recursively submit another remote job from inside the
                # durable job supervisor.
                shlex.join([sb, "ensure", "--local", "--json"]),
                shlex.join([sb, "exec", "--in-instance", "--", *argv]),
            ))
            argv = ["sh", "-lc", controller]
        elif argv[:1] == ["sb"]:
            # Test, E2E, and compatibility coordinators deliberately invoke
            # the co-located CLI with an explicit local target.  The staged
            # runtime is not assumed to be on the VPS PATH.  They also need
            # an instance in their freshly deployed workspace before the
            # nested command can inspect or exercise the project.
            sb = self.remote_sb_path(remote)
            argv[0] = sb
            controller = " && ".join((
                f"cd {shlex.quote(workspace_path)}",
                shlex.join([sb, "ensure", "--local", "--json"]),
                shlex.join(argv),
            ))
            argv = ["sh", "-lc", controller]
        args += ["--json", "--", *argv]
        result = self.ssh_run(remote, self._remote_command(remote, args), timeout=30)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload or not payload.get("ok"):
            raise RemoteJobTransportError("remote job acceptance failed")
        return {**payload, "target": {"kind": "remote", "remote": submission.remote_name,
                                        "workspace": submission.workspace_label},
                "source": {"identity": deployed["identity"], "commit": deployed["commit"],
                 "dirty": deployed["dirty"], "dirty_digest": deployed["dirty_digest"]},
                "workspace_path": workspace_path}

    def read_output(self, remote_name: str, job_id: str, *, stream: str = "combined",
                    cursor: str | None = None, tail_bytes: int | None = None,
                    max_bytes: int = 65536, wait_seconds: int = 0,
                    encoding: str = "utf8") -> dict:
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict):
            raise RemoteJobTransportError("unknown remote")
        args = ["job-output", job_id, "--stream", stream,
                "--max-bytes", str(max_bytes), "--encoding", encoding, "--json"]
        if cursor:
            args += ["--cursor", cursor]
        if tail_bytes is not None:
            args += ["--tail-bytes", str(tail_bytes)]
        # The remote job-output command performs the bounded wait against its
        # retained output. SSH carries only the resulting page, never child IO.
        if wait_seconds:
            args += ["--wait-seconds", str(wait_seconds)]
        result = self.ssh_run(remote, self._remote_command(remote, args), timeout=25)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload:
            raise RemoteJobTransportError("remote output read failed")
        return payload

    def control(self, remote_name: str, argv: list[str], *, timeout: int = 25) -> dict:
        """Invoke a bounded JSON-only remote job control operation."""
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict) or not remote.get("provisioned"):
            raise RemoteJobTransportError("unknown or unprovisioned remote")
        result = self.ssh_run(remote, self._remote_command(remote, [*argv, "--json"]), timeout=timeout)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload:
            raise RemoteJobTransportError("remote job control operation failed")
        return payload

    def status(self, remote_name: str, job_id: str) -> dict:
        try:
            result = self.control(remote_name, ["job-status", job_id])
            result["target"] = {"kind": "remote", "remote": remote_name,
                                "workspace": result.get("workspace_label")}
            return result
        except RemoteJobTransportError as exc:
            return {"ok": False, "job_id": job_id, "lifecycle": "unknown",
                    "health": "unreachable", "target": {"kind": "remote", "remote": remote_name},
                    "error": str(exc)}

    def list(self, remote_name: str, *, limit: int = 50, project_dir: str | None = None,
             workspace: str | None = None, active_only: bool = False) -> dict:
        args = ["job-list", "--limit", str(limit)]
        if project_dir:
            args += ["--local", "--project-dir", project_dir]
        if workspace:
            args += ["--workspace", workspace]
        if active_only:
            args.append("--active-only")
        return self.control(remote_name, args)

    def cancel(self, remote_name: str, job_id: str, *, force: bool = False) -> dict:
        args = ["job-cancel", job_id]
        if force: args.append("--force")
        return self.control(remote_name, args)

    def metrics(self, remote_name: str, job_id: str, *, limit: int = 500) -> dict:
        return self.control(remote_name, ["job-metrics", job_id, "--limit", str(limit)])

    def artifacts(self, remote_name: str, job_id: str) -> dict:
        return self.control(remote_name, ["job-artifacts", job_id])

    def artifact_get(self, remote_name: str, job_id: str, artifact_id: str, *,
                     offset: int = 0, max_bytes: int = 1_048_576) -> dict:
        return self.control(remote_name, ["job-artifact-get", job_id, artifact_id,
                                          "--offset", str(offset), "--max-bytes", str(max_bytes)])

    def retry(self, remote_name: str, job_id: str, *, request_id: str | None = None) -> dict:
        args = ["job-retry", job_id]
        if request_id: args += ["--request-id", request_id]
        return self.control(remote_name, args)

    def cleanup(self, remote_name: str, job_id: str, *, logs: bool = True,
                artifacts: bool = True, metrics: bool = True) -> dict:
        args = ["job-cleanup", job_id]
        for flag, enabled in (("--logs", logs), ("--artifacts", artifacts), ("--metrics", metrics)):
            if enabled: args.append(flag)
        return self.control(remote_name, args)
