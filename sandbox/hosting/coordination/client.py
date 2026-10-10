"""Controller side of the spec 060 coordination program.

Every call runs the fixed ``program.PROGRAM`` over the injected ``ssh_run``
within ``TIMEOUT_SECONDS`` and validates the one JSON line it prints. A
refusal decided by the program comes back as data (``{"refused": code, ...}``)
for the lease layer to act on. Anything that leaves the decision unknown (SSH
failure, timeout, invalid output, a runtime without the capability marker)
raises ``CoordinationError`` with ``lease_authority_unavailable`` and the
migrate remedy; transport detail is never carried into the error.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Callable

from sandbox.hosting.coordination import program

TIMEOUT_SECONDS = 15
LEASE_AUTHORITY_UNAVAILABLE = "lease_authority_unavailable"

_CONTROLLER = re.compile(r"h-[0-9a-f]{16}")
_LEASE = re.compile(r"l-[0-9a-f]{16}")
_HOLD = re.compile(r"hd-[0-9a-f]{16}")
_WAITER = re.compile(r"w-[0-9a-f]{16}")
_OPERATION = re.compile(r"[a-z][a-z0-9-]{0,39}")
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_STEP = re.compile(r"[a-z][a-z0-9-]{0,39}")


class CoordinationError(RuntimeError):
    """The coordination authority could not decide; ``code`` is fixed vocabulary."""

    def __init__(self, code: str, message: str, *, remedy: str = "", reason: str | None = None):
        super().__init__(message)
        self.code = code
        self.remedy = remedy
        self.reason = reason


def controller_id(home: str | os.PathLike | None = None) -> str:
    """``h-`` + 16 hex of sha256(local Sandbox home realpath)."""
    if home is None:
        from sandbox.core._paths import BASE
        home = BASE
    digest = hashlib.sha256(os.path.realpath(home).encode("utf-8", "surrogateescape"))
    return "h-" + digest.hexdigest()[:16]


def _text(value, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit \
        and all(ch.isprintable() for ch in value)


def _check_key(state_key: str) -> str:
    parts = state_key.split("/") if isinstance(state_key, str) else []
    if len(parts) != 3 or not all(_SEGMENT.fullmatch(p) for p in parts):
        raise ValueError("hosting target key is invalid")
    return state_key


def _check(pattern: re.Pattern, value, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{what} is invalid")
    return value


def _check_holder(holder: dict, *, operation: bool = True) -> dict:
    if not isinstance(holder, dict):
        raise ValueError("holder is invalid")
    _check(_CONTROLLER, holder.get("controller_id"), "controller id")
    if not _text(holder.get("session"), 64):
        raise ValueError("holder session is invalid")
    clean = {"controller_id": holder["controller_id"], "session": holder["session"]}
    if operation:
        _check(_OPERATION, holder.get("operation"), "operation")
        if not _text(holder.get("request_id"), 64):
            raise ValueError("request id is invalid")
        clean.update(operation=holder["operation"], request_id=holder["request_id"])
    return clean


def _int(value) -> bool:
    return type(value) is int and 0 <= value < 2**40


def _lease_ok(lease) -> bool:
    return isinstance(lease, dict) and isinstance(lease.get("lease_id"), str) \
        and _LEASE.fullmatch(lease["lease_id"]) is not None \
        and _int(lease.get("expires_at")) and _int(lease.get("started_at", 0))


class CoordinationClient:
    """The coordination store on one remote, reached through ``ssh_run``."""

    def __init__(self, entry: dict, ssh_run: Callable | None = None):
        if ssh_run is None:
            from sandbox.core._remote import ssh_run as default_ssh_run
            ssh_run = default_ssh_run
        self.entry = entry
        self._ssh_run = ssh_run

    @property
    def remedy(self) -> str:
        name = self.entry.get("name") if isinstance(self.entry, dict) else None
        name = name if isinstance(name, str) and _SEGMENT.fullmatch(name) else "<remote>"
        return (f"./sb remote service migrate {name} --plan, then --confirm, so the "
                "remote runtime carries hosting coordination")

    def _unavailable(self, message: str, reason: str | None = None) -> CoordinationError:
        return CoordinationError(LEASE_AUTHORITY_UNAVAILABLE, message,
                                 remedy=self.remedy, reason=reason)

    def _call(self, request: dict) -> dict:
        command = program.remote_command(request)
        try:
            result = self._ssh_run(self.entry, command, timeout=TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - transport detail is never forwarded
            raise self._unavailable("hosting coordination is unreachable") from None
        stdout = getattr(result, "stdout", "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if getattr(result, "returncode", 1) != 0 or not isinstance(stdout, str):
            raise self._unavailable("hosting coordination program failed")
        try:
            value = json.loads(stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise self._unavailable("hosting coordination output is invalid") from None
        if not isinstance(value, dict) or value.get("ok") is not True \
                or value.get("action") != request["action"] or not _int(value.get("now")):
            raise self._unavailable("hosting coordination output is invalid")
        if value.get("refused") == LEASE_AUTHORITY_UNAVAILABLE:
            reason = value.get("reason") if isinstance(value.get("reason"), str) else None
            raise self._unavailable("hosting coordination is not available on this remote",
                                    reason=reason)
        refused = value.get("refused")
        if refused is not None and not (isinstance(refused, str) and _OPERATION.fullmatch(
                refused.replace("_", "-"))):
            raise self._unavailable("hosting coordination output is invalid")
        return value

    def _require(self, value: dict, ok: bool) -> dict:
        if not ok and "refused" not in value:
            raise self._unavailable("hosting coordination output is invalid")
        return value

    # -- capability -------------------------------------------------------

    def capability(self) -> bool:
        value = self._call({"action": "capability"})
        if not isinstance(value.get("enabled"), bool):
            raise self._unavailable("hosting coordination output is invalid")
        return value["enabled"]

    def enable(self) -> None:
        if self._call({"action": "enable"}).get("enabled") is not True:
            raise self._unavailable("hosting coordination output is invalid")

    # -- target leases ----------------------------------------------------

    def admit(self, state_key: str, holder: dict, *, ttl: int | None = None,
              waiter: dict | None = None, hold_id: str | None = None) -> dict:
        request = {"action": "admit", "state_key": _check_key(state_key),
                   "holder": _check_holder(holder)}
        if ttl is not None:
            if type(ttl) is not int or not 1 <= ttl <= program.LEASE_TTL_SECONDS:
                raise ValueError("lease ttl is out of range")
            request["ttl"] = ttl
        if waiter is not None:
            _check(_WAITER, waiter.get("waiter_id"), "waiter id")
            if not _int(waiter.get("deadline")):
                raise ValueError("waiter deadline is invalid")
            request["waiter"] = {"waiter_id": waiter["waiter_id"],
                                 "deadline": waiter["deadline"]}
        if hold_id is not None:
            request["hold_id"] = _check(_HOLD, hold_id, "hold id")
        value = self._call(request)
        admitted = value.get("admitted") is True and _lease_ok(value.get("lease")) \
            and _int(value.get("fencing_token"))
        return self._require(value, admitted)

    def renew(self, state_key: str, lease_id: str, fencing_token: int, *,
              ttl: int | None = None) -> dict:
        request = {"action": "renew", "state_key": _check_key(state_key),
                   "lease_id": _check(_LEASE, lease_id, "lease id"),
                   "fencing_token": fencing_token}
        if ttl is not None:
            request["ttl"] = ttl
        value = self._call(request)
        ok = value.get("lost") is True or (value.get("lost") is False
                                           and _lease_ok(value.get("lease")))
        return self._require(value, ok)

    def release(self, state_key: str, lease_id: str, fencing_token: int) -> dict:
        value = self._call({"action": "release", "state_key": _check_key(state_key),
                            "lease_id": _check(_LEASE, lease_id, "lease id"),
                            "fencing_token": fencing_token})
        return self._require(value, isinstance(value.get("released"), bool))

    def phase_report(self, state_key: str, lease_id: str, fencing_token: int, event: str,
                     phase_id: str, *, pid: int | None = None,
                     start: str | None = None) -> dict:
        if event not in ("start", "check", "end") or not _text(phase_id, 64):
            raise ValueError("phase report is invalid")
        request = {"action": "phase-report", "state_key": _check_key(state_key),
                   "lease_id": _check(_LEASE, lease_id, "lease id"),
                   "fencing_token": fencing_token, "event": event, "phase_id": phase_id}
        if pid is not None:
            request["pid"] = pid
        if start is not None:
            request["start"] = start
        value = self._call(request)
        ok = isinstance(value.get("recorded"), bool) or value.get("valid") is True
        return self._require(value, ok)

    # -- holds ------------------------------------------------------------

    def hold_claim(self, state_key: str, holder: dict, purpose: str, *,
                   duration: int | None = None) -> dict:
        if not _text(purpose, program.MAX_PURPOSE):
            raise ValueError("hold purpose is invalid")
        request = {"action": "hold-claim", "state_key": _check_key(state_key),
                   "holder": _check_holder(holder, operation=False), "purpose": purpose}
        if duration is not None:
            request["duration"] = duration
        value = self._call(request)
        hold = value.get("hold")
        ok = isinstance(hold, dict) and isinstance(hold.get("hold_id"), str) \
            and _HOLD.fullmatch(hold["hold_id"]) is not None
        return self._require(value, ok)

    def hold_renew(self, state_key: str, hold_id: str, *, duration: int | None = None) -> dict:
        request = {"action": "hold-renew", "state_key": _check_key(state_key),
                   "hold_id": _check(_HOLD, hold_id, "hold id")}
        if duration is not None:
            request["duration"] = duration
        value = self._call(request)
        return self._require(value, isinstance(value.get("hold"), dict))

    def hold_release(self, state_key: str, hold_id: str) -> dict:
        value = self._call({"action": "hold-release", "state_key": _check_key(state_key),
                            "hold_id": _check(_HOLD, hold_id, "hold id")})
        return self._require(value, value.get("released") is True)

    def hold_break(self, state_key: str, breaker: str, reason: str) -> dict:
        if not _text(reason, program.MAX_REASON):
            raise ValueError("break reason is invalid")
        value = self._call({"action": "hold-break", "state_key": _check_key(state_key),
                            "controller_id": _check(_CONTROLLER, breaker, "controller id"),
                            "reason": reason})
        return self._require(value, isinstance(value.get("broken"), bool))

    # -- build cap and the remote-wide lease ---------------------------------

    def build_acquire(self, state_key: str, lease_id: str) -> dict:
        value = self._call({"action": "build-acquire", "state_key": _check_key(state_key),
                            "lease_id": _check(_LEASE, lease_id, "lease id")})
        return self._require(value, value.get("acquired") is True)

    def build_release(self, state_key: str, lease_id: str) -> dict:
        value = self._call({"action": "build-release", "state_key": _check_key(state_key),
                            "lease_id": _check(_LEASE, lease_id, "lease id")})
        return self._require(value, isinstance(value.get("released"), bool))

    def shared_acquire(self, state_key: str, step: str, *,
                       bound: int = program.SHARED_MAX_SECONDS) -> dict:
        if type(bound) is not int or not 1 <= bound <= program.SHARED_MAX_SECONDS:
            raise ValueError("shared lease bound is out of range")
        value = self._call({"action": "shared-acquire", "state_key": _check_key(state_key),
                            "step": _check(_STEP, step, "shared step"), "bound": bound})
        return self._require(value, _lease_ok({**(value.get("shared_lease") or {}),
                                               "started_at": 0}))

    def shared_release(self, lease_id: str) -> dict:
        value = self._call({"action": "shared-release",
                            "lease_id": _check(_LEASE, lease_id, "lease id")})
        return self._require(value, isinstance(value.get("released"), bool))

    def cap_get(self) -> dict:
        value = self._call({"action": "cap-get"})
        return self._require(value, _int(value.get("build_cap"))
                             and isinstance(value.get("build_holders"), list))

    def cap_set(self, value: int, controller: str) -> dict:
        if type(value) is not int or not 1 <= value <= program.MAX_BUILD_CAP:
            raise ValueError("build cap is out of range")
        result = self._call({"action": "cap-set", "value": value,
                             "controller_id": _check(_CONTROLLER, controller, "controller id")})
        return self._require(result, result.get("build_cap") == value)

    def list(self) -> dict:
        value = self._call({"action": "list"})
        targets = value.get("targets")
        ok = isinstance(targets, list) and len(targets) <= program.MAX_TARGETS_LISTED \
            and _int(value.get("build_cap")) and isinstance(value.get("build_holders"), list)
        return self._require(value, ok)

    def register_controller(self, controller: str) -> dict:
        value = self._call({"action": "register-controller",
                            "controller_id": _check(_CONTROLLER, controller, "controller id")})
        return self._require(value, value.get("registered") is True)
