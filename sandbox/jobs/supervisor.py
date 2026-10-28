"""Detached host-local supervisor for durable job execution."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from .models import Health, Lifecycle
from .output import JobOutputStore, OutputError
from .process import capture_process_identity
from .registry import JobRepository
from .storage import JobStorage
from .artifacts import collect as collect_artifacts
from .metrics import append as append_metric, sample as sample_metric
from sandbox.services.redaction import redact_structure, redact_text


_CHILD_CONTEXT_KEYS = {
    "job_id": "SANDBOX_DURABLE_JOB_ID",
    "request_id": "SANDBOX_DURABLE_REQUEST_ID",
    "project_identity": "SANDBOX_DURABLE_PROJECT_IDENTITY",
    "project_root_digest": "SANDBOX_DURABLE_PROJECT_ROOT_DIGEST",
    "source_identity": "SANDBOX_DURABLE_SOURCE_IDENTITY",
    "source_commit": "SANDBOX_DURABLE_SOURCE_COMMIT",
    "source_dirty_digest": "SANDBOX_DURABLE_SOURCE_DIRTY_DIGEST",
}


@contextmanager
def _child_identity_context(value: object):
    """Temporarily add only fixed authoritative keys for one child launch.

    The detached supervisor is single-purpose. This intentionally does not
    enumerate, copy, unpack, log, or persist the inherited environment.
    """
    context = value if isinstance(value, dict) else {}
    previous = {}
    changed = []
    try:
        for field, environment_key in _CHILD_CONTEXT_KEYS.items():
            item = context.get(field)
            if item is None:
                continue
            if not isinstance(item, str) or not item or "\x00" in item:
                raise ValueError("durable child identity context is invalid")
            previous[environment_key] = os.environ.get(environment_key)
            os.environ[environment_key] = item
            changed.append(environment_key)
        yield
    finally:
        for environment_key in changed:
            old = previous[environment_key]
            if old is None:
                os.environ.pop(environment_key, None)
            else:
                os.environ[environment_key] = old


def _linux_cgroup_path(pid: int) -> str | None:
    try:
        for line in Path(f"/proc/{pid}/cgroup").read_text(
                encoding="ascii").splitlines():
            hierarchy, controllers, path = line.split(":", 2)
            if hierarchy == "0" and controllers == "" and path.startswith("/"):
                return path
    except (OSError, UnicodeError, ValueError):
        return None
    return None


def run_descriptor(path: str | Path) -> int:
    descriptor_path = Path(path).resolve()
    descriptor = json.loads(descriptor_path.read_text())
    job_id = descriptor["job_id"]
    repository = JobRepository(descriptor["registry_path"])
    storage = JobStorage(descriptor["runtime_dir"], free_disk_reserve=descriptor.get("free_disk_reserve", 0))
    command = None
    selector = None
    release_authority = True
    try:
        current = repository.get(job_id)
        if current["lifecycle"] in {
                Lifecycle.SUCCEEDED.value, Lifecycle.FAILED.value, Lifecycle.TIMED_OUT.value,
                Lifecycle.CANCELLED.value, Lifecycle.INTERRUPTED.value,
        }:
            return 0
        if current["lifecycle"] not in {Lifecycle.ACCEPTED.value, Lifecycle.QUEUED.value}:
            raise RuntimeError(f"supervisor cannot start a {current['lifecycle']} job")
        identity = capture_process_identity(os.getpid())
        if identity is None:
            raise RuntimeError("could not capture supervisor process identity")
        repository.put_process_identity(job_id, host_boot_id=identity.host_boot_id,
            supervisor_pid=identity.pid, supervisor_start_identity=identity.start_identity,
            supervisor_nonce_hash=descriptor["nonce_hash"])
        repository.transition(job_id, Lifecycle.RUNNING)
        output = JobOutputStore(storage, repository, job_id, secrets=descriptor.get("redaction_secrets", ()))
        if _managed_native_job(descriptor):
            return _run_managed_native(descriptor, repository, output)
        with _child_identity_context(descriptor.get("authoritative_context")):
            command = subprocess.Popen(
                descriptor["argv"], cwd=descriptor["cwd"],
                env=descriptor.get("environment"), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, close_fds=True)
        release_authority = False
        child = capture_process_identity(command.pid)
        if child is None:
            raise RuntimeError("could not capture child process identity")
        # ``start_new_session=True`` makes the child its own process-group
        # leader.  Reading its group with ``os.getpgid`` races a fast command
        # that exits between identity capture and the lookup; its PID is the
        # authoritative group ID established at launch.
        repository.put_process_identity(job_id, host_boot_id=identity.host_boot_id,
            supervisor_pid=identity.pid, supervisor_start_identity=identity.start_identity,
            supervisor_nonce_hash=descriptor["nonce_hash"], child_pid=command.pid,
            child_pgid=command.pid,
            child_cgroup_path=(
                child_cgroup if (child_cgroup := _linux_cgroup_path(command.pid))
                not in {None, "/", _linux_cgroup_path(os.getpid())} else None),
            child_start_identity=child.start_identity)
        observed_at = _iso()
        repository.put_heartbeat(job_id, supervisor_at=observed_at, health_evidence={
            "process_alive": True, "child_observed": True,
        }, child_observed_at=observed_at, last_activity_at=observed_at,
            last_progress_at=observed_at)
        selector = selectors.DefaultSelector()
        selector.register(command.stdout, selectors.EVENT_READ, "stdout")
        selector.register(command.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + int(descriptor["deadline_seconds"])
        next_metric = time.monotonic()
        timed_out = False
        stall_cancelled = False
        last_metric_digest = None
        last_progress_monotonic = time.monotonic()
        last_progress_at = _iso()
        while selector.get_map():
            if time.monotonic() >= next_metric and _child_running(command):
                metric = append_metric(storage, repository, job_id, sample_metric(command.pid))
                movement_digest = metric.get("movement_digest")
                metric_movement = movement_digest is not None and movement_digest != last_metric_digest
                if metric_movement:
                    last_progress_monotonic = time.monotonic()
                    last_progress_at = _iso()
                last_metric_digest = movement_digest
                repository.put_heartbeat(job_id, supervisor_at=_iso(), health_evidence={
                    "process_alive": True, "metric_movement": metric_movement,
                }, last_metric_at=_iso(), last_progress_at=last_progress_at,
                    metric_digest=str(movement_digest or ""))
                next_metric = time.monotonic() + 1
            stall_seconds = int(descriptor.get("stall_seconds", 300))
            stalled = _child_running(command) and time.monotonic() - last_progress_monotonic >= stall_seconds
            if stalled:
                repository.put_heartbeat(job_id, supervisor_at=_iso(), health_evidence={
                    "process_alive": True, "stalled": True,
                    "cancel_on_stall": bool(descriptor.get("cancel_on_stall", False)),
                }, last_progress_at=last_progress_at)
            if stalled and not stall_cancelled and descriptor.get("cancel_on_stall", False):
                stall_cancelled = True
                repository.transition(job_id, Lifecycle.CANCELLING)
                _signal_group(command.pid, 15)
                grace_end = time.monotonic() + int(descriptor.get("cancel_grace_seconds", 20))
                while _child_running(command) and time.monotonic() < grace_end:
                    time.sleep(0.05)
                if _child_running(command):
                    _signal_group(command.pid, 9)
            if time.monotonic() >= deadline and _child_running(command):
                timed_out = True
                _signal_group(command.pid, 15)
                grace_end = time.monotonic() + int(descriptor.get("cancel_grace_seconds", 20))
                while _child_running(command) and time.monotonic() < grace_end:
                    time.sleep(0.05)
                if _child_running(command):
                    _signal_group(command.pid, 9)
            for key, _ in selector.select(timeout=0.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    output.append(key.data, chunk)
                    last_progress_monotonic = time.monotonic()
                    last_progress_at = _iso()
                    repository.put_heartbeat(job_id, supervisor_at=_iso(), health_evidence={
                        "process_alive": True, "progress": True,
                    }, last_output_at=_iso(), last_activity_at=_iso(), last_progress_at=last_progress_at)
                else:
                    selector.unregister(key.fileobj)
        return_code = command.wait()
        release_authority = True
        selector.close()
        command.stdout.close()
        command.stderr.close()
        output.finish("stdout"); output.finish("stderr")
        integrity = output.complete()
        # Artifacts describe successful job output. Do not let a missing
        # artifact mask the child command's actual failure (for example a
        # remote CI host missing its execution engine); retained stderr and
        # the child exit status remain the authoritative diagnosis.
        if return_code == 0 and descriptor.get("artifact_paths"):
            collect_artifacts(storage, repository, job_id, project_root=descriptor["cwd"],
                              declared_paths=tuple(descriptor["artifact_paths"]))
        if timed_out:
            repository.transition(job_id, Lifecycle.TIMED_OUT, exit_code=return_code,
                termination_reason="deadline_exceeded", output_completeness="complete", integrity_sha256=integrity)
            return 124
        if stall_cancelled:
            repository.transition(job_id, Lifecycle.CANCELLED, exit_code=return_code,
                termination_reason="cancelled_on_stall", output_completeness="complete", integrity_sha256=integrity)
            return 130
        if repository.get(job_id)["lifecycle"] == Lifecycle.CANCELLING.value:
            repository.transition(job_id, Lifecycle.CANCELLED, exit_code=return_code,
                termination_reason="cancelled_by_request", output_completeness="complete", integrity_sha256=integrity)
            return 130
        target = Lifecycle.SUCCEEDED if return_code == 0 else Lifecycle.FAILED
        termination_reason = _child_termination_reason(return_code)
        repository.transition(job_id, target, exit_code=return_code,
            termination_reason=termination_reason,
            output_completeness="complete", integrity_sha256=integrity)
        return 0 if return_code == 0 else 1
    except BaseException as exc:
        release_authority = False
        output_error = isinstance(exc, OutputError)
        pressure = output_error and "pressure" in str(exc)
        try:
            release_authority = _finalize_failure(
                command, descriptor, repository,
                reason=("storage_pressure" if pressure else "output_storage_failed")
                    if output_error else "supervisor_error",
                completeness=("storage_pressure" if pressure else "write_failed")
                    if output_error else "partial",
                detail={"error": type(exc).__name__, "detail": str(exc)})
        except Exception:
            pass
        return 1
    finally:
        if selector is not None:
            with suppress(Exception):
                selector.close()
        if command is not None:
            for stream in (command.stdout, command.stderr):
                if stream is not None:
                    with suppress(Exception):
                        stream.close()
        try:
            if release_authority:
                repository.release_leases(job_id)
        except Exception:
            pass
        try:
            from sandbox.application.workspace_service import finalize_terminal_workspace
            if release_authority:
                finalize_terminal_workspace(
                    repository, job_id, descriptor.get("workspace_cleanup"))
        except Exception:
            # Terminal command truth is already durable. Cleanup failures are
            # recorded by the shared seam and must never rewrite that result.
            pass
        repository.close()


def _group_members(process_group_id: int) -> list[tuple[int, str]]:
    """Observe group membership without reaping its launch-owned PID anchor."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True,
        timeout=2, check=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"})
    members = []
    for line in result.stdout.splitlines():
        pid, pgid, state = line.split()
        if int(pgid) == process_group_id:
            members.append((int(pid), state))
    return members


def _child_running(command: subprocess.Popen) -> bool:
    # poll() reaps a fast-exiting leader, allowing its PID/group ID to be
    # reused before an output failure can stop descendants. Keep the direct
    # child waitable until the pipe loop finishes instead.
    return any(pid == command.pid and not state.startswith("Z")
               for pid, state in _group_members(command.pid))


def _stop_failed_child(command: subprocess.Popen, grace_seconds: int) -> bool:
    """Bound failure cleanup to the session created by this Popen only.

    An unreaped direct child reserves its PID even after exit; consequently
    killpg cannot target a reused group. Once reaped, only absence is safe to
    establish: do not signal a group whose ownership we can no longer prove.
    """
    if command.returncode is not None:
        try:
            os.killpg(command.pid, 0)
        except ProcessLookupError:
            return True
        return False
    for signal_number, seconds in ((15, grace_seconds), (9, 2)):
        if not any(not state.startswith("Z") for _, state in _group_members(command.pid)):
            command.wait(timeout=1)
            return True
        _signal_group(command.pid, signal_number)
        end = time.monotonic() + seconds
        while True:
            members = _group_members(command.pid)
            if not any(not state.startswith("Z") for _, state in members):
                command.wait(timeout=1)
                return True
            if time.monotonic() >= end:
                break
            time.sleep(0.05)
    return False


def _finalize_failure(command, descriptor, repository, *, reason, completeness, detail) -> bool:
    stopped = command is None
    stop_error = None
    if command is not None:
        try:
            stopped = _stop_failed_child(command, int(descriptor.get("cancel_grace_seconds", 20)))
        except BaseException as exc:
            stop_error = type(exc).__name__
    job_id = descriptor["job_id"]
    detail = redact_structure({**detail, "termination_reason": reason,
                               "owned_work_stopped": stopped, "stop_error": stop_error})
    if not stopped:
        # Immutable evidence also fences later startup reconciliation. Merely
        # losing this supervisor must not turn uncertain cessation into proof.
        repository.append_event(job_id, "supervisor_stop_uncertain", detail)
        repository.put_heartbeat(job_id, supervisor_at=_iso(), health_evidence=detail)
        repository.set_health(job_id, Health.ORPHANED, detail)
        if repository.get(job_id)["lifecycle"] == Lifecycle.RUNNING.value:
            repository.transition(job_id, Lifecycle.CANCELLING,
                termination_reason=reason, output_completeness=completeness,
                result_json=json.dumps(detail))
        return False
    repository.transition(job_id, Lifecycle.FAILED, termination_reason=reason,
        output_completeness=completeness, result_json=json.dumps(detail))
    return True


def _iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _child_termination_reason(return_code: int) -> str | None:
    """Classify a child kill without mistaking it for proof of an OOM event.

    POSIX shells expose SIGKILL as 128 + 9 (137), while ``Popen`` can surface
    the same event as ``-9``.  The host supervisor cannot independently prove
    whether an operator, Docker, or the kernel sent that signal, so the stable
    classification deliberately remains ``process_killed``; callers can use
    host/cgroup evidence to distinguish OOM from another kill.
    """
    if return_code == 0:
        return None
    if return_code in {-9, 137}:
        return "process_killed"
    return "exit_nonzero"


def _managed_native_job(descriptor) -> bool:
    """Do not host-Popen a managed-native durable payload."""
    runtime = descriptor.get("execution_runtime")
    if runtime not in {"host", "managed_native"}:
        raise RuntimeError("durable descriptor runtime selection is missing")
    return runtime == "managed_native"


def _run_managed_native(descriptor, repository, output: JobOutputStore) -> int:
    """Execute a managed durable payload through the adapter-owned gateway.

    This synchronous boundary is deliberate: the installed helper owns the
    bounded guest process and deadline.  The host supervisor only persists the
    already-bounded result and never interprets or host-executes project argv.
    """
    from sandbox.application.context import execute_project
    from sandbox.runtimes.base import ExecutionRequest

    if descriptor.get("artifact_paths"):
        repository.transition(
            descriptor["job_id"], Lifecycle.FAILED,
            termination_reason="adapter_native_artifacts_unavailable",
            result_json=json.dumps({"error": "managed durable artifacts require an adapter-native transport"}),
        )
        return 1
    result = execute_project({}, ExecutionRequest(
        descriptor.get("project_root") or descriptor["cwd"],
        descriptor.get("label", "default"),
        "durable_job",
        tuple(descriptor["argv"]),
        int(descriptor["deadline_seconds"]),
    ))
    stdout = result.data.get("stdout", "")
    stderr = result.data.get("stderr", "")
    output.append("stdout", stdout.encode() if isinstance(stdout, str) else bytes(stdout))
    output.append("stderr", stderr.encode() if isinstance(stderr, str) else bytes(stderr))
    output.finish("stdout")
    output.finish("stderr")
    integrity = output.complete()
    target = Lifecycle.SUCCEEDED if result.ok else Lifecycle.FAILED
    reason = redact_structure(result.data.get("reason", {}))
    if not isinstance(reason, dict):
        reason = {"code": "redaction_failed"}
    reason_code = reason.get("code") if isinstance(reason, dict) else None
    repository.transition(
        descriptor["job_id"], target, exit_code=result.exit_code,
        termination_reason=None if result.ok else (reason_code or "isolated_payload_failed"),
        output_completeness="complete", integrity_sha256=integrity,
        result_json=json.dumps({"state": result.state, "reason": reason}, sort_keys=True),
    )
    return 0 if result.ok else 1


def _signal_group(process_group_id: int, signal_number: int) -> None:
    """Signal the launch-owned group without converting an exit race to failure."""
    try:
        os.killpg(process_group_id, signal_number)
    except ProcessLookupError:
        # The deadline check and signal can race normal child completion. The
        # subsequent ``wait`` observes the actual result and finalizes it.
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("descriptor")
    args = parser.parse_args(argv)
    return run_descriptor(args.descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
