"""Closed controller-role composition for the standalone credential v2 graph."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
import re
import socket
import time
import uuid
from typing import Any, Callable

from .credential_controller_audit_v2 import (
    ControllerAuditAuthorityV2,
    DurableAuditRepositoryV2,
)
from .credential_controller_authority_v2 import (
    ControllerAuthorityInterfaces,
    ControllerOperationAuthorityV2,
)
from .credential_controller_lifecycle_v2 import ControllerLifecycleAuthorityV2
from .credential_controller_service_v2 import (
    ControllerServiceConfig,
    ControllerServiceV2Error,
    PersistentControllerService,
    abstract_controller_address,
)
from .credential_service_runtime_v2 import (
    pinned_process_identity_observer_v2,
    prepare_controller_process_runtime_v2,
)


CONTROLLER_PROTOCOL_V2 = "credential-broker-controller-v2"
MAX_PROC_UNIX_BYTES_V2 = 65536
_MACHINE = re.compile(r"^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$")


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except Exception:
        return False


def parse_proc_net_unix_v2(payload: str, expected_address: bytes) -> bool:
    """Prove exactly one listening seqpacket row for the fixed abstract name."""

    if (not isinstance(payload, str) or not isinstance(expected_address, bytes)
            or not expected_address.startswith(b"\0")):
        raise ControllerServiceV2Error("controller_listener_unavailable")
    try:
        if len(payload.encode("ascii", errors="strict")) > MAX_PROC_UNIX_BYTES_V2:
            raise ValueError
        expected = "@" + expected_address[1:].decode("ascii")
        lines = payload.splitlines()
        if not lines or not lines[0].startswith("Num"):
            raise ValueError
        matches = []
        for line in lines[1:]:
            fields = line.split()
            if len(fields) not in {7, 8}:
                raise ValueError
            if len(fields) == 8 and fields[7] == expected:
                matches.append(fields)
        return bool(len(matches) == 1 and matches[0][3] == "00010000"
                    and matches[0][4] == "0005" and matches[0][5] == "01"
                    and matches[0][6].isdigit())
    except (UnicodeError, ValueError):
        raise ControllerServiceV2Error("controller_listener_unavailable") from None


def observe_exact_controller_listener_v2(machine_id: str, broker_digest: str, *,
                                         reader: Callable[[], str]) -> bool:
    if not callable(reader):
        raise ControllerServiceV2Error("controller_listener_unavailable")
    try:
        payload = reader()
        address = abstract_controller_address(machine_id, broker_digest)
    except Exception:
        raise ControllerServiceV2Error("controller_listener_unavailable") from None
    if parse_proc_net_unix_v2(payload, address) is not True:
        raise ControllerServiceV2Error("controller_listener_unavailable")
    return True


def wait_for_exact_controller_listener_v2(
        machine_id: str, broker_digest: str, *, reader: Callable[[], str],
        monotonic: Callable[[], float], deadline: float,
        wait: Callable[[float], Any]) -> bool:
    """Boundedly wait for the broker-owned listener without connecting."""

    if (not callable(reader) or not callable(monotonic) or not callable(wait)
            or not _finite_number(deadline)):
        raise ControllerServiceV2Error("controller_listener_unavailable")
    address = abstract_controller_address(machine_id, broker_digest)
    while True:
        try:
            now = monotonic()
            if not _finite_number(now) or now >= deadline:
                raise ControllerServiceV2Error("controller_listener_unavailable")
            payload = reader()
            if parse_proc_net_unix_v2(payload, address):
                return True
            remaining = deadline - now
            wait(min(0.05, remaining))
        except ControllerServiceV2Error:
            raise
        except Exception:
            raise ControllerServiceV2Error("controller_listener_unavailable") from None


def fixed_controller_connector_v2(family: int, kind: int, protocol: int,
                                  *, socket_factory=socket.socket):
    """Create the sole outgoing socket with verified packet credentials enabled."""

    if ((family, kind, protocol) != (socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
            or not callable(socket_factory)
            or type(getattr(socket, "SO_PASSCRED", None)) is not int):
        raise ControllerServiceV2Error("controller_connection_refused")
    connection = None
    try:
        connection = socket_factory(family, kind, protocol)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        readback = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED)
        if type(readback) is not int or readback != 1:
            raise OSError
        return connection
    except Exception:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        raise ControllerServiceV2Error("controller_connection_refused") from None


@dataclass(frozen=True, slots=True)
class ControllerAuthorityProviderV2:
    interfaces: ControllerAuthorityInterfaces
    audit_repository: DurableAuditRepositoryV2
    decision_id_factory: Callable[[], str]
    lease_id_factory: Callable[[], str]
    commit_id_factory: Callable[[], str]
    phase_id_factory: Callable[[], str]

    def __post_init__(self) -> None:
        if (not isinstance(self.interfaces, ControllerAuthorityInterfaces)
                or not isinstance(self.audit_repository, DurableAuditRepositoryV2)
                or any(not callable(getattr(self, name)) for name in (
                    "decision_id_factory", "lease_id_factory",
                    "commit_id_factory", "phase_id_factory"))):
            raise ControllerServiceV2Error("controller_authority_unavailable")


class ControllerRoleRuntimeV2:
    """Own one controller service and exactly one observed broker connection."""

    __slots__ = ("prepared", "service", "operation_authority", "audit_authority",
                 "lifecycle_authority", "_provider", "_started", "_terminal",
                 "_stop_attempted", "_stop_result", "_first_failure")

    def __init__(self, prepared, *, provider: ControllerAuthorityProviderV2 | None,
                 epoch_factory=lambda: uuid.uuid4().hex,
                 owner_factory=lambda: "controller-session-" + uuid.uuid4().hex[:16]):
        try:
            controller = prepared["controller_plan"]
            broker = prepared["broker_plan"]
            controller_identity = prepared["controller_identity"]
            broker_pinner = prepared["broker_identity_pinner"]
            plan_identity = hashlib.sha256(
                controller.canonical_bytes + b"\0" + broker.canonical_bytes).hexdigest()
        except Exception:
            raise ControllerServiceV2Error("service_composition_invalid") from None
        self.prepared = prepared
        self.service = None
        self.operation_authority = None
        self.audit_authority = None
        self.lifecycle_authority = None
        self._provider = provider
        self._started = False
        self._terminal = (epoch_factory, owner_factory)
        self._stop_attempted = False
        self._stop_result = None
        self._first_failure = None
        if (not isinstance(plan_identity, str) or not callable(broker_pinner)
                or controller_identity.uid != controller.document["service_uid"]):
            raise ControllerServiceV2Error("service_composition_invalid")

    def start_closed(self, *, platform: str, effective_uid: int, effective_gid: int,
                     listener_reader, connector, now_ms: int,
                     monotonic=time.monotonic, closer=os.close,
                     so_peercred=None, scm_credentials=None, scm_rights=None,
                     listener_monotonic=time.monotonic,
                     listener_deadline=None, listener_wait=time.sleep) -> dict[str, Any]:
        if self._started or not isinstance(self._terminal, tuple):
            raise ControllerServiceV2Error("controller_start_refused")
        controller_plan = self.prepared["controller_plan"]
        broker_plan = self.prepared["broker_plan"]
        controller_identity = self.prepared["controller_identity"]
        if (platform != "linux" or effective_uid != controller_identity.uid
                or effective_gid != controller_identity.gid):
            raise ControllerServiceV2Error("controller_start_refused")
        self._started = True
        try:
            self.prepared["controller_observer"](
                os.getpid(), effective_uid, effective_gid)
            if listener_deadline is None:
                started_listener_clock = listener_monotonic()
                if not _finite_number(started_listener_clock):
                    raise ControllerServiceV2Error("controller_listener_unavailable")
                selected_deadline = started_listener_clock + 5.0
            else:
                selected_deadline = listener_deadline
            wait_for_exact_controller_listener_v2(
                controller_plan.machine_id, broker_plan.document["broker_digest"],
                reader=listener_reader, monotonic=listener_monotonic,
                deadline=selected_deadline, wait=listener_wait)
            broker_identity = self.prepared["broker_identity_pinner"](broker_plan)
            broker_observer = pinned_process_identity_observer_v2(broker_identity)
            config = ControllerServiceConfig(
                machine_id=controller_plan.machine_id,
                controller=controller_identity, broker=broker_identity,
                policy_digest=broker_plan.document["policy_digest"],
                egress_digest=broker_plan.document["egress_digest"],
                broker_digest=broker_plan.document["broker_digest"],
                proof_digest=broker_plan.document["proof_digest"],
                effective_isolation_digest=broker_plan.document[
                    "effective_isolation_digest"],
                evidence_id=broker_plan.document["evidence_id"])
            epoch_factory, owner_factory = self._terminal
            self.service = PersistentControllerService(
                config, epoch_factory=epoch_factory, owner_factory=owner_factory)
            self._terminal = None
            self.service.start(platform="linux", enabled=True)
            result = self.service.connect(
                connector=connector, observer=broker_observer,
                now_ms=now_ms, monotonic=monotonic,
                so_peercred=(getattr(socket, "SO_PEERCRED", None)
                             if so_peercred is None else so_peercred),
                scm_credentials=(getattr(socket, "SCM_CREDENTIALS", None)
                                 if scm_credentials is None else scm_credentials),
                scm_rights=(getattr(socket, "SCM_RIGHTS", None)
                            if scm_rights is None else scm_rights), closer=closer)
            if not result.get("ok") or not self.service.session.authenticated:
                raise ControllerServiceV2Error("controller_handshake_refused")
            session = self.service.session
            plan_identity = hashlib.sha256(
                self.prepared["controller_plan"].canonical_bytes + b"\0" +
                self.prepared["broker_plan"].canonical_bytes).hexdigest()
            self.lifecycle_authority = ControllerLifecycleAuthorityV2(
                session, plan_identity=plan_identity)
            if self._provider is None or config.evidence_id is None:
                return {"ok": True, "code": "controller_closed",
                        "admission_open": False, "authorities_ready": False}
            self.operation_authority = ControllerOperationAuthorityV2(
                session, self._provider.interfaces,
                decision_id_factory=self._provider.decision_id_factory,
                lease_id_factory=self._provider.lease_id_factory)
            self.audit_authority = ControllerAuditAuthorityV2(
                session, self._provider.audit_repository,
                commit_id_factory=self._provider.commit_id_factory,
                phase_id_factory=self._provider.phase_id_factory)
            return {"ok": True, "code": "controller_closed",
                    "admission_open": False, "authorities_ready": True}
        except Exception as exc:
            first = (exc.code if isinstance(exc, ControllerServiceV2Error)
                     else "controller_start_refused")
            self._first_failure = self._first_failure or first
            self._terminal = self._first_failure
            self.stop()
            raise ControllerServiceV2Error(self._first_failure) from None

    def run_closed(self, *, poll: Callable[[Any], str]) -> dict[str, Any]:
        """Remain closed until an injected signal/EOF poll reports termination."""

        if (not callable(poll) or self.service is None
                or self.service.session is None or not self.service.session.authenticated):
            raise ControllerServiceV2Error("controller_start_refused")
        while True:
            try:
                state = poll(self.service.session.connection)
            except Exception:
                state = "failed"
            if state in {"signal", "eof"}:
                return {"ok": True, "code": f"controller_{state}",
                        "admission_open": False}
            if state != "waiting":
                self._first_failure = self._first_failure or "controller_connection_refused"
                raise ControllerServiceV2Error(self._first_failure)

    def stop(self) -> dict[str, Any]:
        if self._stop_attempted:
            return dict(self._stop_result)
        self._stop_attempted = True
        try:
            result = (self.service.stop() if self.service is not None
                      else {"ok": True, "code": "controller_stopped",
                            "admission_open": False})
            if (not isinstance(result, dict)
                    or set(result) != {"ok", "code", "admission_open"}
                    or type(result.get("ok")) is not bool
                    or not isinstance(result.get("code"), str)
                    or result.get("admission_open") is not False):
                raise ValueError
        except Exception:
            result = {"ok": False, "code": "controller_cleanup_failed",
                      "admission_open": False}
        if self._first_failure is not None:
            result = {"ok": False, "code": self._first_failure,
                      "admission_open": False}
        self.operation_authority = None
        self.audit_authority = None
        self.lifecycle_authority = None
        self._stop_result = dict(result)
        return dict(result)


def prepare_controller_role_v2(machine_id: str, *, service_gid: int,
                               provider_factory: Callable[[str], Any]):
    """Use only the fixed application-context provider factory; absence closes."""

    if (not isinstance(machine_id, str) or _MACHINE.fullmatch(machine_id) is None
            or not callable(provider_factory)):
        raise ControllerServiceV2Error("controller_authority_unavailable")
    prepared = prepare_controller_process_runtime_v2(
        machine_id, service_gid=service_gid)
    try:
        provider = provider_factory(machine_id)
    except Exception:
        raise ControllerServiceV2Error("controller_authority_unavailable") from None
    if provider is not None and not isinstance(provider, ControllerAuthorityProviderV2):
        raise ControllerServiceV2Error("controller_authority_unavailable")
    return ControllerRoleRuntimeV2(prepared, provider=provider)


__all__ = [
    "CONTROLLER_PROTOCOL_V2", "ControllerAuthorityProviderV2",
    "ControllerRoleRuntimeV2", "observe_exact_controller_listener_v2",
    "fixed_controller_connector_v2", "parse_proc_net_unix_v2",
    "prepare_controller_role_v2", "wait_for_exact_controller_listener_v2",
]
