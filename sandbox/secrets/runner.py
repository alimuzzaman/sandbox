"""Bounded direct-argv child execution for one selected secret."""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence

from sandbox.services.redaction import StreamingRedactor

from .models import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT_SECONDS,
    RunResult,
    SecretBrokerError,
)
from .policy import validate_destination


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BASE_ENV = frozenset({"PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "TERM"})


def validate_child_request(argv: Sequence[str], secrets: Mapping[str, str]) -> None:
    """Check the direct argv and the selected values shared by run and session."""
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or not isinstance(argv[0], str) or not argv[0]
        # Empty arguments are legitimate (e.g. `--prefix ""`); only the
        # executable must be non-empty. NUL can never cross exec.
        or any(not isinstance(item, str) or "\x00" in item for item in argv)
    ):
        raise SecretBrokerError("command_invalid", "secret use requires a non-empty direct command")
    if not isinstance(secrets, Mapping) or not secrets:
        raise SecretBrokerError("key_empty", "at least one secret is required")
    for destination, value in secrets.items():
        validate_destination(destination)
        if not isinstance(value, str) or value == "":
            raise SecretBrokerError("key_empty", "empty secrets cannot be used by a child process")


# Session display (spec 059 decision 3). Ordinary ``run`` keeps ``_CONTROL``.
_TEXT_RUN = re.compile(r"[^\x00-\x08\x0b-\x1f\x7f-\x9f]+")
_SGR_PARAMETERS = re.compile(r"[0-9;]*")
_STRING_INTRODUCERS = frozenset("]PX^_")
_C1_STRING_INTRODUCERS = frozenset("\x90\x98\x9d\x9e\x9f")
_MAX_CSI_CHARS = 256
_MAX_STRING_CHARS = 65_536


class SessionDisplayFilter:
    """Stateful terminal-safety filter for the live session stream.

    Complete SGR sequences (``ESC [ <digits;> m``) pass unchanged. Every other
    C0/C1 control character except tab, newline and carriage return is
    removed, and every other escape sequence (CSI, OSC, DCS and friends,
    two-byte escapes, malformed or overlong SGR) is removed whole, so no
    ``[31`` residue reaches the terminal. An escape sequence that is still
    incomplete at the end of a chunk is held for the next chunk and dropped
    by ``finish``.
    """

    def __init__(self) -> None:
        self._state = "text"
        self._csi = []  # characters after the introducer of the current CSI
        self._csi_length = 0
        self._string_length = 0
        self._string_escape = False

    def feed(self, text: str) -> str:
        out: list[str] = []
        index = 0
        size = len(text)
        while index < size:
            state = self._state
            char = text[index]
            if state == "text":
                match = _TEXT_RUN.match(text, index)
                if match:
                    out.append(match.group())
                    index = match.end()
                    continue
                index += 1
                if char in "\t\n\r":
                    out.append(char)
                elif char == "\x1b":
                    self._state = "escape"
                elif char == "\x9b":
                    self._start_csi()
                elif char in _C1_STRING_INTRODUCERS:
                    self._start_string()
                continue
            if state == "escape":
                if char == "[":
                    index += 1
                    self._start_csi()
                elif char in _STRING_INTRODUCERS:
                    index += 1
                    self._start_string()
                elif "\x20" <= char <= "\x2f":
                    index += 1
                    self._state = "nf"
                elif "\x30" <= char <= "\x7e":
                    index += 1
                    self._state = "text"
                else:
                    # Not an escape sequence: drop the escape, reprocess char.
                    self._state = "text"
                continue
            if state == "nf":
                if "\x20" <= char <= "\x2f":
                    index += 1
                elif "\x30" <= char <= "\x7e":
                    index += 1
                    self._state = "text"
                else:
                    self._state = "text"
                continue
            if state == "csi":
                if "\x40" <= char <= "\x7e":
                    index += 1
                    parameters = "".join(self._csi)
                    if char == "m" and _SGR_PARAMETERS.fullmatch(parameters):
                        out.append("\x1b[" + parameters + "m")
                    self._reset_csi()
                elif "\x20" <= char <= "\x3f" and self._csi_length < _MAX_CSI_CHARS:
                    index += 1
                    self._csi.append(char)
                    self._csi_length += 1
                else:
                    # Control character, non-ASCII, or overlong: the sequence is
                    # malformed. Drop it whole and reprocess this character.
                    self._reset_csi()
                continue
            # state == "string": OSC/DCS/SOS/PM/APC, ended by BEL or ST.
            if self._string_escape:
                self._string_escape = False
                if char == "\\":
                    index += 1
                    self._state = "text"
                else:
                    # ESC inside a string aborts it and starts a new escape.
                    self._state = "escape"
                continue
            index += 1
            if char in "\x07\x9c":
                self._state = "text"
            elif char == "\x1b":
                self._string_escape = True
            elif self._string_length < _MAX_STRING_CHARS:
                self._string_length += 1
            else:
                # Unterminated and overlong: abandon the sequence so a broken
                # child cannot hide the rest of the session. What follows is
                # shown as filtered text, never as a control sequence.
                self._state = "text"
        return "".join(out)

    def finish(self) -> str:
        self._state = "text"
        self._reset_csi()
        self._string_escape = False
        return ""

    def _start_csi(self) -> None:
        self._state = "csi"
        self._csi = []
        self._csi_length = 0

    def _reset_csi(self) -> None:
        self._state = "text"
        self._csi = []
        self._csi_length = 0

    def _start_string(self) -> None:
        self._state = "string"
        self._string_length = 0
        self._string_escape = False


def minimal_environment_values(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping) or not values:
        raise SecretBrokerError("key_empty", "at least one secret is required")
    result = {
        key: item
        for key, item in os.environ.items()
        if key in _BASE_ENV or key.startswith("LC_")
    }
    for destination, value in values.items():
        result[validate_destination(destination)] = value
    return result


def minimal_environment(destination: str, value: str) -> dict[str, str]:
    return minimal_environment_values({destination: value})


def run_with_secret(
    argv: Sequence[str],
    *,
    destination: str,
    value: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> RunResult:
    return run_with_secrets(
        argv, secrets={destination: value}, timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def run_with_secrets(
    argv: Sequence[str],
    *,
    secrets: Mapping[str, str],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> RunResult:
    validate_child_request(argv, secrets)
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) \
            or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise SecretBrokerError("command_invalid", "secret use timeout must be between 1 and 1800 seconds")
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) \
            or not 1 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise SecretBrokerError("command_invalid", "secret use output limit is invalid")

    environment = minimal_environment_values(secrets)
    redactor = StreamingRedactor(tuple(value.encode() for value in secrets.values()))
    started = time.monotonic()
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
    except (OSError, ValueError) as exc:
        raise SecretBrokerError("command_invalid", "secret use command could not be started") from exc

    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    retained = bytearray()
    truncated = False
    timed_out = False

    def retain(chunk: bytes) -> None:
        nonlocal truncated
        remaining = max_output_bytes - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True

    try:
        while selector.get_map():
            remaining_time = timeout_seconds - (time.monotonic() - started)
            if remaining_time <= 0:
                timed_out = True
                break
            events = selector.select(min(remaining_time, 0.1))
            if not events:
                if process.poll() is not None:
                    raw = os.read(process.stdout.fileno(), 65_536)
                    if raw:
                        retain(redactor.feed(raw))
                        continue
                    selector.unregister(process.stdout)
                continue
            for key, _ in events:
                raw = os.read(key.fileobj.fileno(), 65_536)
                if raw:
                    retain(redactor.feed(raw))
                else:
                    selector.unregister(key.fileobj)
        if timed_out:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=2)
        else:
            process.wait(timeout=2)
        retain(redactor.finish())
    finally:
        selector.close()
        process.stdout.close()

    elapsed = time.monotonic() - started
    output = retained.decode("utf-8", errors="replace")
    output = _CONTROL.sub("", output)
    return RunResult(
        exit_code=None if timed_out else process.returncode,
        termination="timed_out" if timed_out else "exited",
        output=output,
        truncated=truncated,
        elapsed_seconds=elapsed,
    )
