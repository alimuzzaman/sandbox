from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from typing import Mapping, Protocol, Sequence
import os
import subprocess


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

    def __init__(self, *, max_output: int = 1_048_576,
                 secret_values: Sequence[str] = ()) -> None:
        if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output < 0:
            raise ValueError("max_output must be a non-negative integer")
        if not all(isinstance(value, str) for value in secret_values):
            raise ValueError("secret_values must contain strings")
        self.max_output = max_output
        self._secrets = tuple(value for value in secret_values if value)

    def _redact(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value[:self.max_output]

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
        process = subprocess.Popen(
            command, cwd=cwd, env={**os.environ, **dict(env or {})},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        )
        output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}

        def drain(name: str, stream) -> None:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    return
                remaining = self.max_output - len(output[name])
                if remaining > 0:
                    output[name].extend(chunk[:remaining])

        readers = tuple(
            threading.Thread(target=drain, args=(name, stream), daemon=True)
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
        )
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            for reader in readers:
                reader.join()
            process.stdout.close()
            process.stderr.close()
            return ProcessResult(
                command,
                124,
                self._redact(bytes(output["stdout"]).decode(errors="replace")),
                self._redact(bytes(output["stderr"]).decode(errors="replace") + "\nprocess timed out"),
            )
        for reader in readers:
            reader.join()
        process.stdout.close()
        process.stderr.close()
        return ProcessResult(
            command, process.returncode,
            self._redact(bytes(output["stdout"]).decode(errors="replace")),
            self._redact(bytes(output["stderr"]).decode(errors="replace")),
        )
