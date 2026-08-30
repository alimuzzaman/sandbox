from __future__ import annotations

from dataclasses import dataclass, field
import math
import signal
import threading
import time
from typing import Mapping, Protocol, Sequence
import os
import subprocess

from .environment import ExplicitEnvironment
from .redaction import redact_text


_EDGE_TRUNCATION_MARKER = b"\n...[output truncated]...\n"


class _BoundedEdgeCapture:
    """Retain one bounded byte stream, preserving both edges after overflow.

    Until the stream crosses ``limit`` this behaves like the historical
    prefix buffer and retains the complete value. Once it does, the already
    captured prefix is split into a head and tail and subsequent chunks update
    only the bounded tail. At no point does the capture hold more than the
    configured per-stream limit plus one bounded pipe-read chunk.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._data = bytearray()
        self._head = bytearray()
        self._tail = bytearray()
        self._truncated = False
        # A marker should not consume a tiny caller-selected bound entirely;
        # when there is no room for the full marker, retain the two edges and
        # omit the marker rather than returning only a diagnostic fragment.
        self._marker = (_EDGE_TRUNCATION_MARKER
                        if limit >= len(_EDGE_TRUNCATION_MARKER) else b"")
        available = max(0, limit - len(self._marker))
        self._head_limit = available // 2
        self._tail_limit = available - self._head_limit

    def append(self, chunk: bytes) -> None:
        if not chunk or self.limit == 0:
            return
        if not self._truncated:
            if len(self._data) + len(chunk) <= self.limit:
                self._data.extend(chunk)
                return
            # The first overflow is the only time we split the complete
            # prefix. Thereafter the tail is a fixed-size rolling window.
            prefix = bytes(self._data) + chunk
            self._head.extend(prefix[:self._head_limit])
            if self._tail_limit:
                self._tail.extend(prefix[-self._tail_limit:])
            self._data.clear()
            self._truncated = True
            # The split above already folded this chunk into both edges;
            # falling through would append it a second time and corrupt the
            # tail window with a duplicated fragment.
            return
        if self._tail_limit:
            self._tail.extend(chunk)
            if len(self._tail) > self._tail_limit:
                del self._tail[:-self._tail_limit]

    def render(self) -> bytes:
        if not self._truncated:
            return bytes(self._data)
        return bytes(self._head) + self._marker + bytes(self._tail)

    @property
    def truncated(self) -> bool:
        return self._truncated


def _decode_bounded_output(raw: bytes, limit: int) -> str:
    """Decode captured bytes without expanding a split UTF-8 edge past bound."""
    text = raw.decode(errors="replace")
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    # A raw edge cut can split a multibyte code point; ``replace`` expands
    # that fragment. Re-bound the expanded representation and ignore only an
    # incomplete endpoint so the returned string's encoded size stays exact.
    bounded = _BoundedEdgeCapture(limit)
    bounded.append(encoded)
    return bounded.render().decode(errors="ignore")


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = field(repr=False)
    stderr: str = field(repr=False)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    termination_reason: str | None = None


class ProcessRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: str | None = None,
            env: Mapping[str, str] | None = None,
            timeout: float | None = None, cancellation=None) -> ProcessResult: ...


class BoundedProcessRunner:
    """Argument-list-only subprocess runner with bounded, redacted output.

    ``env=None`` leaves native OS inheritance to ``Popen`` without reading the
    parent environment. Any explicit mapping is the complete child environment.
    """

    _TERMINATION_GRACE = 0.2
    _READER_START_GRACE = 0.1
    _DRAIN_GRACE = 0.2

    def __init__(self, *, max_output: int = 1_048_576,
                 secret_values: Sequence[str] = (),
                 terminate_on_output_limit: bool = False) -> None:
        if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output < 0:
            raise ValueError("max_output must be a non-negative integer")
        if not all(isinstance(value, str) for value in secret_values):
            raise ValueError("secret_values must contain strings")
        if not isinstance(terminate_on_output_limit, bool):
            raise ValueError("terminate_on_output_limit must be boolean")
        self.max_output = max_output
        self._secrets = tuple(value for value in secret_values if value)
        self.terminate_on_output_limit = terminate_on_output_limit

    def _redact(self, value: str) -> str:
        """Redact and re-bound one stream without losing its retained tail."""
        redacted = redact_text(value, exact_values=self._secrets)
        encoded = redacted.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_output:
            return redacted
        bounded = _BoundedEdgeCapture(self.max_output)
        bounded.append(encoded)
        return _decode_bounded_output(bounded.render(), self.max_output)

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
            timeout: float | None = None, cancellation=None) -> ProcessResult:
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
        terminal_status = None
        initial_state = None
        if cancellation is not None:
            terminal_status = getattr(cancellation, "terminal_status", None)
            if not callable(terminal_status):
                raise ValueError("cancellation must expose terminal_status")
            try:
                initial_state = terminal_status()
            except Exception as exc:
                raise ValueError("cancellation probe failed before process start") from exc
            if initial_state is not None and (
                type(initial_state) is not str
                or initial_state not in {"cancelled", "disconnected"}
            ):
                raise ValueError("cancellation returned an invalid terminal state")
            if initial_state is not None:
                return ProcessResult(
                    command, 130, "", "process cancelled",
                    termination_reason="cancelled",
                )
        process = subprocess.Popen(
            command, cwd=cwd, env=None if env is None else ExplicitEnvironment(env),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            start_new_session=os.name == "posix", bufsize=0,
        )
        output = {name: _BoundedEdgeCapture(self.max_output)
                  for name in ("stdout", "stderr")}
        output_limit_reached = threading.Event()
        reader_started = {name: threading.Event() for name in ("stdout", "stderr")}

        def drain(name: str, stream) -> None:
            reader_started[name].set()
            try:
                while True:
                    chunk = stream.read(65_536)
                    if not chunk:
                        return
                    output[name].append(chunk)
                    if output[name].truncated:
                        output_limit_reached.set()
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
        # Enter both drains before timeout/cancellation can close their pipes.
        # This bounded scheduling grace remains inside the caller's deadline.
        for started in reader_started.values():
            remaining = self._remaining(deadline)
            started.wait(timeout=min(remaining, self._READER_START_GRACE)
                         if remaining is not None else self._READER_START_GRACE)
        timed_out = False
        cancelled = False
        output_overflow = False
        cancellation_probe_failed = False
        leader_reaped = False

        if cancellation is None and not self.terminate_on_output_limit:
            try:
                process.wait(timeout=self._remaining(deadline))
                leader_reaped = True
            except subprocess.TimeoutExpired:
                timed_out = True
        else:
            while True:
                if self.terminate_on_output_limit and output_limit_reached.is_set():
                    output_overflow = True
                    break
                if cancellation is not None:
                    try:
                        state = terminal_status()
                    except Exception:
                        cancellation_probe_failed = True
                        cancelled = True
                        break
                    if state is not None and (
                        type(state) is not str
                        or state not in {"cancelled", "disconnected"}
                    ):
                        cancellation_probe_failed = True
                        cancelled = True
                        break
                    if type(state) is str and state in {"cancelled", "disconnected"}:
                        cancelled = True
                        break
                remaining = self._remaining(deadline)
                if remaining is not None and remaining <= 0:
                    timed_out = True
                    break
                try:
                    process.wait(timeout=min(remaining, 0.05) if remaining is not None else 0.05)
                    leader_reaped = True
                    break
                except subprocess.TimeoutExpired:
                    continue

        # Output overflow is already a terminal condition. Waiting for reader
        # EOF here can consume the caller's entire deadline while the live
        # child keeps its pipes open; terminate first, then use the short
        # bounded drain below for retained evidence.
        if not timed_out and not cancelled and not output_overflow:
            timed_out = not self._join_readers(readers, self._remaining(deadline))

        if self.terminate_on_output_limit and output_limit_reached.is_set():
            output_overflow = True
            timed_out = False

        if timed_out or cancelled or output_overflow:
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
                130 if cancelled else 125 if output_overflow else 124,
                self._redact(_decode_bounded_output(output["stdout"].render(), self.max_output)),
                self._redact(
                    _decode_bounded_output(output["stderr"].render(), self.max_output)
                    + (
                        "\ncancellation probe failed"
                        if cancellation_probe_failed else
                        "\nprocess cancelled" if cancelled else
                        "\nprocess output limit exceeded; completion is unknown"
                        if output_overflow else
                        "\nprocess timed out"
                    )
                ),
                output["stdout"].truncated,
                output["stderr"].truncated,
                ("cancelled" if cancelled else
                 "output_overflow" if output_overflow else "timeout"),
            )
        process.stdout.close()
        process.stderr.close()
        return ProcessResult(
            command, process.returncode,
            self._redact(_decode_bounded_output(output["stdout"].render(), self.max_output)),
            self._redact(_decode_bounded_output(output["stderr"].render(), self.max_output)),
            output["stdout"].truncated,
            output["stderr"].truncated,
        )
