"""Bounded control-plane transport for jobs hosted on a provisioned remote."""

from __future__ import annotations

import json
import base64
import hashlib
import shlex
from typing import Any, Callable

from sandbox.jobs.models import validate_ack_job_id
from sandbox.services.redaction import redact_structure, redact_text, require_safe_argv


class RemoteJobTransportError(RuntimeError):
    pass


_MAX_REMOTE_JSON_BYTES = 1_048_576


def _safe_remote_detail(value: object, *, limit: int = 512) -> str:
    """Return bounded controller diagnostics without forwarding credentials."""
    if not isinstance(value, str):
        return ""
    return redact_text(value.strip())[-limit:]


def _last_json(text: str) -> dict | None:
    if not isinstance(text, str) or len(text.encode("utf-8", errors="replace")) > _MAX_REMOTE_JSON_BYTES:
        return None
    for line in reversed((text or "").splitlines()):
        if len(line.encode("utf-8", errors="replace")) > _MAX_REMOTE_JSON_BYTES:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            sanitized = redact_structure(value)
            return sanitized if isinstance(sanitized, dict) else None
    return None


def _error_detail(payload: dict | None, result: object) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or error.get("code")
            if isinstance(message, str) and message.strip():
                return _safe_remote_detail(message)
        elif isinstance(error, str) and error.strip():
            return _safe_remote_detail(error)
    for field in ("stderr", "stdout"):
        detail = getattr(result, field, "")
        if isinstance(detail, str) and detail.strip():
            safe = _safe_remote_detail(detail)
            if safe:
                return safe
    return f"remote exit code {getattr(result, 'returncode', 1)}"


def _require_submission_ack(payload: object, *, aggregate: bool = False) -> dict:
    """Require an explicit accepted acknowledgement with a durable identity."""
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("remote acceptance acknowledgement is not successful")
    if aggregate:
        validate_ack_job_id(payload.get("parent_job_id"), label="parent job id")
        children = payload.get("children", ())
        if not isinstance(children, list):
            raise ValueError("remote acceptance acknowledgement has invalid children")
        for child in children:
            if not isinstance(child, dict):
                raise ValueError("remote acceptance acknowledgement has invalid child")
            validate_ack_job_id(child.get("job_id"), label="child job id")
    else:
        validate_ack_job_id(payload.get("job_id"))
    status = payload.get("status")
    if status != "accepted":
        raise ValueError("remote acceptance acknowledgement is missing status=accepted")
    return payload


def _decode_job_page(payload: object) -> dict:
    """Decode the feature-owned top-level list envelope exactly once."""
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("job-list response must be a top-level ok object")
    if "data" in payload:
        raise ValueError("job-list response must expose top-level jobs")
    rows = payload.get("jobs")
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        raise ValueError("job-list response jobs must be a top-level list of objects")
    return payload


def workspace_refresh_command(source_path: str, workspace_path: str) -> str:
    """Refresh a remote copy without invalidating existing bind-mount inodes."""
    root_script = (
        "find /workspace -mindepth 2 -maxdepth 2 -exec rm -rf -- {} +; "
        "find /workspace -mindepth 1 -maxdepth 1 ! -type d -exec rm -f -- {} +"
    )
    root_clean = (
        'docker run --rm --user 0:0 --volume "$workspace:/workspace" '
        f"alpine:3.20 sh -c {shlex.quote(root_script)}"
    )
    top_level_items = '"$workspace"/* "$workspace"/.[!.]* "$workspace"/..?*'
    clean_contents = (
        f"for item in {top_level_items}; do "
        'if [ ! -e "$item" ] && [ ! -L "$item" ]; then continue; fi; '
        'if [ -d "$item" ] && [ ! -L "$item" ]; then '
        'find "$item" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; '
        'else rm -f -- "$item"; fi; done'
    )
    remaining_contents = (
        'find "$workspace" -mindepth 2 -print -quit; '
        'find "$workspace" -mindepth 1 -maxdepth 1 ! -type d -print -quit'
    )
    prune_stale_dirs = (
        f"for item in {top_level_items}; do "
        'if [ ! -e "$item" ] && [ ! -L "$item" ]; then continue; fi; '
        'if [ -d "$item" ] && [ ! -L "$item" ]; then name=${item##*/}; '
        'if [ ! -d "$source/$name" ] || [ -L "$source/$name" ]; then '
        'rmdir -- "$item"; fi; fi; done'
    )
    return (
        f"workspace={shlex.quote(workspace_path)}; source={shlex.quote(source_path)}; "
        'mkdir -p "$workspace" && '
        f"{clean_contents} 2>/dev/null || true; "
        f'if [ -n "$({remaining_contents})" ]; then {root_clean}; fi && '
        f"{prune_stale_dirs} && "
        f'if [ -n "$({remaining_contents})" ]; then '
        "echo 'remote workspace cleanup left contents' >&2; exit 1; fi && "
        'cp -a "$source/." "$workspace"'
    )


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

    def _run(self, remote: dict, command: str, *, timeout: int):
        """Sever arbitrary runner failures from the public transport error."""
        failed = False
        try:
            result = self.ssh_run(remote, command, timeout=timeout)
        except Exception:
            # Raise after leaving the handler so the raw exception is neither
            # the cause nor context of the bounded public error.
            failed = True
            result = None
        if failed:
            raise RemoteJobTransportError("remote job transport runner failed") from None
        return result

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
        try:
            require_safe_argv(submission.argv)
        except ValueError:
            raise RemoteJobTransportError(
                "remote job command contains credential-like material"
            ) from None
        remote = self._execution_remote(submission.remote_name)
        deployed = self.deploy(remote, submission.project_root)
        self._validate_deployment(deployed)
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
        for item in submissions:
            try:
                require_safe_argv(item.argv)
            except ValueError:
                raise RemoteJobTransportError(
                    "remote job command contains credential-like material"
                ) from None
        first = submissions[0]
        if (first.target_kind != "remote" or not first.remote_name or
                any(item.target_kind != "remote" or item.remote_name != first.remote_name or
                    item.project_root != first.project_root for item in submissions)):
            raise RemoteJobTransportError("remote matrix children must share one remote and project")
        remote = self._execution_remote(first.remote_name)
        deployed = self.deploy(remote, first.project_root)
        self._validate_deployment(deployed)
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
                 "project_identity": item.project_identity,
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
                "--project-identity", first.project_identity,
                "--timeout", str(max(item.deadline_seconds for item in submissions)),
                "--output-profile", first.output_profile, "--spec-json", encoded, "--json"]
        result = self._run(remote, self._remote_command(remote, args), timeout=30)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload or payload.get("ok") is not True:
            raise RemoteJobTransportError(
                f"remote matrix acceptance failed: {_error_detail(payload, result)}")
        # Matrix controllers predating the explicit status field remain readable;
        # identity and successful acknowledgement are still mandatory. New
        # controllers include status=accepted, just like job-start.
        try:
            if payload.get("status") is not None and payload.get("status") != "accepted":
                raise ValueError("remote acceptance acknowledgement is missing status=accepted")
            _require_submission_ack({**payload, "status": "accepted"}, aggregate=True)
        except ValueError as exc:
            raise RemoteJobTransportError(f"remote matrix acceptance failed: {exc}") from exc
        return {**payload, "target": {"kind": "remote", "remote": first.remote_name,
                                        "workspace": first.workspace_label},
                "source": {"identity": deployed["identity"], "commit": deployed.get("commit"),
                 "dirty": bool(deployed.get("dirty")), "dirty_digest": deployed.get("dirty_digest")},
                "workspace_path": deployed["target_path"]}

    def _prepare_workspace(self, remote: dict, source_path: str, label: str) -> str:
        suffix = hashlib.sha256(label.encode()).hexdigest()[:14]
        # Project resolution derives a slug from the deployed workspace name;
        # use hyphens so the copied path remains a valid project root.
        workspace_path = f"{source_path}-workspace-{suffix}"
        # Preserve top-level directory inodes already used by nested Compose
        # bind mounts while replacing their contents and pruning stale dirs.
        command = workspace_refresh_command(source_path, workspace_path)
        result = self._run(remote, command, timeout=120)
        if getattr(result, "returncode", 1) != 0:
            detail = "\n".join(part.strip() for part in (
                getattr(result, "stderr", ""), getattr(result, "stdout", ""),
            ) if part.strip())
            detail = _safe_remote_detail(detail, limit=4096)
            raise RemoteJobTransportError(
                "remote workspace preparation failed" + (f": {detail}" if detail else ""))
        return workspace_path

    @staticmethod
    def _validate_deployment(deployed: object) -> None:
        if not isinstance(deployed, dict):
            raise RemoteJobTransportError("remote deployment did not return checkout metadata")
        for key in ("target_path", "identity"):
            value = deployed.get(key)
            if not isinstance(value, str) or not value.strip():
                raise RemoteJobTransportError(f"remote deployment metadata is missing {key}")

    def _submit_deployed(self, remote: dict, deployed: dict, submission) -> dict:
        # Stable request ID lets the remote durable repository replay an uncertain
        # SSH submission safely after a control-plane timeout.
        workspace_path = self._prepare_workspace(remote, deployed["target_path"], submission.workspace_label)
        args = ["job-start", "--local", "--project-dir", workspace_path,
                "--project-identity", submission.project_identity,
                "--workspace", submission.workspace_label, "--timeout", str(submission.deadline_seconds),
                "--cwd-relative", submission.cwd_relative,
                "--output-profile", submission.output_profile, "--profile", submission.execution_profile,
                "--source-identity", deployed["identity"]]
        if submission.stall_seconds != 300:
            args += ["--stall-seconds", str(submission.stall_seconds)]
        if submission.cancel_on_stall:
            args.append("--cancel-on-stall")
        # The deployed checkout is the source of truth for detached execution.
        # Never let a caller's pre-deploy metadata overwrite the exact tree
        # identity that was just staged on the controller.
        if deployed.get("commit") is not None:
            args += ["--source-commit", str(deployed["commit"])]
        if deployed.get("dirty_digest") is not None:
            args += ["--source-dirty-digest", str(deployed["dirty_digest"])]
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
                # This controller already runs on the selected VPS. Explicitly
                # select that host's local runtime so a remote-first project
                # policy cannot recursively submit to the same named remote.
                shlex.join([sb, "exec", "--local", "--in-instance", "--timeout",
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
        result = self._run(remote, self._remote_command(remote, args), timeout=30)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload or payload.get("ok") is not True:
            raise RemoteJobTransportError(
                f"remote job acceptance failed: {_error_detail(payload, result)}")
        try:
            _require_submission_ack(payload)
        except ValueError as exc:
            raise RemoteJobTransportError(f"remote job acceptance failed: {exc}") from exc
        return {**payload, "target": {"kind": "remote", "remote": submission.remote_name,
                                        "workspace": submission.workspace_label},
                "source": {"identity": deployed["identity"], "commit": deployed.get("commit"),
                 "dirty": bool(deployed.get("dirty")), "dirty_digest": deployed.get("dirty_digest")},
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
        result = self._run(remote, self._remote_command(remote, args), timeout=25)
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload or payload.get("ok") is not True:
            raise RemoteJobTransportError(
                f"remote output read failed: {_error_detail(payload, result)}")
        return payload

    def control(self, remote_name: str, argv: list[str], *, timeout: int = 25) -> dict:
        """Invoke a bounded JSON-only remote job control operation."""
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict) or not remote.get("provisioned"):
            raise RemoteJobTransportError("unknown or unprovisioned remote")
        result = self._run(
            remote, self._remote_command(remote, [*argv, "--json"]), timeout=timeout,
        )
        payload = _last_json(getattr(result, "stdout", ""))
        if getattr(result, "returncode", 1) != 0 or not payload or payload.get("ok") is not True:
            raise RemoteJobTransportError(
                f"remote job control operation failed: {_error_detail(payload, result)}")
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
             project_identity: str | None = None, workspace: str | None = None,
             active_only: bool = False, lifecycle: str | None = None,
             kind: str | None = None, cursor_job_id: str | None = None) -> dict:
        args = ["job-list", "--limit", str(limit)]
        # `job-list` is already running on the selected controller. Passing
        # `--local` made the remote parser reject the request, while a client
        # checkout path is not meaningful on that host. Use the canonical
        # project identity filter instead; callers may supply a resolved identity
        # directly or retain the path-derived identity for local parity.
        if project_identity:
            args += ["--project-identity", project_identity]
        elif project_dir:
            import hashlib
            from pathlib import Path
            identity = hashlib.sha256(
                str(Path(project_dir).expanduser().resolve()).encode()).hexdigest()
            args += ["--project-identity", identity]
        if workspace:
            args += ["--workspace", workspace]
        if active_only:
            args.append("--active-only")
        if lifecycle:
            args += ["--lifecycle", lifecycle]
        category = kind
        if category:
            args += ["--kind", category]
        if cursor_job_id:
            args += ["--cursor-job-id", cursor_job_id]
        try:
            return _decode_job_page(self.control(remote_name, args))
        except ValueError as exc:
            raise RemoteJobTransportError(str(exc)) from exc

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
