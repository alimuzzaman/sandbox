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


def run_descriptor(path: str | Path) -> int:
    descriptor_path = Path(path).resolve()
    descriptor = json.loads(descriptor_path.read_text())
    job_id = descriptor["job_id"]
    repository = JobRepository(descriptor["registry_path"])
    storage = JobStorage(descriptor["runtime_dir"], free_disk_reserve=descriptor.get("free_disk_reserve", 0))
    try:
        current = repository.get(job_id)
        if current["lifecycle"] == Lifecycle.ACCEPTED.value:
            repository.transition(job_id, Lifecycle.QUEUED)
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
        repository.put_process_identity(job_id, host_boot_id=identity.host_boot_id,
            supervisor_pid=identity.pid, supervisor_start_identity=identity.start_identity,
            supervisor_nonce_hash=descriptor["nonce_hash"], child_pid=command.pid,
            child_pgid=os.getpgid(command.pid), child_start_identity=child.start_identity)
        selector = selectors.DefaultSelector()
        selector.register(command.stdout, selectors.EVENT_READ, "stdout")
        selector.register(command.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + int(descriptor["deadline_seconds"])
        timed_out = False
        while selector.get_map():
            if time.monotonic() >= deadline and command.poll() is None:
                timed_out = True
                os.killpg(os.getpgid(command.pid), 15)
                grace_end = time.monotonic() + int(descriptor.get("cancel_grace_seconds", 20))
                while command.poll() is None and time.monotonic() < grace_end:
                    time.sleep(0.05)
                if command.poll() is None:
                    os.killpg(os.getpgid(command.pid), 9)
            for key, _ in selector.select(timeout=0.1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    output.append(key.data, chunk)
                    repository.put_heartbeat(job_id, supervisor_at=_iso(), health_evidence={"process_alive": True}, last_output_at=_iso())
                else:
                    selector.unregister(key.fileobj)
        return_code = command.wait()
        output.finish("stdout"); output.finish("stderr")
        if descriptor.get("artifact_paths"):
            collect_artifacts(storage, repository, job_id, project_root=descriptor["cwd"],
                              declared_paths=tuple(descriptor["artifact_paths"]))
        if timed_out:
            repository.transition(job_id, Lifecycle.TIMED_OUT, exit_code=return_code, termination_reason="deadline_exceeded")
            return 124
        if repository.get(job_id)["lifecycle"] == Lifecycle.CANCELLING.value:
            repository.transition(job_id, Lifecycle.CANCELLED, exit_code=return_code,
                termination_reason="cancelled_by_request")
            return 130
        target = Lifecycle.SUCCEEDED if return_code == 0 else Lifecycle.FAILED
        repository.transition(job_id, target, exit_code=return_code,
            termination_reason=None if return_code == 0 else "exit_nonzero")
        return 0 if return_code == 0 else 1
    except OutputError as exc:
        repository.transition(job_id, Lifecycle.FAILED, termination_reason="output_storage_failed", result_json=json.dumps({"error": str(exc)}))
        return 1
    except BaseException as exc:
        try:
            repository.transition(job_id, Lifecycle.FAILED, termination_reason="supervisor_error", result_json=json.dumps({"error": type(exc).__name__}))
        except Exception:
            pass
        return 1
    finally:
        repository.close()


def _iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("descriptor")
    args = parser.parse_args(argv)
    return run_descriptor(args.descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
