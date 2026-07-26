"""Bounded control-plane transport for jobs hosted on a provisioned remote."""

from __future__ import annotations

import json
import base64
import hashlib
import re
import shlex
from typing import Any, Callable


class RemoteJobTransportError(RuntimeError):
    pass


_REMOTE_SECRET = re.compile(
    r"(?i)\b(bearer|token|password|secret|api[_-]?key|authorization)\b(?:\s*[:=]\s*|\s+)[^\s]+"
)
_URL_USERINFO = re.compile(r"\b(https?://)[^\s/@:]+:[^\s/@]+@")


def _safe_remote_detail(value: object, *, limit: int = 512) -> str:
    """Return bounded controller diagnostics without forwarding credentials."""
    if not isinstance(value, str):
        return ""
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", value.strip())
    text = _REMOTE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text[-limit:]


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

    def _execution_remote(self, name: str) -> dict:
        """Resolve a provisioned execution target before any deployment side effect."""
        remote = self.remote_lookup(name)
        if not isinstance(remote, dict) or not remote.get("provisioned"):
            raise RemoteJobTransportError("remote is not provisioned")
        # Older hand-written remote records may predate capability metadata;
        # preserve that compatibility while refusing an explicitly constrained
        # remote before staging source or starting a job.
        capabilities = remote.get("capabilities")
        if capabilities is not None and "job.exec" not in capabilities:
            raise RemoteJobTransportError("remote does not support job.exec")
        return remote

    def submit(self, submission) -> dict:
        if submission.target_kind != "remote" or not submission.remote_name:
            raise RemoteJobTransportError("remote transport requires a remote submission")
        remote = self._execution_remote(submission.remote_name)
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
        remote = self._execution_remote(first.remote_name)
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
                 "cwd_relative": item.cwd_relative, "execution_profile": item.execution_profile,
                 "output_profile": item.output_profile, "deadline_source": item.deadline_source,
                 "stall_seconds": item.stall_seconds, "cancel_on_stall": item.cancel_on_stall,
                 "environment_keys": list(item.environment_keys),
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
            detail = ""
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    detail = _safe_remote_detail(error["message"])
            if not detail:
                stderr = getattr(result, "stderr", "")
                if isinstance(stderr, str) and stderr.strip():
                    detail = _safe_remote_detail(stderr)
                else:
                    detail = f"remote exit code {getattr(result, 'returncode', 1)}"
            raise RemoteJobTransportError(f"remote matrix acceptance failed: {detail}")
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
        # container root into its bind-mounted checkout. Keep the workspace
        # directory itself: an already-created reusable Compose container has
        # that directory as a bind mount, and deleting/recreating it makes a
        # later ``docker compose exec`` reject its working directory as outside
        # the mount namespace. Replace contents in place instead. First use
        # normal unprivileged cleanup; only when that fails, use a narrowly
        # mounted disposable root cleaner for this deterministic workspace.
        clean_contents = shlex.join([
            "find", workspace_path, "-mindepth", "1", "-maxdepth", "1",
            "-exec", "rm", "-rf", "--", "{}", "+",
        ])
        root_clean = shlex.join([
            "docker", "run", "--rm", "--user", "0:0", "--volume",
            f"{workspace_path}:/workspace", "alpine:3.20", "sh", "-c",
            "find /workspace -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +",
        ])
        command = (
            shlex.join(["mkdir", "-p", workspace_path]) + " && "
            f"if ! {clean_contents} 2>/dev/null; then {root_clean} && {clean_contents}; fi && "
            + shlex.join(
            ["cp", "-a", f"{source_path}/.", workspace_path])
        )
        result = self.ssh_run(remote, command, timeout=120)
        if getattr(result, "returncode", 1) != 0:
            detail = "\n".join(part.strip() for part in (
                getattr(result, "stderr", ""), getattr(result, "stdout", ""),
            ) if part.strip())
            detail = _safe_remote_detail(detail, limit=4096)
            raise RemoteJobTransportError(
                "remote workspace preparation failed" + (f": {detail}" if detail else ""))
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
                shlex.join([sb, "exec", "--in-instance", "--timeout",
                            str(submission.deadline_seconds), "--", *argv]),
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
                "deadline": payload.get("deadline", {"seconds": submission.deadline_seconds,
                                                       "source": submission.deadline_source}),
                "workspace_path": workspace_path}

    def read_output(self, remote_name: str, job_id: str, *, stream: str = "combined",
                    cursor: str | None = None, offset: int | None = None,
                    tail_bytes: int | None = None, lines: int | None = None,
                    since: str | None = None,
                    max_bytes: int = 65536, wait_seconds: int = 0,
                    encoding: str = "utf8", profile: str = "full") -> dict:
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict):
            raise RemoteJobTransportError("unknown remote")
        args = ["job-output", job_id, "--stream", stream,
                "--max-bytes", str(max_bytes), "--encoding", encoding]
        # The default full page is understood by controllers that predate
        # declarative presentation profiles. Omit it for reconnectability
        # across an independently provisioned controller; named profiles are
        # still explicit and require a current controller.
        if profile != "full":
            args += ["--profile", profile]
        args.append("--json")
        if cursor:
            args += ["--cursor", cursor]
        if offset is not None:
            args += ["--offset", str(offset)]
        if tail_bytes is not None:
            args += ["--tail-bytes", str(tail_bytes)]
        if lines is not None:
            args += ["--lines", str(lines)]
        if since is not None:
            args += ["--since", since]
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
        args = ["job-cleanup", job_id, "--yes"]
        for flag, enabled in (("--logs", logs), ("--artifacts", artifacts), ("--metrics", metrics)):
            if enabled: args.append(flag)
        return self.control(remote_name, args)
