"""Small, dependency-free fixtures shared by Hermes unit tests."""
from __future__ import annotations

import subprocess


REMOTE = {"ssh": "sandbox@example.test", "provisioned": True}
COMMIT = "a" * 40


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)
