"""Supervised long-running secret session (spec 059).

One direct-argv child runs with brokered secrets in the foreground of the
operator's terminal. The broker enforces the lifetime itself, ends the
child's whole process group on the first of lifetime expiry, interrupt,
hangup, terminal loss or the child's own exit, and streams redacted output
live without retaining any of it. Only metadata leaves this module.
"""

from __future__ import annotations

import codecs
import os
import re
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence

from sandbox.services.redaction import StreamingRedactor

from .models import (
    DEFAULT_SESSION_SECONDS,
    END_REASONS,
    MAX_SESSION_SECONDS,
    SecretBrokerError,
    SessionResult,
)
from .runner import SessionDisplayFilter, minimal_environment_values, validate_child_request


POLL_SECONDS = 0.25
GRACE_SECONDS = 3.0
KILL_WAIT_SECONDS = 1.5
DRAIN_IDLE_SECONDS = 0.5
DRAIN_CAP_SECONDS = 5.0
_READ_BYTES = 65_536

# Every catchable signal whose default action would terminate the broker
# (spec 059 FR-010). SIGHUP is the terminal going away; the rest are
# termination requests. Left out on purpose: SIGKILL/SIGSTOP (uncatchable),
# SIGPIPE/SIGXFSZ (Python ignores them; they surface as OSError), fault
# signals (SIGSEGV, SIGBUS, SIGFPE, SIGILL, SIGABRT, SIGSYS, SIGTRAP) and
# job-control stops (SIGTSTP is ignored below; SIGTTIN/SIGTTOU stay default).
_TERMINATION_SIGNAL_NAMES = (
    "SIGINT", "SIGHUP", "SIGTERM", "SIGQUIT", "SIGUSR1", "SIGUSR2", "SIGALRM",
    "SIGVTALRM", "SIGPROF", "SIGXCPU",
)


def _termination_signals() -> dict[int, str]:
    mapping = {}
    for name in _TERMINATION_SIGNAL_NAMES:
        signum = getattr(signal, name, None)
        if signum is not None:
            mapping[signum] = "hangup" if name == "SIGHUP" else "interrupted"
    return mapping


TERMINATION_SIGNALS = _termination_signals()


def _wall_now() -> float:
    return time.time()


def _mono_now() -> float:
    return time.monotonic()


class SessionSignals:
    """Own the broker's termination signals for the length of one session.

    Every signal in ``TERMINATION_SIGNALS`` is handled, so no catchable
    termination request can kill the broker and leave the child's group
    running with the secret. Handlers only record the first end reason; the
    session loop acts on it.
    ``SIGTSTP`` is ignored so Ctrl-Z cannot stop the process that enforces
    the lifetime. Handlers are installed unconditionally, so an inherited
    ignored ``SIGHUP`` (``nohup``) is overridden. Previous dispositions are
    restored on exit, including after an exception.
    """

    def __init__(self) -> None:
        self.reason: str | None = None
        self._previous: dict[int, object] = {}

    def request_end(self, reason: str) -> None:
        if reason not in END_REASONS:
            raise ValueError("unknown session end reason")
        if self.reason is None:
            self.reason = reason

    def _handle(self, signum, _frame) -> None:
        if self.reason is None:
            self.reason = TERMINATION_SIGNALS.get(signum, "interrupted")

    def __enter__(self) -> "SessionSignals":
        try:
            for signum in TERMINATION_SIGNALS:
                self._previous[signum] = signal.signal(signum, self._handle)
            self._previous[signal.SIGTSTP] = signal.signal(signal.SIGTSTP, signal.SIG_IGN)
        except BaseException:
            self._restore()
            raise
        return self

    def __exit__(self, *_exc) -> bool:
        self._restore()
        return False

    def _restore(self) -> None:
        while self._previous:
            signum, previous = self._previous.popitem()
            signal.signal(signum, previous)


# Commands whose whole purpose is to run their argument with other
# privileges. A privileged member of the child's group cannot be signalled by
# the unprivileged broker, so the 5-second end bound (FR-011) could not hold.
ESCALATION_COMMANDS = frozenset({"sudo", "sudoedit", "doas", "su", "pkexec", "run0"})


def refuse_escalation(argv: Sequence[str]) -> None:
    """Refuse a session whose direct command escalates privileges.

    Only the executable itself is inspected: escalation deeper inside the
    child is not detectable before launch and is a documented limit, reported
    after the fact through ``SessionResult.group_ended``.
    """
    executable = argv[0] if isinstance(argv, (list, tuple)) and argv else ""
    if isinstance(executable, str) and os.path.basename(executable) in ESCALATION_COMMANDS:
        raise SecretBrokerError(
            "escalation_unsupported",
            "session mode cannot end a privileged child; run the command without "
            "sudo, sudoedit, doas, su, pkexec or run0",
        )


def validate_lifetime(lifetime_seconds: object) -> int:
    if not isinstance(lifetime_seconds, int) or isinstance(lifetime_seconds, bool) \
            or not 1 <= lifetime_seconds <= MAX_SESSION_SECONDS:
        raise SecretBrokerError(
            "lifetime_invalid", "session lifetime must be a whole number from 1 to 43200 seconds",
        )
    return lifetime_seconds


def _group_state(pgid: int) -> str:
    """``gone``, ``alive`` or ``unreachable`` (a member we may not signal).

    ``killpg`` fails with ``EPERM`` only when no member could be signalled,
    so ``unreachable`` means every process left in the group runs with other
    privileges than the broker: the child escalated and the broker cannot end
    what remains.
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "unreachable"
    return "alive"


def _group_alive(pgid: int) -> bool:
    return _group_state(pgid) != "gone"


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _wait_group(process: subprocess.Popen, pgid: int, seconds: float) -> bool:
    # Real monotonic time: termination bounds must not follow a test clock.
    deadline = time.monotonic() + seconds
    while True:
        process.poll()
        if not _group_alive(pgid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def end_process_group(process: subprocess.Popen) -> bool:
    """Polite then forced termination of the child's whole group, under 5 s.

    Returns ``True`` when the group is gone. ``False`` means a member survived
    the bound: in practice one the broker may not signal (a privileged
    descendant), which the caller reports instead of claiming the group ended.
    """
    pgid = process.pid  # start_new_session=True: the child leads its group
    process.poll()
    gone = True
    if _group_alive(pgid):
        _signal_group(pgid, signal.SIGTERM)
        if not _wait_group(process, pgid, GRACE_SECONDS):
            _signal_group(pgid, signal.SIGKILL)
            gone = _wait_group(process, pgid, KILL_WAIT_SECONDS)
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    return gone


_SGR_SPLIT = re.compile(r"(\x1b\[[0-9;]*m)")


class _ColourPreservingRedaction:
    """Redact the plain text of the filtered stream, keeping SGR where safe.

    ``redact_text`` strips the escape byte, so SGR sequences cannot ride
    through the redactor. They are taken out first, the plain projection
    (exactly what the terminal shows, minus colour) is redacted with the
    shared ``StreamingRedactor``, and each sequence is put back at its
    position only for a span the redactor emitted unchanged. A span that was
    redacted or discarded is shown as the redactor returned it, without its
    colour sequences and followed by an SGR reset, so colour can never be
    used to split or hide a value from redaction.
    """

    def __init__(self, values: tuple[bytes, ...]) -> None:
        self._values = values
        self.reset()

    def reset(self) -> None:
        self._redactor = StreamingRedactor(self._values)
        self._plain = bytearray()  # fed but not yet accounted plain bytes
        self._start = 0            # redactor offset of self._plain[0]
        self._markers: list[tuple[int, bytes]] = []  # (redactor offset, SGR)

    def feed(self, filtered: str) -> str:
        fed = bytearray()
        for index, piece in enumerate(_SGR_SPLIT.split(filtered)):
            if index % 2:
                self._markers.append((self._start + len(self._plain), piece.encode()))
            elif piece:
                raw = piece.encode()
                self._plain.extend(raw)
                fed.extend(raw)
        return self._resolve(self._redactor.feed(bytes(fed)))

    def finish(self) -> str:
        text = self._resolve(self._redactor.finish())
        tail = b"".join(sgr for _offset, sgr in self._markers)
        self._markers = []
        return text + tail.decode()

    def _resolve(self, output: bytes) -> str:
        consumed = self._redactor.consumed_bytes
        size = consumed - self._start
        if size <= 0:
            return output.decode("utf-8", errors="replace")
        segment = bytes(self._plain[:size])
        del self._plain[:size]
        inside = [item for item in self._markers if item[0] < consumed]
        self._markers = [item for item in self._markers if item[0] >= consumed]
        base = self._start
        self._start = consumed
        if output != segment:
            reset = "\x1b[0m" if inside else ""
            return output.decode("utf-8", errors="replace") + reset
        pieces = []
        cursor = 0
        for offset, sgr in inside:
            relative = max(0, offset - base)
            pieces.append(segment[cursor:relative])
            pieces.append(sgr)
            cursor = relative
        pieces.append(segment[cursor:])
        return b"".join(pieces).decode("utf-8", errors="replace")


def run_session(
    argv: Sequence[str],
    *,
    secrets: Mapping[str, str],
    lifetime_seconds: int = DEFAULT_SESSION_SECONDS,
    signals: SessionSignals | None = None,
    display: Callable[[bytes], None],
    on_start: Callable[[float], None] | None = None,
) -> SessionResult:
    """Run one supervised child and return metadata only.

    ``display`` receives redacted, terminal-filtered bytes as they arrive.
    ``on_start`` receives the wall-clock deadline once the child is running.
    A display (or ``on_start``) ``OSError`` is terminal loss: ``hangup``.
    """
    validate_child_request(argv, secrets)
    refuse_escalation(argv)
    lifetime = validate_lifetime(lifetime_seconds)
    if not callable(display):
        raise SecretBrokerError("command_invalid", "session display is invalid")
    signals = signals if signals is not None else SessionSignals()
    environment = minimal_environment_values(secrets)
    values = tuple(value.encode() for value in secrets.values())

    if signals.reason is not None:
        return SessionResult(signals.reason, None, 0.0, 0, lifetime)

    mono_start = _mono_now()
    wall_deadline = _wall_now() + lifetime
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, ValueError):
        process = None
    if process is None:
        raise SecretBrokerError("command_invalid", "secret use command could not be started")
    del environment

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    terminal = SessionDisplayFilter()
    redaction = _ColourPreservingRedaction(values)
    state = {"dropped": 0, "display_failed": False}

    def show(text: str) -> None:
        if not text or state["display_failed"]:
            return
        try:
            display(text.encode())
        except OSError:
            # Closing a terminal can surface as EIO or a broken pipe before
            # SIGHUP is handled. Either way the operator is gone.
            state["display_failed"] = True
            signals.request_end("hangup")

    def consume(raw: bytes, *, final: bool = False) -> None:
        filtered = terminal.feed(decoder.decode(raw, final=final))
        if final:
            filtered += terminal.finish()
        try:
            text = redaction.feed(filtered)
            if final:
                text += redaction.finish()
        except Exception:
            # Fail closed: drop this chunk and anything the redactor held.
            state["dropped"] += 1
            redaction.reset()
            return
        show(text)

    def expired() -> bool:
        return _wall_now() >= wall_deadline or _mono_now() - mono_start >= lifetime

    if on_start is not None:
        try:
            on_start(wall_deadline)
        except OSError:
            state["display_failed"] = True
            signals.request_end("hangup")

    assert process.stdout is not None
    stream = process.stdout
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ)
    stream_open = True
    end_reason: str | None = None
    exit_code: int | None = None
    group_ended = True
    try:
        while True:
            if signals.reason is not None:
                end_reason = signals.reason
                break
            if expired():
                end_reason = "lifetime_expired"
                break
            returncode = process.poll()
            if returncode is not None:
                end_reason, exit_code = "child_exited", returncode
                break
            if stream_open:
                for key, _events in selector.select(POLL_SECONDS):
                    raw = os.read(key.fileobj.fileno(), _READ_BYTES)
                    if raw:
                        consume(raw)
                    else:
                        selector.unregister(key.fileobj)
                        stream_open = False
            else:
                try:
                    process.wait(timeout=POLL_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
        group_ended = end_process_group(process)
        if exit_code is None and end_reason == "child_exited":
            exit_code = process.returncode
        # Every writer in the group is gone; drain what is left. Bounded in
        # case a process escaped the group and still holds the pipe open.
        drain_cap = time.monotonic() + DRAIN_CAP_SECONDS
        idle_until = time.monotonic() + DRAIN_IDLE_SECONDS
        while stream_open and time.monotonic() < min(drain_cap, idle_until):
            if not selector.select(0.05):
                continue
            raw = os.read(stream.fileno(), _READ_BYTES)
            if not raw:
                stream_open = False
                break
            consume(raw)
            idle_until = time.monotonic() + DRAIN_IDLE_SECONDS
        consume(b"", final=True)
    finally:
        selector.close()
        stream.close()
        if process.poll() is None:
            group_ended = end_process_group(process) and group_ended

    return SessionResult(
        end_reason=end_reason,
        exit_code=exit_code if end_reason == "child_exited" else None,
        elapsed_seconds=max(0.0, _mono_now() - mono_start),
        dropped_chunks=state["dropped"],
        lifetime_seconds=lifetime,
        group_ended=group_ended,
    )


__all__ = [
    "ESCALATION_COMMANDS", "SessionSignals", "TERMINATION_SIGNALS", "end_process_group",
    "refuse_escalation", "run_session", "validate_lifetime",
]
