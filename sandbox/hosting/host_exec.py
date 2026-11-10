"""Run one argv in a hosted target's service with stdin passed through (spec 074).

The caller's stdin descriptor is handed straight to the ``ssh`` child, so the
bytes travel kernel to ``ssh`` to ``docker compose exec -T`` and Sandbox never
reads, stores or logs them. The remote command line carries only the compose
prefix ``host apply`` uses, the service and the caller's argv.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from sandbox.core import _paths
from sandbox.core._hosting import HostingRefusal
from sandbox.hosting.target_resolution import ResolvedTarget

TIMEOUT_DEFAULT = 300
TIMEOUT_MIN = 1
TIMEOUT_MAX = 3600
KILL_AFTER_SECONDS = 10
LOCAL_GRACE_SECONDS = 30
CAPTURE_LIMIT = 1 << 20

OK = "ok"
FAILED = "host_exec_failed"
TIMED_OUT = "host_exec_timeout"
TRANSPORT_FAILED = "host_exec_transport_failed"
WATCHDOG_EXPIRED = "host_exec_watchdog_expired"
INTERRUPTED = "host_exec_interrupted"

USAGE = "host_exec_usage"
UNKNOWN_SERVICE = "host_exec_unknown_service"
SERVICES_UNAVAILABLE = "host_exec_services_unavailable"
AUDIT_UNAVAILABLE = "host_exec_audit_unavailable"
REMOTE_UNKNOWN = "remote_unknown"
REMOTE_NOT_PROVISIONED = "remote_not_provisioned"

EXIT_REFUSAL = 1
EXIT_USAGE = 2
EXIT_TIMEOUT = 124
EXIT_TRANSPORT = 255

# Stack apply jobs in these lifecycles still own the target.
ACTIVE_JOB_LIFECYCLES = frozenset({"accepted", "queued", "running", "cancelling"})
_GLOBAL_VALUE_OPTIONS = frozenset({"--instance", "--label"})


class ExecRefusal(HostingRefusal):
    """A refusal before anything ran on the remote."""

    def __init__(self, code: str, message: str, exit_code: int = EXIT_REFUSAL):
        super().__init__(code, message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class ExecRequest:
    target: ResolvedTarget
    service: str
    argv: tuple[str, ...]
    timeout_seconds: int = TIMEOUT_DEFAULT
    capture: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple) or not self.argv or not all(
                isinstance(item, str) for item in self.argv):
            raise ExecRefusal(USAGE, "host exec needs a command after --", EXIT_USAGE)
        if not isinstance(self.service, str) or not self.service:
            raise ExecRefusal(USAGE, "host exec requires --service", EXIT_USAGE)
        parse_timeout(self.timeout_seconds)


@dataclass(frozen=True)
class ExecOutcome:
    exit_code: int
    code: str
    duration_ms: int
    stdout: bytes = b""
    stderr: bytes = b""
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.code == OK

    def envelope(self, request: ExecRequest) -> dict:
        payload = {
            "ok": self.ok, "code": self.code, "exit_code": self.exit_code,
            "remote": request.target.remote, "target": request.target.label,
            "service": request.service, "duration_ms": self.duration_ms,
        }
        if request.capture:
            payload.update({
                "stdout": self.stdout.decode("utf-8", errors="replace"),
                "stderr": self.stderr.decode("utf-8", errors="replace"),
                "stdout_truncated": self.stdout_truncated,
                "stderr_truncated": self.stderr_truncated,
            })
        return payload


def parse_timeout(value: object) -> int:
    if value is None:
        return TIMEOUT_DEFAULT
    if isinstance(value, bool) or not isinstance(value, int) \
            or not TIMEOUT_MIN <= value <= TIMEOUT_MAX:
        raise ExecRefusal(
            USAGE, f"--timeout must be between {TIMEOUT_MIN} and {TIMEOUT_MAX} seconds", EXIT_USAGE)
    return value


def split_passthrough(argv: list[str]) -> tuple[list[str], list[str] | None]:
    """Set aside the argv after ``--`` when the top-level command is ``host``.

    The shared ``host`` parser cannot take ``argparse.REMAINDER`` (it would
    swallow every option), so ``main()`` parses the head and hands the tail to
    ``host exec``. Any other command is returned untouched.
    """
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in _GLOBAL_VALUE_OPTIONS:
            index += 2
            continue
        if any(token.startswith(option + "=") for option in _GLOBAL_VALUE_OPTIONS):
            index += 1
            continue
        break
    if index >= len(argv) or argv[index] != "host" or "--" not in argv[index + 1:]:
        return argv, None
    delimiter = argv.index("--", index + 1)
    return argv[:delimiter], argv[delimiter + 1:]


def remote_command(prefix: str, request: ExecRequest) -> str:
    """The remote shell line: bounded Compose exec, no user or env overrides.

    ``env`` keeps the prefix's leading ``SANDBOX_HOST_ENV_FILE=...`` an
    assignment after ``timeout``.
    """
    head = shlex.join(["timeout", f"--kill-after={KILL_AFTER_SECONDS}",
                       str(request.timeout_seconds), "env"])
    tail = shlex.join(["--profile", "*", "exec", "-T", request.service, *request.argv])
    return f"{head} {prefix} {tail}"


def busy_reason(record: dict | None, stack_job_lifecycle: str | None) -> str | None:
    """Why the target is owned by another operation, or None."""
    if stack_job_lifecycle in ACTIVE_JOB_LIFECYCLES:
        return f"the stack's apply job is {stack_job_lifecycle}"
    record = record if isinstance(record, dict) else {}
    if record.get("active_operation") is not None:
        return "another host apply, recovery or destroy owns this target"
    if record.get("recovery_uncertainty") is not None:
        return "uncertain host recovery state fences this target"
    activation = record.get("image_activation")
    if isinstance(activation, dict) and activation.get("active") is not None:
        return "a host image activation owns this target"
    return None


def effect_lock_free(state_key: str, repository=None) -> bool:
    """Try the per-target effect lock apply holds for its whole run, once."""
    if repository is None:
        from sandbox.hosting.recovery.repository import RecoveryRepository
        repository = RecoveryRepository()
    try:
        with repository.effect_lock(state_key, timeout_seconds=0):
            return True
    except TimeoutError:
        return False


def stdin_source(stream=None):
    """The descriptor to hand to ``ssh``: the caller's stdin unless it is a TTY."""
    stream = sys.stdin if stream is None else stream
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return subprocess.DEVNULL
    try:
        if os.isatty(descriptor):
            return subprocess.DEVNULL
        os.fstat(descriptor)
    except OSError:
        return subprocess.DEVNULL
    return descriptor


class _BoundedBuffer:
    def __init__(self, limit: int):
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def drain(self, pipe) -> None:
        try:
            while True:
                chunk = pipe.read(64 * 1024)
                if not chunk:
                    return
                room = self.limit - len(self.data)
                if room > 0:
                    self.data.extend(chunk[:room])
                if len(chunk) > max(room, 0):
                    self.truncated = True
        except (OSError, ValueError):
            return
        finally:
            try:
                pipe.close()
            except OSError:
                pass


def classify(returncode: int, elapsed: float, timeout_seconds: int, *,
             locally_killed: bool = False) -> tuple[int, str]:
    """Map the SSH child's status to (exit status, result code).

    The remote status is passed through unchanged. ``timeout`` exits 124, or
    137 after ``--kill-after``; a command that itself exits 124 or 137 after
    the deadline reads the same, so the timeout label is inferred, never the
    status. The local watchdog firing means the remote deadline could not be
    confirmed and the command may still be running there.
    """
    if locally_killed:
        return EXIT_TRANSPORT, WATCHDOG_EXPIRED
    if returncode in (124, 137) and elapsed >= timeout_seconds:
        return returncode, TIMED_OUT
    if returncode == 255:
        return EXIT_TRANSPORT, TRANSPORT_FAILED
    if returncode < 0:
        return 128 + (-returncode), FAILED
    return returncode, OK if returncode == 0 else FAILED


def run(ssh_argv: list[str], request: ExecRequest, *, stdin, clock=time.monotonic) -> ExecOutcome:
    """Run one SSH child; stream or capture its output; never read stdin."""
    capture = request.capture
    started = clock()
    try:
        proc = subprocess.Popen(
            ssh_argv, stdin=stdin,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except OSError as exc:
        message = f"could not start ssh: {exc.strerror or exc}\n".encode()
        return ExecOutcome(exit_code=EXIT_TRANSPORT, code=TRANSPORT_FAILED,
                           duration_ms=int((clock() - started) * 1000),
                           stderr=message if capture else b"")
    buffers = []
    threads = []
    if capture:
        for pipe in (proc.stdout, proc.stderr):
            buffer = _BoundedBuffer(CAPTURE_LIMIT)
            thread = threading.Thread(target=buffer.drain, args=(pipe,), daemon=True)
            thread.start()
            buffers.append(buffer)
            threads.append(thread)
    deadline = request.timeout_seconds + KILL_AFTER_SECONDS + LOCAL_GRACE_SECONDS
    locally_killed = False
    interrupted = False
    try:
        returncode = proc.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        proc.kill()
        returncode = proc.wait()
        locally_killed = True
    except KeyboardInterrupt:
        proc.kill()
        returncode = proc.wait()
        interrupted = True
    for thread in threads:
        thread.join(timeout=5)
    elapsed = clock() - started
    if interrupted:
        exit_code, code = 130, INTERRUPTED
    else:
        exit_code, code = classify(returncode, elapsed, request.timeout_seconds,
                                   locally_killed=locally_killed)
    out, err = (buffers + [_BoundedBuffer(0), _BoundedBuffer(0)])[:2]
    return ExecOutcome(
        exit_code=exit_code, code=code, duration_ms=int(elapsed * 1000),
        stdout=bytes(out.data), stderr=bytes(err.data),
        stdout_truncated=out.truncated, stderr_truncated=err.truncated)


def audit_path() -> Path:
    return _paths.RUNTIME_DIR / "host-exec" / "audit.jsonl"


class AuditLog:
    """Append-only audit, opened before the run so a run is never unaudited.

    Use it as a context manager; the descriptor is closed exactly once.
    """

    def __init__(self, path: Path | None = None):
        path = path or audit_path()
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError as exc:
            raise ExecRefusal(AUDIT_UNAVAILABLE,
                              f"cannot open the host exec audit log: {exc.strerror}") from None
        self.path = path

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                os.close(fd)
            except OSError:
                # The record, if any, is already written; a close error must not
                # replace the command's result.
                pass

    def write(self, request: ExecRequest, outcome: ExecOutcome, *, now=None) -> None:
        from sandbox.services.redaction import redact_text
        at = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {
            "at": at, "remote": request.target.remote, "target": request.target.label,
            "service": request.service, "argv": [redact_text(item) for item in request.argv],
            "exit_code": outcome.exit_code, "code": outcome.code,
            "duration_ms": outcome.duration_ms,
        }
        line = (json.dumps(record, sort_keys=True) + "\n").encode()
        if self._fd is None:
            raise OSError("audit log is closed")
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        try:
            os.write(self._fd, line)
        finally:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass  # close() releases the lock; never mask the write's own result
