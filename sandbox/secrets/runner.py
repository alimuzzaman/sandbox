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
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or any(not isinstance(item, str) or not item or "\x00" in item for item in argv)
    ):
        raise SecretBrokerError("command_invalid", "secret use requires a non-empty direct command")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) \
            or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise SecretBrokerError("command_invalid", "secret use timeout must be between 1 and 1800 seconds")
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) \
            or not 1 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise SecretBrokerError("command_invalid", "secret use output limit is invalid")
    if not isinstance(secrets, Mapping) or not secrets:
        raise SecretBrokerError("key_empty", "at least one secret is required")
    for destination, value in secrets.items():
        validate_destination(destination)
        if not isinstance(value, str) or value == "":
            raise SecretBrokerError("key_empty", "empty secrets cannot be used by a child process")

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
