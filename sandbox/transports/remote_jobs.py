"""Bounded control-plane transport for jobs hosted on a provisioned remote."""

from __future__ import annotations

import json
import base64
import hashlib
import inspect
import re
import shlex
import subprocess
from typing import Any, Callable

from sandbox.jobs.models import (normalize_output_page_bytes,
                                 normalize_output_wait_seconds, validate_ack_job_id)
from sandbox.services.redaction import redact_structure, redact_text, require_safe_argv


class RemoteJobTransportError(RuntimeError):
    """Bounded, retryable failure from a remote job control operation."""

    code = "remote_job_transport_error"
    retryable = True

    def to_payload(self, *, remote: str | None = None,
                   operation: str | None = None) -> dict:
        """Return a redacted envelope when a remote control call has no receipt."""
        allowed_operations = {
            "exec", "test", "job-output", "job-status", "job-list", "job-cancel",
            "job-retry", "job-cleanup", "job-artifacts", "job-artifact-get",
        }
        operation = operation if operation in allowed_operations else "job"
        return {
            "ok": False,
            "status": "unknown",
            "code": self.code,
            "error": _safe_remote_detail(str(self)) or "remote job transport failed",
            "target": _admission_target({"kind": "remote", "remote": remote}),
            "operation": operation,
            "retryable": bool(self.retryable),
            "acceptance": "unknown" if operation in {"exec", "test"} else None,
        }


_NETWORK_CAPACITY_CODES = frozenset({
    "docker_network_capacity_unavailable",
    "docker_network_subnet_exhausted",
    "network_allocation_conflict",
})
_SAFE_REMOTE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_ADMISSION_REASONS = frozenset({
    "ambiguous_pool_evidence",
    "inconsistent_capacity_totals",
    "inconsistent_pool_totals",
    "invalid_capacity_totals",
    "invalid_collision_evidence",
    "invalid_ownership_evidence",
    "invalid_pool_capacity",
    "invalid_pool_evidence",
    "missing_pool_evidence",
    "network_allocation_conflict",
    "network_ipam_unavailable",
    "ownership_does_not_cover_allocations",
    "probe_failed",
    "probe_incomplete",
    "probe_not_successful",
    "probe_output_ambiguous",
    "probe_output_unavailable",
})
_SAFE_ADMISSION_ID = re.compile(r"^pool-[0-9a-f]{16,64}$")
_MAX_ADMISSION_INTEGER = 10**18
_MAX_ADMISSION_POOLS = 32


def _admission_int(value: object) -> int | None:
    """Keep bounded non-negative counters from an untrusted decision."""
    if (isinstance(value, int) and not isinstance(value, bool)
            and 0 <= value <= _MAX_ADMISSION_INTEGER):
        return value
    return None


def _admission_reason(value: object, fallback: str | None = None) -> str | None:
    if isinstance(value, str) and value in _SAFE_ADMISSION_REASONS:
        return value
    return fallback


def _admission_opaque_id(value: object) -> str | None:
    """Retain evaluator IDs, replacing malformed values with no identifier."""
    if isinstance(value, str) and _SAFE_ADMISSION_ID.fullmatch(value):
        return value
    return None


def _admission_capacity(value: object) -> dict:
    """Whitelist the bounded capacity fields exposed by the policy evaluator."""
    if not isinstance(value, dict):
        return {"status": "unavailable", "usable_subnets": None}
    status = value.get("status")
    result = {
        "status": status if isinstance(status, str) and status in {
            "complete", "partial", "unavailable",
        }
        else "unavailable",
    }
    for field in ("total_subnets", "allocated_subnets", "usable_subnets",
                  "required_subnets"):
        if field in value:
            result[field] = _admission_int(value.get(field))
    # Every blocked decision produced by the evaluator carries this field;
    # retaining a null fallback makes malformed decisions fail closed without
    # exposing their original shape.
    result.setdefault("usable_subnets", None)
    pools = value.get("pools")
    if isinstance(pools, list):
        safe_pools = []
        for item in pools[:_MAX_ADMISSION_POOLS]:
            if not isinstance(item, dict):
                continue
            pool_id = _admission_opaque_id(item.get("pool_id"))
            if pool_id is None:
                continue
            safe_pool = {"pool_id": pool_id}
            for field in ("capacity_subnets", "allocated_subnets", "usable_subnets"):
                if field in item:
                    safe_pool[field] = _admission_int(item.get(field))
            safe_pools.append(safe_pool)
        if safe_pools:
            result["pools"] = safe_pools
    return result


def _admission_evidence(value: object) -> dict:
    """Whitelist evidence summaries; never forward probe output or paths."""
    if not isinstance(value, dict):
        return {"status": "unavailable"}
    status = value.get("status")
    result = {
        "status": status if isinstance(status, str) and status in {
            "complete", "partial", "unavailable",
        }
        else "unavailable",
    }
    if value.get("inventory") == "address_pools_and_network_ipam":
        result["inventory"] = "address_pools_and_network_ipam"
    reason = _admission_reason(value.get("reason"))
    if reason is not None:
        result["reason"] = reason
    collision_count = _admission_int(value.get("collision_count"))
    if collision_count is not None:
        result["collision_count"] = collision_count
    ownership = value.get("ownership")
    if isinstance(ownership, dict):
        safe_ownership = {}
        for owner in ("sandbox", "foreign", "unattributed"):
            field = f"{owner}_allocated_subnets"
            if field in ownership:
                safe_ownership[field] = _admission_int(ownership.get(field))
        if safe_ownership:
            result["ownership"] = safe_ownership
    return result


def _admission_target(value: object) -> dict:
    """Expose only an opaque remote name; no SSH, path, or host metadata."""
    remote = (
        value.get("remote")
        if isinstance(value, dict) and value.get("kind") == "remote"
        else None
    )
    if not (isinstance(remote, str) and _SAFE_REMOTE_NAME.fullmatch(remote)):
        remote = None
    return {"kind": "remote", "remote": remote}


class RemoteJobAdmissionError(RemoteJobTransportError):
    """A bounded public refusal raised before remote staging can begin."""

    _ERROR = "remote job submission blocked by Docker network capacity admission"
    _GUIDANCE = (
        "Review the bounded Docker address-pool plan and scoped Sandbox "
        "ownership evidence before retrying."
    )

    def __init__(self, decision: object) -> None:
        # Keep the source decision private.  Callers must use to_payload(),
        # whose closed whitelist prevents exception data from crossing an API
        # boundary by way of str(), repr(), or an accidental dict merge.
        if not isinstance(decision, dict):
            decision = getattr(decision, "decision", None)
        self._decision = decision if isinstance(decision, dict) else {}
        super().__init__(self._ERROR)

    def to_payload(self) -> dict:
        """Return the stable, redacted admission envelope."""
        decision = self._decision
        code = decision.get("code")
        if not isinstance(code, str) or code not in _NETWORK_CAPACITY_CODES:
            code = "docker_network_capacity_unavailable"
        target = _admission_target(decision.get("target"))
        recovery = {
            "automatic_cleanup": False,
            "automatic_retry": False,
            "plan": "reviewed_docker_network_capacity",
        }
        # Human guidance is only emitted for a validated opaque target. Never
        # interpolate or forward an arbitrary remote/command value.
        if target["remote"] is not None:
            recovery["guidance"] = self._GUIDANCE
        return {
            "ok": False,
            "status": "blocked",
            "code": code,
            "error": self._ERROR,
            "resource_class": "docker_user_defined_network_subnet",
            "resource_kind": "network",
            "owner_classes": ["sandbox", "foreign", "unattributed"],
            "target": target,
            "capacity": _admission_capacity(decision.get("capacity")),
            "evidence": _admission_evidence(decision.get("evidence")),
            "recovery": recovery,
            "retryable": False,
            "side_effects": {
                "staging_started": False,
                "network_allocation_started": False,
            },
        }


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
    """Return a bounded controller diagnostic without echoing retained output.

    A failed control command can still put a valid-looking job page on stdout
    before the transport notices truncation or a non-zero exit.  Treat stdout
    as data, not an error channel: surfacing its tail can disclose retained
    job logs and makes a malformed page look like a usable diagnostic.  The
    controller's stderr remains a bounded diagnostic after redaction.
    """
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or error.get("code")
            if isinstance(message, str) and message.strip():
                return _safe_remote_detail(message)
        elif isinstance(error, str) and error.strip():
            return _safe_remote_detail(error)
    detail = getattr(result, "stderr", "")
    if isinstance(detail, str) and detail.strip():
        safe = _safe_remote_detail(detail)
        if safe:
            return safe
    return f"remote exit code {getattr(result, 'returncode', 1)}"


def _require_submission_ack(payload: object, *, aggregate: bool = False,
                            expected_policy: dict | list[dict] | None = None) -> dict:
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
    if expected_policy is not None:
        if aggregate:
            children = payload.get("children", ())
            expected = expected_policy if isinstance(expected_policy, list) else [expected_policy] * len(children)
            if len(expected) != len(children) or any(
                    child.get("execution_policy") != policy
                    for child, policy in zip(children, expected)):
                raise ValueError("remote acceptance acknowledgement is missing exact execution policy")
        elif payload.get("execution_policy") != expected_policy:
            raise ValueError("remote acceptance acknowledgement is missing exact execution policy")
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
        self._deploy_accepts_push_timeout = self._accepts_keyword(
            deploy, "push_timeout"
        )
        # The VPS runtime is staged under SANDBOX_HOME; its CLI is not
        # necessarily on PATH.  Keep the path policy injected by the remote
        # adapter so this transport remains runtime-neutral and testable.
        self.remote_sb_path = remote_sb_path or (lambda _remote: "sb")

    @staticmethod
    def _accepts_keyword(callable_obj: Callable, name: str) -> bool:
        try:
            parameters = inspect.signature(callable_obj).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parameter.name == name
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

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
        capabilities = remote.get("capabilities")
        required = {"job.exec", "job.execution-policy.v1"}
        if not isinstance(capabilities, (list, tuple, set)) or not required.issubset(capabilities):
            raise RemoteJobTransportError(
                "remote does not support job.execution-policy.v1; reprovision or update the remote before submitting jobs")
        return remote

    def _deploy(self, remote: dict, project_root: str,
                *, deployment_timeout: int | None = None) -> dict:
        """Deploy once, translating only the policy admission refusal."""
        # Keep this import lazy: the core remote adapter imports this transport
        # for workspace helpers, so importing it at module load would create a
        # cycle in callers that only need the local transport contract.
        from sandbox.core._remote import (
            NetworkCapacityAdmissionError,
            RemotePushTimeout,
            remote_push_timeout_for_deadline,
        )

        caught = False
        timed_out = False
        timeout_seconds = None
        decision = None
        deploy_kwargs = {}
        if deployment_timeout is not None and self._deploy_accepts_push_timeout:
            deploy_kwargs["push_timeout"] = remote_push_timeout_for_deadline(
                deployment_timeout
            )
        try:
            deployed = self.deploy(remote, project_root, **deploy_kwargs)
        except NetworkCapacityAdmissionError as exc:
            caught = True
            decision = getattr(exc, "decision", None)
        except subprocess.TimeoutExpired:
            # A source push can time out before the durable job exists. Keep
            # the transport boundary typed and actionable instead of leaking
            # the subprocess command (which may contain a private path) as a
            # raw traceback to CLI/MCP callers.
            timed_out = True
        except RemotePushTimeout as exc:
            timed_out = True
            timeout_seconds = exc.timeout_seconds
        if timed_out:
            # Raise after leaving the handler so the private subprocess
            # exception is not retained as the public exception context.
            suffix = (
                f" after {timeout_seconds} seconds"
                if timeout_seconds is not None else ""
            )
            raise RemoteJobTransportError(
                "remote source deployment timed out"
                f"{suffix} before job acceptance; "
                "retry with --local or inspect the remote deployment state before replaying"
            ) from None
        if caught:
            # Raising outside the except block keeps both __cause__ and
            # __context__ empty while still making the translation explicit.
            raise RemoteJobAdmissionError(decision) from None
        return deployed

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
        deployed = self._deploy(
            remote, submission.project_root,
            deployment_timeout=submission.deadline_seconds,
        )
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
        deployed = self._deploy(
            remote, first.project_root,
            deployment_timeout=max(item.deadline_seconds for item in submissions),
        )
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
                 "execution_policy": {
                     "execution_profile": item.execution_profile,
                     "deadline_seconds": item.deadline_seconds,
                     "deadline_source": item.deadline_source,
                     "deadline_reminder": item.deadline_reminder,
                     "stall_seconds": item.stall_seconds,
                     "cancel_grace_seconds": item.cancel_grace_seconds,
                     "cancel_on_stall": item.cancel_on_stall,
                     "cleanup_policy": item.cleanup_policy,
                     "provenance": dict(item.execution_policy_provenance or {}),
                 },
                 "environment_keys": list(item.environment_keys),
                 "request_id": item.request_id, "cleanup_policy": item.cleanup_policy,
                 "execution_policy_provenance": dict(item.execution_policy_provenance or {}),
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
            _require_submission_ack({**payload, "status": "accepted"}, aggregate=True,
                                    expected_policy=[{
                                        "profile": item.execution_profile,
                                        "deadline_seconds": item.deadline_seconds,
                                        "deadline_source": item.deadline_source,
                                        "deadline_reminder": item.deadline_reminder,
                                        "stall_seconds": item.stall_seconds,
                                        "cancel_grace_seconds": item.cancel_grace_seconds,
                                        "cancel_on_stall": item.cancel_on_stall,
                                        "cleanup_policy": item.cleanup_policy,
                                        "provenance": dict(item.execution_policy_provenance or {}),
                                    } for item in submissions])
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
        policy = {
            "execution_profile": submission.execution_profile,
            "deadline_seconds": submission.deadline_seconds,
            "deadline_source": submission.deadline_source,
            "deadline_reminder": submission.deadline_reminder,
            "stall_seconds": submission.stall_seconds,
            "cancel_grace_seconds": submission.cancel_grace_seconds,
            "cancel_on_stall": submission.cancel_on_stall,
            "cleanup_policy": submission.cleanup_policy,
            "provenance": dict(submission.execution_policy_provenance or {}),
        }
        encoded_policy = base64.b64encode(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).decode()
        args += ["--execution-policy-json", encoded_policy]
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
            _require_submission_ack(payload, expected_policy={
                "profile": submission.execution_profile,
                "deadline_seconds": submission.deadline_seconds,
                "deadline_source": submission.deadline_source,
                "deadline_reminder": submission.deadline_reminder,
                "stall_seconds": submission.stall_seconds,
                "cancel_grace_seconds": submission.cancel_grace_seconds,
                "cancel_on_stall": submission.cancel_on_stall,
                "cleanup_policy": submission.cleanup_policy,
                "provenance": dict(submission.execution_policy_provenance or {}),
            })
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
        max_bytes = normalize_output_page_bytes(max_bytes)
        wait_seconds = normalize_output_wait_seconds(wait_seconds)
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

    def status(self, remote_name: str, job_id: str, *, timeout: int = 25) -> dict:
        try:
            result = self.control(remote_name, ["job-status", job_id], timeout=timeout)
            result["target"] = {"kind": "remote", "remote": remote_name,
                                "workspace": result.get("workspace_label")}
            return result
        except RemoteJobTransportError as exc:
            # Preserve the legacy lifecycle/health fields while carrying the
            # bounded typed envelope through status callers. This distinguishes
            # an unobservable job from terminal success without exposing raw
            # SSH/controller diagnostics.
            payload = exc.to_payload(remote=remote_name, operation="job-status")
            payload.update({
                "job_id": job_id,
                "lifecycle": "unknown",
                "health": "unreachable",
            })
            return payload

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
