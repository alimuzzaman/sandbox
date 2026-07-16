from __future__ import annotations

from dataclasses import dataclass
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
        command = tuple(str(item) for item in argv)
        try:
            result = subprocess.run(
                command, cwd=cwd, env={**os.environ, **dict(env or {})},
                timeout=timeout, capture_output=True, text=True, shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            def output(value: str | bytes | None) -> str:
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")

            return ProcessResult(
                command,
                124,
                self._redact(output(exc.stdout)),
                self._redact(output(exc.stderr) + "\nprocess timed out"),
            )
        return ProcessResult(
            command, result.returncode,
            self._redact(result.stdout or ""), self._redact(result.stderr or ""),
        )
