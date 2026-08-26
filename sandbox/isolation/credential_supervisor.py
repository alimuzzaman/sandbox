"""Lifecycle and lease supervision for the unprivileged credential broker.

This is a process-bound control object, not a service launcher.  A production
unit may wrap it, but the unit receives only broker identity and lifecycle
verbs.  Credential bytes remain inside :class:`BrokerLease.consume` and are
never accepted as supervisor arguments, environment, or serialized state.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable
import re
import threading
import time
from typing import Any

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_DRAIN_SECONDS = 5.0


class CredentialSupervisorError(RuntimeError):
    """Stable lifecycle refusal with no process or credential diagnostics."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_.-]{1,64}", code) else "supervisor_failed"
        self.message = message if isinstance(message, str) and 0 < len(message) <= 256 else "credential broker supervisor failed"
        self.retryable = bool(retryable)
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class BrokerLeaseTransfer:
    """One-use hand-off of a resolver lease to the broker process boundary."""

    __slots__ = ("_lease", "_instance_id", "_closed", "_lock")

    def __init__(self, lease, instance_id: str) -> None:
        if (not callable(getattr(lease, "consume", None))
                or not callable(getattr(lease, "invalidate", None))):
            raise CredentialSupervisorError("lease_invalid", "credential lease is invalid")
        if (not isinstance(getattr(lease, "binding_id", None), str)
                or not isinstance(getattr(lease, "binding_version", None), int)
                or lease.binding_version < 1):
            raise CredentialSupervisorError("lease_invalid", "credential lease is invalid")
        self._lease = lease
        self._instance_id = instance_id
        self._closed = False
        self._lock = threading.Lock()

    @property
    def binding_id(self) -> str:
        return self._lease.binding_id

    @property
    def binding_version(self) -> int:
        return self._lease.binding_version

    def __repr__(self) -> str:
        return (
            "BrokerLeaseTransfer("
            f"binding_id={self.binding_id!r}, version={self.binding_version})"
        )

    def consume(self, consumer: Callable[[bytes], Any]) -> Any:
        with self._lock:
            if self._closed:
                raise CredentialSupervisorError("lease_closed", "credential lease transfer is closed")
        # BrokerLease owns one-use and transient cleanup.  This wrapper never
        # receives the material itself; it only passes the trusted callback on.
        result = self._lease.consume(consumer)
        if isinstance(result, (bytes, bytearray, memoryview)):
            raise CredentialSupervisorError(
                "plaintext_denied", "credential lease consumers must return a structured result",
            )
        return result

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._lease.invalidate()


class CredentialBrokerSupervisor:
    """Close admission, invalidate leases, and drain a broker deterministically."""

    def __init__(
        self,
        broker,
        *,
        instance_id: str | None = None,
        drain_seconds: float = MAX_DRAIN_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if broker is None or not callable(getattr(broker, "request", None)) \
                or not callable(getattr(broker, "close", None)) \
                or not callable(getattr(broker, "drain", None)):
            raise ValueError("credential supervisor requires a broker")
        identity = instance_id or getattr(broker, "instance_id", None)
        if not isinstance(identity, str) or not _IDENTITY.fullmatch(identity):
            raise ValueError("credential supervisor instance identity is invalid")
        broker_identity = getattr(broker, "instance_id", None)
        if broker_identity is not None and broker_identity != identity:
            raise ValueError("credential supervisor instance identity does not match broker")
        if not isinstance(drain_seconds, (int, float)) or isinstance(drain_seconds, bool) \
                or not 0 < drain_seconds <= MAX_DRAIN_SECONDS:
            raise ValueError("credential supervisor drain deadline is invalid")
        self.broker = broker
        self.instance_id = identity
        self.drain_seconds = float(drain_seconds)
        self.clock = clock or time.monotonic
        self._condition = threading.Condition()
        self._transfers: set[BrokerLeaseTransfer] = set()
        self._state = "running"
        self._last_action = "started"
        self._atexit_registered = True
        atexit.register(self.shutdown)

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def admission_open(self) -> bool:
        with self._condition:
            return self._state == "running" and bool(getattr(self.broker, "admission_open", True))

    def __repr__(self) -> str:
        return f"CredentialBrokerSupervisor(instance_id={self.instance_id!r}, state={self.state!r})"

    def start(self) -> dict[str, Any]:
        with self._condition:
            if self._state == "closed":
                raise CredentialSupervisorError("supervisor_closed", "credential broker supervisor is closed")
            self._state = "running"
            self._last_action = "started"
        return self.status()

    def transfer(self, lease, *, instance_id: str | None = None) -> BrokerLeaseTransfer:
        with self._condition:
            if self._state != "running":
                raise CredentialSupervisorError("supervisor_closed", "credential broker admission is closed")
            target = self.instance_id if instance_id is None else instance_id
            if target != self.instance_id:
                raise CredentialSupervisorError("transport_denied", "credential lease target is not this instance")
            if not isinstance(getattr(lease, "binding_id", None), str):
                raise CredentialSupervisorError("lease_invalid", "credential lease is invalid")
            transfer = BrokerLeaseTransfer(lease, self.instance_id)
            self._transfers.add(transfer)
            return transfer

    def request(self, value, *, transport_identity: str | None = None):
        with self._condition:
            if self._state != "running":
                raise CredentialSupervisorError("supervisor_closed", "credential broker admission is closed")
        return self.broker.request(value, transport_identity=transport_identity)

    def revoke_binding(self, binding_id: str, *, binding_version: int | None = None,
                       timeout_seconds: float | None = None) -> dict[str, Any]:
        with self._condition:
            if self._state == "closed":
                return {"ok": True, "state": "closed", "drained": True, "mutated": False}
            self._state = "draining"
            self._last_action = "revoke"
            transfers = tuple(self._transfers)
        # Close new admission first.  The broker also invalidates resolver
        # leases, while transfer.close covers leases already handed off.
        invalidated = self.broker.close_binding(binding_id, binding_version=binding_version)
        for transfer in transfers:
            if transfer.binding_id == binding_id and (
                    binding_version is None or transfer.binding_version == binding_version):
                transfer.close()
        drained = self._drain(timeout_seconds)
        with self._condition:
            self._state = "running" if drained else "draining"
        return {"ok": drained, "state": "revoked" if drained else "revoking",
                "drained": drained, "invalidated": int(invalidated), "mutated": True}

    def _drain(self, timeout_seconds: float | None) -> bool:
        timeout = self.drain_seconds if timeout_seconds is None else timeout_seconds
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) \
                or timeout < 0 or timeout > MAX_DRAIN_SECONDS:
            raise ValueError("credential supervisor drain deadline is invalid")
        try:
            return bool(self.broker.drain(float(timeout)))
        except Exception:
            return False

    def shutdown(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        with self._condition:
            if self._state == "closed":
                return {"ok": True, "state": "closed", "drained": True, "mutated": False}
            self._state = "draining"
            self._last_action = "shutdown"
            transfers = tuple(self._transfers)
        for transfer in transfers:
            transfer.close()
        self.broker.close()
        drained = self._drain(timeout_seconds)
        with self._condition:
            self._state = "closed"
            self._transfers.clear()
            self._condition.notify_all()
        if self._atexit_registered:
            try:
                atexit.unregister(self.shutdown)
            except Exception:
                pass
            self._atexit_registered = False
        return {"ok": drained, "state": "closed", "drained": drained, "mutated": True}

    close = shutdown

    def status(self) -> dict[str, Any]:
        with self._condition:
            state = self._state
            transfers = len(self._transfers)
            last_action = self._last_action
        return {
            "ok": state == "running" and bool(getattr(self.broker, "admission_open", True)),
            "state": state,
            "instance_id": self.instance_id,
            "active_sessions": int(getattr(self.broker, "active_sessions", 0)),
            "lease_transfers": transfers,
            "last_action": last_action,
            "mutated": False,
        }


__all__ = [
    "BrokerLeaseTransfer", "CredentialBrokerSupervisor", "CredentialSupervisorError",
    "MAX_DRAIN_SECONDS",
]
