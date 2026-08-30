"""The only approved captured-subprocess boundary for tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import subprocess
from typing import Any

from sandbox.services.environment import compatible_subprocess_environment


def synthetic_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a compatibility-only child environment with synthetic overrides."""
    return compatible_subprocess_environment(overrides)


def run_test_process(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = 90,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run one captured test child without implicit parent-env inheritance."""
    if "shell" in kwargs or "timeout" in kwargs:
        raise TypeError("run_test_process owns shell and timeout")
    return subprocess.run(
        list(argv),
        env=synthetic_environment(env),
        timeout=timeout,
        shell=False,
        **kwargs,
    )
