"""Controller side of the spec 070 zone-coverage store program.

Every call runs the fixed ``program.PROGRAM`` over the injected ``ssh_run``
within ``TIMEOUT_SECONDS`` (15 s) and validates the one JSON line printed.
Refusals from the store come back as data (``{"ok": true, "refused": code, ...}``).
Anything else (SSH error, timeout, bad exit code, invalid JSON) raises
``CoverageError(COVERAGE_STORE_UNAVAILABLE, ...)``.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from sandbox.hosting.zone_coverage import (
    COVERAGE_STORE_UNAVAILABLE,
    CoverageError,
)
from sandbox.hosting.zone_coverage import program

TIMEOUT_SECONDS = 15


class ZoneCoverageClient:
    """The zone-coverage store client on one remote, reached through ``ssh_run``."""

    def __init__(self, entry: dict[str, Any], ssh_run: Callable[..., Any] | None = None):
        if ssh_run is None:
            from sandbox.core._remote import ssh_run as default_ssh_run
            ssh_run = default_ssh_run
        self.entry = entry
        self._ssh_run = ssh_run

    def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        command = program.remote_command(request)
        try:
            result = self._ssh_run(self.entry, command, timeout=TIMEOUT_SECONDS)
        except Exception as exc:
            raise CoverageError(
                COVERAGE_STORE_UNAVAILABLE,
                f"zone coverage store is unreachable: {exc}",
            ) from None

        stdout = getattr(result, "stdout", "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        rc = getattr(result, "returncode", 1)

        if rc != 0 or not isinstance(stdout, str):
            raise CoverageError(
                COVERAGE_STORE_UNAVAILABLE,
                f"zone coverage store program failed with exit code {rc}",
            )

        lines = [line.strip() for line in stdout.strip().splitlines() if line.strip()]
        if not lines:
            raise CoverageError(
                COVERAGE_STORE_UNAVAILABLE,
                "zone coverage store returned empty output",
            )

        try:
            value = json.loads(lines[-1])
        except Exception:
            raise CoverageError(
                COVERAGE_STORE_UNAVAILABLE,
                "zone coverage store output is invalid JSON",
            ) from None

        if (
            not isinstance(value, dict)
            or value.get("ok") is not True
            or value.get("action") != request.get("action")
        ):
            raise CoverageError(
                COVERAGE_STORE_UNAVAILABLE,
                "zone coverage store output structure is invalid",
            )

        return value

    def read(self, zone: str) -> dict[str, Any]:
        return self._call({"action": "read", "zone": zone})

    def list(self) -> dict[str, Any]:
        return self._call({"action": "list"})

    def begin(
        self,
        zone: str,
        kind: str,
        controller: str,
        *,
        expected_generation: int | None = None,
        expected_dependents_revision: int | None = None,
        resume: bool = False,
        zone_id: str = "",
    ) -> dict[str, Any]:
        req: dict[str, Any] = {
            "action": "begin",
            "zone": zone,
            "kind": kind,
            "controller": controller,
            "resume": resume,
            "zone_id": zone_id,
        }
        if expected_generation is not None:
            req["expected_generation"] = expected_generation
        if expected_dependents_revision is not None:
            req["expected_dependents_revision"] = expected_dependents_revision
        return self._call(req)

    def phase(
        self,
        zone: str,
        operation_id: str,
        phase: str,
        *,
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._call({
            "action": "phase",
            "zone": zone,
            "operation_id": operation_id,
            "phase": phase,
            "updates": updates or {},
        })

    def finish(
        self,
        zone: str,
        operation_id: str,
        state: str,
        *,
        interrupted_kind: str | None = None,
        raise_generation: bool = False,
    ) -> dict[str, Any]:
        return self._call({
            "action": "finish",
            "zone": zone,
            "operation_id": operation_id,
            "state": state,
            "interrupted_kind": interrupted_kind,
            "raise_generation": raise_generation,
        })

    def dependent_bind(
        self,
        zone: str,
        state_key: str,
        hostname: str,
        state: str,
        *,
        operation_id: str | None = None,
        generation: int | None = None,
    ) -> dict[str, Any]:
        return self._call({
            "action": "dependent-bind",
            "zone": zone,
            "state_key": state_key,
            "hostname": hostname,
            "state": state,
            "operation_id": operation_id,
            "generation": generation,
        })

    def dependent_release(self, zone: str, state_key: str) -> dict[str, Any]:
        return self._call({
            "action": "dependent-release",
            "zone": zone,
            "state_key": state_key,
        })
