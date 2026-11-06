"""Fail-closed local transport for the fixed zone coverage store program."""
import base64
import json
import shlex
import sys

from sandbox.hosting.zone_coverage import program
from tests.subprocess_support import run_test_process


def validate_store_command(command):
    try:
        parts = shlex.split(command)
        if (len(parts) != 5 or parts[:3] != ["python3", "-c", program.PROGRAM]
                or parts[3] != "${SANDBOX_HOME:-$HOME/sandbox}"):
            raise ValueError("unexpected command")
        request = json.loads(base64.b64decode(parts[4], validate=True))
        if not isinstance(request, dict) or program.remote_command(request) != command:
            raise ValueError("noncanonical command")
    except (ValueError, TypeError) as exc:
        raise AssertionError("unexpected store transport command") from exc
    return parts[4]


def store_transport(home):
    def run(entry, command, timeout=15):
        payload = validate_store_command(command)
        return run_test_process(
            [sys.executable, "-I", "-B", "-c", program.PROGRAM, home, payload],
            env={"SANDBOX_HOME": home, "HOME": home},
            capture_output=True, text=True, timeout=timeout)
    return run
