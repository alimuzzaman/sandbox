from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
import re as _re

from dependencies import ToolDependencies
from sandbox.commands.jobs_runtime import _resolved_project_identity, _source_identity

SANDBOX_ROOT = None
_compose = _herd_host_env = _host_run = _is_herd = None
_project_instance = _require_project_capability = _resolve_instance = None
_safe_json = _wp_root = _wpcli = _core = None

# The CLI's Docker probe/launch budget is 15 seconds. Keep a slightly larger
# MCP-side envelope so interpreter startup and pipe teardown do not turn a
# bounded CLI refusal into an unbounded tool call. A timeout never proves that
# a detached launch did not cross the transport boundary.
_WP_CLI_ASYNC_ACCEPTANCE_TIMEOUT = 30.0
_WP_CLI_JOB_ID_RE = _re.compile(r"(?m)^\s*([a-f0-9]{16})\s*$")
_WP_CLI_STARTED_RE = _re.compile(r"started background job\s+([a-f0-9]{16})")


def register(server, dependencies: ToolDependencies) -> None:
    """Bind WordPress tools to explicit composition-root dependencies."""
    global SANDBOX_ROOT, _compose, _herd_host_env, _host_run, _is_herd
    global _project_instance, _require_project_capability, _resolve_instance
    global _safe_json, _wp_root, _wpcli, _core
    for name in (
        "sandbox_root", "core", "compose", "herd_host_env", "host_run", "is_herd",
        "project_instance", "require_project_capability", "resolve_instance",
        "safe_json", "wp_root", "wpcli",
    ):
        globals()[{"sandbox_root": "SANDBOX_ROOT", "core": "_core", "compose": "_compose",
                    "herd_host_env": "_herd_host_env", "host_run": "_host_run",
                    "is_herd": "_is_herd", "project_instance": "_project_instance",
                    "require_project_capability": "_require_project_capability",
                    "resolve_instance": "_resolve_instance", "safe_json": "_safe_json",
                    "wp_root": "_wp_root", "wpcli": "_wpcli"}[name]] = dependencies.require(name)
    for tool in (wp_cli, wp_exec, wp_rest, run_tests, wp_cli_async, wp_cli_job, wp_cli_job_kill):
        server.tool()(tool)


def _managed_execution_unavailable(project_dir: str, label: str | None, entry_path: str,
                                   argv: tuple[str, ...], timeout: int):
    """Dispatch managed-native MCP payloads through the isolation gateway."""
    try:
        from sandbox.application.context import execute_project, managed_native_project_selected
        from sandbox.runtimes.base import ExecutionRequest
        if not managed_native_project_selected(project_dir, label=label or "default"):
            return None
        request = ExecutionRequest(str(project_dir), label or "default", entry_path, argv, timeout)
        execution = execute_project({}, request)
    except Exception:
        return {"ok": False, "error": "managed execution request is invalid"}
    return {"ok": execution.ok, "state": execution.state,
            "returncode": execution.exit_code,
            "stdout": execution.data.get("stdout", ""),
            "stderr": execution.data.get("stderr", ""),
            "reason": execution.data.get("reason", {"code": "isolated_payload_failed"})}


def _remote_job_transport():
    """Build the remote test transport with the staged CLI path policy."""
    from sandbox.core import _remote
    from sandbox.transports.remote_jobs import RemoteJobTransport
    return RemoteJobTransport(deploy=_remote.deploy_exact_working_tree,
        ssh_run=_remote.ssh_run, remote_lookup=_remote.get_remote,
        remote_sb_path=_remote.remote_sb_path)


def _resolve_test_mode(project_dir: str, label: str | None, explicit: str | None) -> str:
    """Resolve mode before target/capability selection changes execution scope."""
    from sandbox.core._tests import resolve_test_mode

    # MCP registration always supplies the explicit composition-root dependency.
    # The fallback keeps this module independently importable in contract tests.
    config = _core().load_project_config(project_dir, label=label) if _core is not None else {}
    return resolve_test_mode(project_dir,
                             configured=config.get("tests", {}).get("suite", "auto"),
                             explicit=explicit)


def wp_cli(command: str, timeout: int = 60, *, project_dir: str, label: str | None = None) -> dict:
    """Run any wp-cli command. Pass the args after `wp` (e.g. 'plugin list').

    Note: this runs `wp <command>` directly. If you need shell features like
    `$(cat ...)`, pipes, or redirects, use wp_exec instead.

    project_dir: the plugin project to target (its instance must already exist —
    call ensure_instance first).
    """
    capability_error = _require_project_capability(project_dir, label, "wordpress.cli")
    if capability_error:
        return capability_error
    blocked = _managed_execution_unavailable(project_dir, label, "wordpress_cli",
                                             ("wp", *shlex.split(command)), timeout)
    if blocked: return blocked
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    return _wpcli(shlex.split(command), instance=inst, timeout=timeout)

def wp_exec(command: str, container: str = "wp", workdir: str | None = None,
            timeout: int = 120, *, project_dir: str, label: str | None = None) -> dict:
    """Run an arbitrary shell command inside a container (default `wp`).

    Use for composer, npm, node, php scripts, file ops, etc. Runs as the
    container's default user. Supports pipes, $(...) and redirects since
    it goes through `sh -c`.

    container: 'wp' (default), 'db', 'wpcli', or 'mailpit'.
    project_dir: the plugin project to target (call ensure_instance first).
    """
    capability_error = _require_project_capability(project_dir, label, "wordpress.exec")
    if capability_error:
        return capability_error
    blocked = _managed_execution_unavailable(project_dir, label, "exec",
                                             ("sh", "-c", command), timeout)
    if blocked: return blocked
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    if _is_herd(inst):
        # Host-served instance: there are no containers — run on the host,
        # defaulting cwd to the WP root (the `container` param is ignored).
        # PATH carries the pinned-PHP shims so bare `php`/`wp`/composer run on
        # the project's PHP version, not Herd's default.
        cwd = Path(workdir) if workdir else _wp_root(inst)
        return _host_run(["sh", "-c", command], timeout=timeout, cwd=cwd,
                         env=_herd_host_env(inst))
    args = ["exec"]
    if workdir:
        args += ["-w", workdir]
    args += ["-T", container, "sh", "-c", command]
    return _compose(*args, instance=inst, timeout=timeout)

def wp_rest(method: str, path: str, body: dict | None = None,
            query: dict | None = None, *, project_dir: str, label: str | None = None) -> dict:
    """Call the WordPress REST API.

    path: e.g. '/wp/v2/posts' (leading slash optional)
    Auth via Application Password — auto-provisioned when the instance installs.
    project_dir: the plugin project to target (call ensure_instance first).
    """
    capability_error = _require_project_capability(project_dir, label, "wordpress.rest")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    inst_cfg = _resolve_instance(inst)
    app_pw = inst_cfg["app_password"]
    if not app_pw:
        return {
            "ok": False,
            "error": f"no application_password for instance '{inst}'. "
                     f"Re-run ensure_instance(project_dir={project_dir!r}).",
        }
    port = inst_cfg["wordpress_port"]
    user = inst_cfg["admin"].get("user", "admin")
    base = f"http://localhost:{port}"
    url = f"{base}/wp-json{'/' if not path.startswith('/') else ''}{path}"
    try:
        with httpx.Client(auth=(user, app_pw), timeout=30.0) as c:
            r = c.request(method.upper(), url, params=query, json=body)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}

def run_tests(project_dir: str, phpunit_args: str = "",
             label: str | None = None, mode: str | None = None,
             local: bool = False,
             remote: str | None = None, workspace: str | None = None,
             timeout_seconds: int | None = None, output_profile: str | None = None,
             execution_profile: str | None = None, stall_seconds: int | None = None,
             cancel_grace_seconds: int | None = None,
             cancel_on_stall: bool | None = None, cleanup_policy: str | None = None) -> dict:
    """Run the plugin's PHPUnit tests in unit or integration mode.

    Integration mode uses the externally-provisioned WP test suite, polyfills,
    and isolated wp_tests DB. Unit mode uses project Composer dependencies and
    PHPUnit without the WordPress test harness or test database.

    project_dir: the plugin project. Local execution requires an existing
      instance; remote execution durably deploys the current tree first.
    phpunit_args: optional args passed through to phpunit (e.g. "--filter Foo"
      or a specific test file path).
    mode: optional `auto`, `unit`, or `integration` override.

    remote/workspace: optional configured remote and reusable workspace. A
      remote call accepts a detached job and returns its job_id; use job_status
      and job_output/job_follow for retained progress instead of streaming its
      child process through MCP.

    Returns {ok, passed, summary, output, mode}. Remote acceptance additionally
    returns job_id and lifecycle; `passed` is null until its durable job ends.
    """
    if mode is not None and mode not in {"auto", "unit", "integration"}:
        return {"ok": False, "passed": False, "summary": None,
                "output": "", "mode": None,
                "error": "test mode must be auto, unit, or integration"}
    try:
        resolved_mode = _resolve_test_mode(project_dir, label, mode)
    except (AttributeError, TypeError, ValueError, OSError) as exc:
        return {"ok": False, "passed": False, "summary": None,
                "output": "", "mode": None, "error": str(exc)}
    selected_remote = remote
    if not local and selected_remote is None and workspace is None:
        # A configured project-level remote is the default; an unconfigured or
        # non-Sandbox project keeps the historical local PHPUnit path.
        try:
            from sandbox.application.context import durable_job_dependencies
            from sandbox.jobs.models import TargetRequest
            auto_target = durable_job_dependencies()["target_service"].resolve(
                TargetRequest(project_dir=project_dir, required_capability="job.exec"))
            if auto_target.kind == "remote":
                selected_remote, workspace = auto_target.remote_name, auto_target.workspace_label
        except Exception:
            pass
    if not local and (selected_remote or workspace is not None):
        # Keep remote tests inside the shared detached runtime. The command
        # executes from the deployed project root, so `.` names the exact tree
        # sent by the deploy layer rather than the caller's local filesystem.
        from sandbox.application.context import durable_job_dependencies
        from sandbox.application.target_service import TargetResolutionError
        from sandbox.jobs.models import JobSubmission, TargetRequest
        from sandbox.transports.remote_jobs import RemoteJobAdmissionError
        from sandbox.config.runtime import normalize_runtime_policy, resolve_execution_policy
        try:
            dependencies = durable_job_dependencies()
            target = dependencies["target_service"].resolve(TargetRequest(
                project_dir=project_dir, remote=selected_remote, workspace=workspace,
                required_capability="job.exec"))
            if target.kind != "remote":
                raise ValueError("remote test target did not resolve to a remote")
            runtime = normalize_runtime_policy(getattr(target, "runtime_policy", None))
            policy = resolve_execution_policy(
                runtime, workspace=target.workspace_label,
                execution_profile=execution_profile, timeout_seconds=timeout_seconds,
                stall_seconds=stall_seconds, cancel_grace_seconds=cancel_grace_seconds,
                cancel_on_stall=cancel_on_stall, cleanup_policy=cleanup_policy,
            )
            workspace_policy = runtime["workspaces"].get(target.workspace_label, {})
            resolved_output = (output_profile if output_profile is not None else
                               workspace_policy.get("outputProfile") if workspace_policy.get("outputProfile") is not None
                               else runtime["outputProfile"])
            if resolved_output not in runtime["outputProfiles"]:
                raise ValueError("output profile is invalid")
            command = ["sb", "test", resolved_mode, "--local", "--project-dir", "."]
            if phpunit_args.strip():
                command += ["--", *shlex.split(phpunit_args)]
            accepted = _remote_job_transport().submit(JobSubmission(
                    "test", target.project_root, _resolved_project_identity(target), "remote",
                    target.workspace_label, tuple(command), policy.deadline_seconds, _source_identity(target.project_root),
                    remote_name=target.remote_name, execution_profile=policy.execution_profile,
                    output_profile=resolved_output, deadline_source=policy.deadline_source,
                    deadline_reminder=policy.deadline_reminder, stall_seconds=policy.stall_seconds,
                    cancel_grace_seconds=policy.cancel_grace_seconds,
                    cancel_on_stall=policy.cancel_on_stall, cleanup_policy=policy.cleanup_policy,
                    execution_policy_provenance=policy.provenance))
        except (TargetResolutionError, ValueError) as exc:
            return {"ok": False, "passed": False, "summary": None, "output": "", "mode": resolved_mode,
                    "error": str(exc)}
        except RemoteJobAdmissionError as exc:
            return {**exc.to_payload(), "passed": False, "summary": None,
                    "output": "", "mode": resolved_mode}
        except Exception as exc:
            return {"ok": False, "passed": False, "summary": None, "output": "", "mode": resolved_mode,
                    "error": f"remote durable test acceptance failed: {exc}"}
        return {"ok": True, "passed": None, "summary": "remote test job accepted", "output": "",
                "mode": resolved_mode, "job_id": accepted["job_id"], "lifecycle": "accepted",
                "workspace": target.workspace_label, "remote": target.remote_name}
    capability_error = _require_project_capability(project_dir, label, "wordpress.cli")
    if capability_error:
        return capability_error
    blocked = _managed_execution_unavailable(project_dir, label, "phpunit",
                                             ("sb", "test", mode or "auto"),
                                             timeout_seconds if timeout_seconds is not None else 900)
    if blocked: return {**blocked, "passed": False, "summary": None, "output": "", "mode": mode}
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "test", "--project-dir", project_dir]
    if label:
        cmd += ["--label", label]
    if mode:
        cmd += [mode]
    if phpunit_args.strip():
        cmd += ["--", *shlex.split(phpunit_args)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=900, cwd=str(SANDBOX_ROOT))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "run_tests timed out after 900s"}
    out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    import re as _re
    m = _re.search(r"(OK \(\d+ test.*?\)|FAILURES!.*|ERRORS!.*|Tests: \d.*)", out)
    resolved = _re.search(r"^\s*mode:\s+(auto|unit|integration)\s*$", out, _re.MULTILINE)
    return {
        "ok": res.returncode == 0,
        "passed": res.returncode == 0,
        "summary": m.group(1) if m else None,
        "output": out[-4000:],
        "mode": resolved_mode,
    }


def _async_text(value) -> str:
    """Normalize subprocess partial output without exposing a bytes repr."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _async_job_id_from_output(stdout: str, stderr: str = "") -> str | None:
    """Extract only the CLI's exact job-id forms from bounded output."""
    for pattern, value in ((_WP_CLI_JOB_ID_RE, stdout),
                           (_WP_CLI_STARTED_RE, stderr),
                           (_WP_CLI_STARTED_RE, stdout)):
        match = pattern.search(value or "")
        if match:
            return match.group(1)
    return None


def _async_request_id_conflict(stderr: str) -> dict | None:
    """Map the CLI's deterministic replay refusal to a typed MCP error."""
    for line in stderr.splitlines():
        if line.startswith("error: request id "):
            return {
                "ok": False,
                "code": "request_id_conflict",
                "error": line.removeprefix("error: ").strip(),
            }
    return None


def _async_acceptance_unknown(reason: str, *, stdout: str = "",
                              stderr: str = "") -> dict:
    """Return a bounded, retry-safe envelope when acceptance is unproven.

    A candidate job id is retained only as an inspection handle. It is never
    reported as accepted until the CLI emitted its complete success envelope.
    """
    payload = {
        "ok": False,
        "code": "acceptance_unknown",
        "status": "unknown",
        "error": reason,
    }
    candidate = _async_job_id_from_output(stdout, stderr)
    if candidate:
        payload["job_id"] = candidate
    diagnostic = (stdout + ("\n" if stdout and stderr else "") + stderr).strip()
    if diagnostic:
        payload["output"] = diagnostic[-2000:]
    return payload


def wp_cli_async(command: str, *, project_dir: str, label: str | None = None,
                 request_id: str | None = None) -> dict:
    """Start a wp-cli command as a BACKGROUND job (spec 004). Returns immediately
    with {ok, job_id}; the command keeps running detached. Use for long ops
    (media regenerate, big search-replace/imports). Poll with wp_cli_job, cancel
    with wp_cli_job_kill. The acceptance call is bounded; a timeout, malformed
    envelope, or non-zero launcher result returns ``code=acceptance_unknown``
    and must be inspected before any retry. A conflicting ``request_id`` is a
    deterministic ``request_id_conflict`` error instead.

    project_dir: the plugin project to target (call ensure_instance first).
    """
    if request_id is not None:
        try:
            from sandbox.commands.jobs import validate_request_id
            validate_request_id(request_id)
        except ValueError as exc:
            return {"ok": False, "code": "invalid_request_id", "error": str(exc)}
    capability_error = _require_project_capability(project_dir, label, "wordpress.cli")
    if capability_error:
        return capability_error
    blocked = _managed_execution_unavailable(project_dir, label, "durable_job",
                                             ("wp", *shlex.split(command)), 300)
    if blocked: return blocked
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    cmd = [str(SANDBOX_ROOT / "sb"), "--instance", inst, "wp", "--async"]
    if request_id is not None:
        cmd += ["--request-id", request_id]
    cmd += shlex.split(command)
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(SANDBOX_ROOT),
            timeout=_WP_CLI_ASYNC_ACCEPTANCE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _async_text(getattr(exc, "stdout", None)
                             or getattr(exc, "output", None))
        stderr = _async_text(getattr(exc, "stderr", None))
        return _async_acceptance_unknown(
            "wp_cli_async launch timed out after "
            f"{_WP_CLI_ASYNC_ACCEPTANCE_TIMEOUT:g}s; acceptance is unknown—"
            "inspect wp_cli_job or ./sb jobs before retrying",
            stdout=stdout, stderr=stderr,
        )
    except (OSError, subprocess.SubprocessError):
        return _async_acceptance_unknown(
            "wp_cli_async launch transport failed; acceptance is unknown—"
            "inspect ./sb jobs before retrying",
        )

    stdout = _async_text(getattr(res, "stdout", None))
    stderr = _async_text(getattr(res, "stderr", None))
    jid = _async_job_id_from_output(stdout, stderr)
    if getattr(res, "returncode", 1) == 0 and jid:
        return {"ok": True, "job_id": jid, "status": "running"}
    if request_id is not None:
        conflict = _async_request_id_conflict(stderr)
        if conflict is not None:
            return conflict
    return _async_acceptance_unknown(
        "wp_cli_async launch returned no valid acceptance; acceptance is "
        "unknown—inspect ./sb jobs before retrying",
        stdout=stdout, stderr=stderr,
    )


def _wp_cli_job_helpers():
    """Import the per-instance job surface before resolving an ID's instance."""
    import sys as _sys
    _sys.path.insert(0, str(SANDBOX_ROOT))
    from sandbox.commands.jobs import job_status, kill_job, _valid_job_id
    return job_status, kill_job, _valid_job_id


def wp_cli_job(job_id: str, offset: int = 0, limit: int = 1048576, *, project_dir: str, label: str | None = None) -> dict:
    """Poll a background wp-cli job (spec 004): returns {ok, status
    (running|completed|not_found), exit_code?, stdout, bytes_read, truncated}.
    Advance `offset` by `bytes_read` to fetch only new output. limit=-1 = whole log.
    """
    try:
        job_status, _kill_job, _valid_job_id = _wp_cli_job_helpers()
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"jobs module import failed: {e}"}
    if not _valid_job_id(job_id):
        return {"ok": False, "error": "invalid job id"}
    capability_error = _require_project_capability(project_dir, label, "wordpress.cli")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    try:
        return {"ok": True, **job_status(inst, job_id, offset=offset, limit=limit)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def wp_cli_job_kill(job_id: str, *, project_dir: str, label: str | None = None) -> dict:
    """Cancel a running background wp-cli job (spec 004). No-op if already finished.
    """
    try:
        _job_status, kill_job, _valid_job_id = _wp_cli_job_helpers()
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"jobs module import failed: {e}"}
    if not _valid_job_id(job_id):
        return {"ok": False, "error": "invalid job id"}
    capability_error = _require_project_capability(project_dir, label, "wordpress.cli")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    return {"ok": True, **kill_job(inst, job_id)}
