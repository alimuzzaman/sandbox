"""Bounded direct-argv child execution for one selected secret."""

from __future__ import annotations

import os
import re
import selectors
import shutil
import stat
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
from .process_group import terminate_process_group


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BASE_ENV = frozenset({"PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "TERM"})
ESCALATION_COMMANDS = frozenset({
    "sudo", "sudo-rs", "sudoedit", "doas", "su", "pkexec", "run0",
    "systemd-run", "machinectl",
})
_EXEC_WRAPPERS = frozenset({
    "env", "nice", "nohup", "timeout", "setsid", "stdbuf", "ionice",
    "chrt", "taskset", "command",
})
_WRAPPER_ALIASES = {
    **{name: name for name in _EXEC_WRAPPERS},
    "genv": "env", "gnice": "nice", "gnohup": "nohup",
    "gtimeout": "timeout", "gstdbuf": "stdbuf",
}
_RUN_DRAIN_IDLE_SECONDS = 0.2
_RUN_DRAIN_CAP_SECONDS = 1.0


def _resolved_executable(executable: str) -> str | None:
    candidate = (
        executable if os.path.dirname(executable)
        else shutil.which(executable, path=os.environ.get("PATH", os.defpath))
    )
    return os.path.realpath(candidate) if candidate else None


def _wrapper_command_index(name: str, argv: Sequence[str], start: int) -> int | None:
    """Return the next command position for a bounded set of exec wrappers."""
    index = start + 1
    size = len(argv)
    if name == "env":
        option_values = {"-u", "--unset", "-C", "--chdir", "-a", "--argv0"}
        options = {"-i", "--ignore-environment", "-0", "--null", "-v", "--debug"}
        while index < size:
            item = argv[index]
            if item == "--":
                index += 1
                break
            if item in option_values:
                if index + 1 >= size:
                    return None
                index += 2
                continue
            if item.startswith(("--unset=", "--chdir=", "--argv0=")):
                index += 1
                continue
            if item.startswith("-"):
                # `env -S` expands a command string and cannot be inspected
                # reliably without reproducing env's parser; fail closed.
                if item.startswith("-S") or item.startswith("--split-string"):
                    return None
                if item in options:
                    index += 1
                    continue
                if item.startswith(("-u", "-C", "-a")) and len(item) > 2:
                    index += 1
                    continue
                return None
            if "=" in item and item.split("=", 1)[0]:
                index += 1
                continue
            break
        return index if index < size else None
    if name == "timeout":
        option_values = {"-k", "--kill-after", "-s", "--signal"}
        options = {"--preserve-status", "--foreground", "-v", "--verbose"}
        while index < size and argv[index].startswith("-"):
            item = argv[index]
            if item == "--":
                index += 1
                break
            if item in option_values:
                if index + 1 >= size:
                    return None
                index += 2
            elif item in options or item.startswith(("--kill-after=", "--signal=")) \
                    or (item.startswith(("-k", "-s")) and len(item) > 2):
                index += 1
            else:
                return None
        index += 1  # duration
        return index if index < size else None
    if name == "stdbuf":
        while index < size and argv[index].startswith("-"):
            item = argv[index]
            if item in {"-i", "-o", "-e", "--input", "--output", "--error"}:
                if index + 1 >= size:
                    return None
                index += 2
            elif item.startswith(("-i", "-o", "-e")) and len(item) > 2 \
                    or item.startswith(("--input=", "--output=", "--error=")):
                index += 1
            else:
                return None
        return index if index < size else None
    if name in {"nice", "setsid", "nohup", "command"}:
        while index < size and argv[index].startswith("-"):
            item = argv[index]
            if item == "--":
                index += 1
                break
            if name == "nice" and item in {"-n", "--adjustment"}:
                if index + 1 >= size:
                    return None
                index += 2
            elif name == "nice" and (
                    item.startswith("--adjustment=") or re.fullmatch(r"-\d+", item)):
                index += 1
            elif name == "setsid" and item in {"-f", "--fork", "-w", "--wait", "-c", "--ctty"}:
                index += 1
            elif name == "command" and item == "-p":
                index += 1
            else:
                return None
        return index if index < size else None
    if name == "ionice":
        option_values = {"-c", "--class", "-n", "--classdata"}
        options = {"-t", "--ignore"}
        process_modes = {"-p", "--pid", "-P", "--pgid", "-u", "--uid"}
        while index < size and argv[index].startswith("-"):
            item = argv[index]
            if item in process_modes or any(
                    item.startswith(prefix + "=") for prefix in process_modes if prefix.startswith("--")):
                return None
            if item == "--":
                index += 1
                break
            if item in option_values:
                if index + 1 >= size:
                    return None
                index += 2
            elif item in options or item.startswith(("--class=", "--classdata=")) \
                    or (item.startswith(("-c", "-n")) and len(item) > 2):
                index += 1
            else:
                return None
        return index if index < size else None
    if name in {"chrt", "taskset"}:
        if any(item in {"-p", "--pid"} for item in argv[index:]):
            return None
        chrt_option_values = {"-T", "--sched-runtime", "-P", "--sched-period",
                              "-D", "--sched-deadline"}
        chrt_options = {
            "-a", "--all-tasks", "-b", "--batch", "-d", "--deadline", "-f", "--fifo",
            "-i", "--idle", "-o", "--other", "-r", "--rr", "-R", "--reset-on-fork",
            "-v", "--verbose",
        }
        taskset_options = {"-a", "--all-tasks", "-c", "--cpu-list"}
        while index < size and argv[index].startswith("-"):
            item = argv[index]
            if item == "--":
                index += 1
                break
            if name == "chrt" and item in chrt_option_values:
                if index + 1 >= size:
                    return None
                index += 2
            elif (name == "chrt" and (item in chrt_options or any(
                    item.startswith(option + "=") for option in chrt_option_values if option.startswith("--")))) \
                    or (name == "taskset" and item in taskset_options):
                index += 1
            else:
                return None
        # Both wrappers take a scheduling/affinity operand before the command.
        index += 1
        return index if index < size else None
    return None


def refuse_escalation(argv: Sequence[str]) -> None:
    """Reject direct or wrapper-mediated privilege changes and set-id tools."""
    if not isinstance(argv, (list, tuple)) or not argv:
        return
    index = 0
    visited = 0
    while index < len(argv) and visited < 12:
        executable = argv[index]
        if not isinstance(executable, str) or not executable:
            return
        resolved = _resolved_executable(executable)
        names = {os.path.basename(executable).casefold()}
        if resolved:
            names.add(os.path.basename(resolved).casefold())
            try:
                mode = os.stat(resolved).st_mode
            except OSError:
                mode = 0
            if mode & (stat.S_ISUID | stat.S_ISGID):
                names.add("<set-id>")
        if names & ESCALATION_COMMANDS or "<set-id>" in names:
            raise SecretBrokerError(
                "escalation_unsupported",
                "a brokered secret cannot go to a privileged command or set-id executable",
            )
        invoked_name = os.path.basename(executable).casefold()
        resolved_name = os.path.basename(resolved).casefold() if resolved else ""
        name = _WRAPPER_ALIASES.get(invoked_name) or _WRAPPER_ALIASES.get(resolved_name)
        if name is None:
            return
        next_index = _wrapper_command_index(name, argv, index)
        if next_index is None:
            raise SecretBrokerError(
                "escalation_unsupported",
                "a brokered secret cannot use an ambiguous privilege-capable command wrapper",
            )
        index = next_index
        visited += 1
    if visited >= 12:
        raise SecretBrokerError("escalation_unsupported", "secret command wrapper depth is unsupported")


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
    refuse_escalation(argv)
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
    os.set_blocking(process.stdout.fileno(), False)
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

    cleanup_ok = True
    leader_exit_code = None
    try:
        stream_open = True
        drain_cap = None
        drain_idle = None
        while stream_open or process.poll() is None:
            now = time.monotonic()
            observed = process.poll()
            if observed is not None and leader_exit_code is None:
                leader_exit_code = observed
                drain_cap = min(started + timeout_seconds, now + _RUN_DRAIN_CAP_SECONDS)
                drain_idle = min(drain_cap, now + _RUN_DRAIN_IDLE_SECONDS)
            if leader_exit_code is not None and (
                    not stream_open or now >= min(drain_cap, drain_idle)):
                break
            remaining_time = timeout_seconds - (now - started)
            if remaining_time <= 0:
                timed_out = leader_exit_code is None
                break
            wait_time = min(remaining_time, 0.1)
            if leader_exit_code is not None:
                wait_time = min(wait_time, max(0.0, min(drain_cap, drain_idle) - now))
            events = selector.select(wait_time) if stream_open else ()
            if not events:
                if not stream_open and process.poll() is None:
                    time.sleep(min(remaining_time, 0.05))
                continue
            for key, _ in events:
                try:
                    raw = os.read(key.fileobj.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if raw:
                    retain(redactor.feed(raw))
                    if leader_exit_code is not None:
                        drain_idle = min(drain_cap, time.monotonic() + _RUN_DRAIN_IDLE_SECONDS)
                else:
                    selector.unregister(key.fileobj)
                    stream_open = False
    finally:
        cleanup_ok = terminate_process_group(process)
        retain(redactor.finish())
        selector.close()
        process.stdout.close()

    if not cleanup_ok:
        raise SecretBrokerError(
            "command_cleanup_failed", "secret command process group could not be ended",
        )

    elapsed = time.monotonic() - started
    output = retained.decode("utf-8", errors="replace")
    output = _CONTROL.sub("", output)
    return RunResult(
        exit_code=None if timed_out else leader_exit_code if leader_exit_code is not None else process.returncode,
        termination="timed_out" if timed_out else "exited",
        output=output,
        truncated=truncated,
        elapsed_seconds=elapsed,
    )


__all__ = [
    "ESCALATION_COMMANDS", "SessionDisplayFilter", "minimal_environment",
    "minimal_environment_values", "refuse_escalation", "run_with_secret",
    "run_with_secrets", "validate_child_request",
]
