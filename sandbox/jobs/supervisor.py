"""Detached host-local supervisor for durable job execution."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

from .models import Lifecycle
from .output import JobOutputStore, OutputError
from .process import capture_process_identity
from .registry import JobRepository
from .storage import JobStorage
from .artifacts import collect as collect_artifacts
from .metrics import append as append_metric, sample as sample_metric


def run_descriptor(path: str | Path) -> int:
    descriptor_path = Path(path).resolve()
    descriptor = json.loads(descriptor_path.read_text())
    job_id = descriptor["job_id"]
    repository = JobRepository(descriptor["registry_path"])
    storage = JobStorage(descriptor["runtime_dir"], free_disk_reserve=descriptor.get("free_disk_reserve", 0))
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
        command = subprocess.Popen(descriptor["argv"], cwd=descriptor["cwd"], env=descriptor.get("environment"),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, close_fds=True)
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
            child_pgid=command.pid, child_start_identity=child.start_identity)
        selector = selectors.DefaultSelector()
        selector.register(command.stdout, selectors.EVENT_READ, "stdout")
        selector.register(command.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + int(descriptor["deadline_seconds"])
        next_metric = time.monotonic()
        timed_out = False
        while selector.get_map():
            if time.monotonic() >= next_metric and command.poll() is None:
                metric = append_metric(storage, repository, job_id, sample_metric(command.pid))
                repository.put_heartbeat(job_id, supervisor_at=_iso(), health_evidence={"process_alive": True},
                    last_metric_at=_iso(), metric_digest=str(metric.get("cpu_seconds")))
                next_metric = time.monotonic() + 1
            if time.monotonic() >= deadline and command.poll() is None:
                timed_out = True
                _signal_group(command.pid, 15)
                grace_end = time.monotonic() + int(descriptor.get("cancel_grace_seconds", 20))
                while command.poll() is None and time.monotonic() < grace_end:
                    time.sleep(0.05)
                if command.poll() is None:
                    _signal_group(command.pid, 9)
            for key, _ in selector.select(timeout=0.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    output.append(key.data, chunk)
                    repository.put_heartbeat(job_id, supervisor_at=_iso(), health_evidence={"process_alive": True}, last_output_at=_iso())
                else:
                    selector.unregister(key.fileobj)
        return_code = command.wait()
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
        if repository.get(job_id)["lifecycle"] == Lifecycle.CANCELLING.value:
            repository.transition(job_id, Lifecycle.CANCELLED, exit_code=return_code,
                termination_reason="cancelled_by_request", output_completeness="complete", integrity_sha256=integrity)
            return 130
        target = Lifecycle.SUCCEEDED if return_code == 0 else Lifecycle.FAILED
        repository.transition(job_id, target, exit_code=return_code,
            termination_reason=None if return_code == 0 else "exit_nonzero",
            output_completeness="complete", integrity_sha256=integrity)
        return 0 if return_code == 0 else 1
    except OutputError as exc:
        pressure = "pressure" in str(exc)
        repository.transition(job_id, Lifecycle.FAILED,
            termination_reason="storage_pressure" if pressure else "output_storage_failed",
            output_completeness="storage_pressure" if pressure else "write_failed",
            result_json=json.dumps({"error": str(exc)}))
        return 1
    except BaseException as exc:
        try:
            repository.transition(job_id, Lifecycle.FAILED, termination_reason="supervisor_error",
                result_json=json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        except Exception:
            pass
        return 1
    finally:
        try:
            repository.release_leases(job_id)
        except Exception:
            pass
        repository.close()


def _iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
