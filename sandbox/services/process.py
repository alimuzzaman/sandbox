from __future__ import annotations

from dataclasses import dataclass
import math
import signal
import threading
import time
from typing import Mapping, Protocol, Sequence
import os
import subprocess

from .redaction import redact_text


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: str | None = None,
            env: Mapping[str, str] | None = None,
            timeout: float | None = None) -> ProcessResult: ...


class BoundedProcessRunner:
    """Argument-list-only subprocess runner with bounded, redacted output."""

    _TERMINATION_GRACE = 0.2
    _DRAIN_GRACE = 0.2

    def __init__(self, *, max_output: int = 1_048_576,
                 secret_values: Sequence[str] = ()) -> None:
        if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output < 0:
            raise ValueError("max_output must be a non-negative integer")
        if not all(isinstance(value, str) for value in secret_values):
            raise ValueError("secret_values must contain strings")
        self.max_output = max_output
        self._secrets = tuple(value for value in secret_values if value)

    def _redact(self, value: str) -> str:
        return redact_text(value, exact_values=self._secrets)[:self.max_output]

    @staticmethod
    def _remaining(deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    @staticmethod
    def _join_readers(readers: Sequence[threading.Thread], timeout: float | None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        for reader in readers:
            reader.join(timeout=BoundedProcessRunner._remaining(deadline))
        return not any(reader.is_alive() for reader in readers)

    @classmethod
    def _terminate_process_tree(cls, process: subprocess.Popen[bytes]) -> None:
        """Terminate a POSIX process group, or only the immediate process elsewhere."""
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        else:
            # Python exposes no portable descendant-tree termination primitive
            # on non-POSIX platforms. Bound the immediate process and pipe
            # draining without claiming that unrelated descendants were killed.
            try:
                process.terminate()
            except ProcessLookupError:
                pass

        # Do not wait/poll here: either can reap the POSIX leader and allow its
        # PID/PGID to be reused before the final group signal.
        time.sleep(cls._TERMINATION_GRACE)

        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        else:
            try:
                process.kill()
            except ProcessLookupError:
                pass

        # Reap only after every signal that uses the leader's numeric PID/PGID.
        try:
            process.wait(timeout=cls._TERMINATION_GRACE)
        except subprocess.TimeoutExpired:
            # Returning must remain bounded even if the platform cannot reap a
            # process promptly after the strongest available termination.
            pass

    def run(self, argv: Sequence[str], *, cwd: str | None = None,
            env: Mapping[str, str] | None = None,
            timeout: float | None = None) -> ProcessResult:
        if isinstance(argv, (str, bytes)) or not argv:
            raise ValueError("argv must be a non-empty argument sequence")
        if (not all(isinstance(item, str) and "\x00" not in item for item in argv)
                or not argv[0]):
            raise ValueError("argv must contain non-empty NUL-free strings")
        if (timeout is not None and
                (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                 not math.isfinite(timeout) or timeout < 0)):
            raise ValueError("timeout must be a finite non-negative number")
        if env is not None:
            if not isinstance(env, Mapping) or not all(
                    isinstance(key, str) and key and "\x00" not in key and
                    isinstance(value, str) and "\x00" not in value
                    for key, value in env.items()):
                raise ValueError("env must contain NUL-free string keys and values")
        command = tuple(argv)
        deadline = None if timeout is None else time.monotonic() + timeout
        process = subprocess.Popen(
            command, cwd=cwd, env={**os.environ, **dict(env or {})},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            start_new_session=os.name == "posix", bufsize=0,
        )
        output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}

        def drain(name: str, stream) -> None:
            try:
                while True:
                    chunk = stream.read(65_536)
                    if not chunk:
                        return
                    remaining = self.max_output - len(output[name])
                    if remaining > 0:
                        output[name].extend(chunk[:remaining])
            except (OSError, ValueError):
                # A bounded timeout can close a pipe while its daemon reader is
                # still draining an escaped or otherwise uncooperative child.
                return

        readers = tuple(
            threading.Thread(target=drain, args=(name, stream), daemon=True)
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
        )
        for reader in readers:
            reader.start()
        timed_out = False
        leader_reaped = False
        try:
            process.wait(timeout=self._remaining(deadline))
            leader_reaped = True
        except subprocess.TimeoutExpired:
            timed_out = True

        if not timed_out:
            timed_out = not self._join_readers(readers, self._remaining(deadline))

        if timed_out:
            # Group signals are safe only while this Popen still owns the
            # leader PID/PGID. If the leader was already reaped, an inherited
            # pipe holder can delay readers, but its old numeric group ID may
            # already belong to an unrelated process.
            if not leader_reaped:
                self._terminate_process_tree(process)
            drained = self._join_readers(readers, self._DRAIN_GRACE)
            if not drained:
                # Do not let descendants that escaped the process group retain
                # pipe readers indefinitely. Closing unblocks or invalidates
                # the daemon reads without extending the caller's deadline.
                process.stdout.close()
                process.stderr.close()
                self._join_readers(readers, self._DRAIN_GRACE)
            else:
                process.stdout.close()
                process.stderr.close()
            return ProcessResult(
                command,
                124,
                self._redact(bytes(output["stdout"]).decode(errors="replace")),
                self._redact(bytes(output["stderr"]).decode(errors="replace") + "\nprocess timed out"),
            )
        process.stdout.close()
        process.stderr.close()
        return ProcessResult(
            command, process.returncode,
            self._redact(bytes(output["stdout"]).decode(errors="replace")),
            self._redact(bytes(output["stderr"]).decode(errors="replace")),
        )
