"""Secret-free derived config and injected local lifecycle ordering for v2.

This module plans and verifies lifecycle work.  It does not install units,
start processes, read repositories, or expose a public runtime composition.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
import json
import re
import threading
from types import MappingProxyType
from typing import Any, Mapping

from .credential_controller_protocol_v2 import PROTOCOL, digest_document
from .credential_controller_service_v2 import (
    ControllerBrokerSession,
    ControllerServiceV2Error,
)
from .credential_controller_audit_v2 import ControllerAuditAuthorityV2


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MACHINE = re.compile(r"^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}$")
_FORBIDDEN_TEXT = re.compile(
    r"(?i)(secret|token|password|authorization[_-]|api[_-]?key|source[_-]?(?:ref|handle)|"
    r"operation-[a-z0-9]|lease-[a-z0-9]|audit-[a-z0-9]|binding-[a-z0-9]|"
    r"request[_-]?digest|header[_-]|body[_-]|argv[_-]|pid[_-])"
)
LIFECYCLE_VERBS_V2 = (
    "credential-controller-configure-v2", "credential-controller-start-v2",
    "credential-controller-status-v2", "credential-controller-stop-v2",
    "credential-broker-configure-v2", "credential-broker-start-v2",
    "credential-broker-status-v2", "credential-broker-stop-v2",
)
_PLAN_KEYS = frozenset((
    "schema_version", "machine_id", "component", "unit_identity", "service_uid",
    "service_gid", "executable_digest", "config_identity", "policy_digest",
    "egress_digest", "broker_digest", "proof_digest",
    "effective_isolation_digest", "evidence_id", "bounds",
    "peer_executable_digest", "peer_config_digest",
    "controller_endpoint_identity", "lease_endpoint_identity",
    "guest_endpoint_identity",
    "own_config_digest",
))
_BOUND_KEYS = frozenset((
    "controller_frame_bytes", "lease_frame_bytes", "lease_ack_bytes",
    "handshake_timeout_ms", "audit_ack_timeout_ms", "audit_transport_retries",
    "lease_ack_timeout_ms", "drain_timeout_ms", "max_active_operations",
))
_FIXED_BOUNDS = {
    "controller_frame_bytes": 16384, "lease_frame_bytes": 732,
    "lease_ack_bytes": 444, "handshake_timeout_ms": 1000,
    "audit_ack_timeout_ms": 1000, "audit_transport_retries": 1,
    "lease_ack_timeout_ms": 1000, "drain_timeout_ms": 5000,
    "max_active_operations": 16,
}
_QUIESCE_RECEIPT_ISSUER = object()
_PUBLIC_ACCEPTANCE_RECEIPT_ISSUER = object()


class LifecycleV2Error(RuntimeError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "lifecycle_refused"
        super().__init__(self.code)


class VerifiedQuiesceReceiptV2:
    """Opaque one-use proof of one session-accepted exact QUIESCE acknowledgement."""

    __slots__ = ("machine_id", "broker_epoch", "controller_epoch", "reply_to",
                 "quiesce_digest", "drain_deadline_unix_ms", "drain_status",
                 "active_operation_count", "reason_code", "configured_digests",
                 "session_owner", "plan_identity", "_used", "_issuer")

    def __init__(self, issuer, **values) -> None:
        if issuer is not _QUIESCE_RECEIPT_ISSUER:
            raise LifecycleV2Error("quiesce_incomplete")
        for name in self.__slots__:
            if name not in {"_used", "_issuer"}:
                object.__setattr__(self, name, values[name])
        object.__setattr__(self, "_used", False)
        object.__setattr__(self, "_issuer", issuer)

    def __setattr__(self, name, value) -> None:
        del name, value
        raise LifecycleV2Error("quiesce_incomplete")

    def consume(self) -> None:
        if self._issuer is not _QUIESCE_RECEIPT_ISSUER or self._used:
            raise LifecycleV2Error("quiesce_incomplete")
        object.__setattr__(self, "_used", True)


class _PublicAcceptanceLifecycleReceiptV2:
    """Opaque one-attempt snapshot minted by the exact lifecycle authority."""

    __slots__ = ("authority", "session", "action", "generation", "initial_state",
                 "active_operation_count", "_issuer")

    def __init__(self, issuer, *, authority, action, generation, initial_state,
                 active_operation_count) -> None:
        if issuer is not _PUBLIC_ACCEPTANCE_RECEIPT_ISSUER:
            raise LifecycleV2Error("public_acceptance_refused")
        object.__setattr__(self, "authority", authority)
        object.__setattr__(self, "session", authority.session)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "initial_state", initial_state)
        object.__setattr__(self, "active_operation_count", active_operation_count)
        object.__setattr__(self, "_issuer", issuer)

    def __setattr__(self, name, value) -> None:
        del name, value
        raise LifecycleV2Error("public_acceptance_refused")


def canonical_config_bytes(value: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping) or set(value) != _PLAN_KEYS:
        raise LifecycleV2Error("config_invalid")
    component = value.get("component")
    machine_id = value.get("machine_id")
    exact_uid = {"controller": 992, "broker": 993}.get(component)
    exact_unit = (f"sandbox-credential-{component}-v2@{machine_id}.service"
                  if component in {"controller", "broker"} else None)
    if (value.get("schema_version") != 2
            or value.get("component") not in {"controller", "broker"}
            or not isinstance(value.get("machine_id"), str)
            or _MACHINE.fullmatch(value["machine_id"]) is None
            or value.get("service_uid") != exact_uid
            or type(value.get("service_gid")) is not int or value["service_gid"] < 1
            or not isinstance(value.get("unit_identity"), str)
            or value.get("unit_identity") != exact_unit
            or not isinstance(value.get("config_identity"), str)
            or value.get("config_identity") != f"sandbox-v2-{component}-config"
            or not isinstance(value.get("bounds"), Mapping)
            or set(value["bounds"]) != _BOUND_KEYS or dict(value["bounds"]) != _FIXED_BOUNDS):
        raise LifecycleV2Error("config_invalid")
    for name in ("executable_digest", "peer_executable_digest", "peer_config_digest",
                 "own_config_digest",
                 "policy_digest", "egress_digest", "broker_digest",
                 "proof_digest", "effective_isolation_digest"):
        if not isinstance(value.get(name), str) or _DIGEST.fullmatch(value[name]) is None:
            raise LifecycleV2Error("config_invalid")
    evidence = value.get("evidence_id")
    if evidence is not None and (not isinstance(evidence, str)
                                 or re.fullmatch(r"evidence-[a-z0-9]{7,54}", evidence) is None):
        raise LifecycleV2Error("config_invalid")
    for name in ("controller_endpoint_identity", "lease_endpoint_identity",
                 "guest_endpoint_identity"):
        if (not isinstance(value.get(name), str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}", value[name])):
            raise LifecycleV2Error("config_invalid")
    for item in value.values():
        selected = item.values() if isinstance(item, Mapping) else (item,)
        for scalar in selected:
            if isinstance(scalar, bytes) or (isinstance(scalar, str)
                                             and _FORBIDDEN_TEXT.search(scalar)):
                raise LifecycleV2Error("config_forbidden")
    if (value.get("controller_endpoint_identity") != "v2-controller.sock"
            or value.get("lease_endpoint_identity") != "v2-lease.sock"
            or value.get("guest_endpoint_identity") != "v2-guest.sock"):
        raise LifecycleV2Error("config_invalid")
    plain = {key: (dict(item) if isinstance(item, Mapping) else item)
             for key, item in value.items()}
    encoded = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(encoded) > 16384 or _FORBIDDEN_TEXT.search(encoded.decode("ascii")):
        # Field names in the reviewed schema are allowed; scan only values.
        for name, item in value.items():
            if name in {"component", "unit_identity", "config_identity"} \
                    and isinstance(item, str) and _FORBIDDEN_TEXT.search(item):
                raise LifecycleV2Error("config_forbidden")
        if len(encoded) > 16384:
            raise LifecycleV2Error("config_invalid")
    return encoded


@dataclass(frozen=True, slots=True)
class DerivedServiceConfigV2:
    document: Mapping[str, Any]
    canonical_bytes: bytes
    config_digest: str
    machine_id: str
    component: str
    service_gid: int

    def __post_init__(self) -> None:
        try:
            encoded = canonical_config_bytes(self.document)
        except Exception:
            raise LifecycleV2Error("config_invalid") from None
        if (type(self.document) is not MappingProxyType
                or type(self.document.get("bounds")) is not MappingProxyType
                or type(self.canonical_bytes) is not bytes
                or self.canonical_bytes != encoded
                or not isinstance(self.config_digest, str)
                or self.config_digest != hashlib.sha256(encoded).hexdigest()
                or self.machine_id != self.document.get("machine_id")
                or self.component != self.document.get("component")
                or self.service_gid != self.document.get("service_gid")):
            raise LifecycleV2Error("config_invalid")

    @classmethod
    def derive(cls, document: Mapping[str, Any]) -> "DerivedServiceConfigV2":
        try:
            copied = json.loads(json.dumps(document))
        except Exception:
            raise LifecycleV2Error("config_invalid") from None
        encoded = canonical_config_bytes(copied)
        copied["bounds"] = MappingProxyType(dict(copied["bounds"]))
        frozen = MappingProxyType(copied)
        return cls(document=frozen, canonical_bytes=encoded,
                   config_digest=hashlib.sha256(encoded).hexdigest(),
                   machine_id=copied["machine_id"], component=copied["component"],
                   service_gid=copied["service_gid"])


@dataclass(frozen=True, slots=True)
class OwnershipObservationV2:
    regular_file: bool
    symlink: bool
    owner_uid: int
    group_gid: int
    mode: int
    size: int
    canonical_bytes: bytes
    digest: str


def verify_owned_config(plan: DerivedServiceConfigV2, observed: OwnershipObservationV2) -> bool:
    if not isinstance(plan, DerivedServiceConfigV2) or not isinstance(observed, OwnershipObservationV2):
        raise LifecycleV2Error("config_observation_invalid")
    return bool(
        observed.regular_file is True and observed.symlink is False
        and observed.owner_uid == 0 and observed.group_gid == plan.service_gid
        and observed.mode == 0o640 and observed.size == len(plan.canonical_bytes)
        and observed.size <= 16384 and observed.canonical_bytes == plan.canonical_bytes
        and observed.digest == plan.config_digest
    )


class FixedLifecycleExecutorV2(ABC):
    """One fixed-verb local execution seam used only by managed service planning."""

    @abstractmethod
    def execute(self, verb: str, plan: DerivedServiceConfigV2) -> Mapping[str, Any]:
        """Run one allowlisted derived lifecycle action."""

    @abstractmethod
    def observe_absence(self, plan: DerivedServiceConfigV2) -> Mapping[str, Any]:
        """Observe exact unit/process/socket/cgroup/descriptor absence."""


class ManagedCredentialLifecycleV2:
    """Injected controller-first start and broker-first stop coordinator."""

    __slots__ = ("controller", "broker", "executor", "events", "_sticky",
                 "_quiesce_ack", "_started", "session", "plan_identity")

    def __init__(self, controller: DerivedServiceConfigV2,
                 broker: DerivedServiceConfigV2,
                 executor: FixedLifecycleExecutorV2,
                 session: ControllerBrokerSession) -> None:
        if (not isinstance(controller, DerivedServiceConfigV2)
                or not isinstance(broker, DerivedServiceConfigV2)
                or controller.component != "controller" or broker.component != "broker"
                or controller.machine_id != broker.machine_id
                or controller.document["peer_executable_digest"] != broker.document["executable_digest"]
                or broker.document["peer_executable_digest"] != controller.document["executable_digest"]
                or controller.document["peer_config_digest"] != broker.document["own_config_digest"]
                or broker.document["peer_config_digest"] != controller.document["own_config_digest"]
                or any(controller.document[name] != broker.document[name] for name in (
                    "policy_digest", "egress_digest", "broker_digest", "proof_digest",
                    "effective_isolation_digest", "evidence_id", "service_gid", "bounds",
                    "controller_endpoint_identity", "lease_endpoint_identity",
                    "guest_endpoint_identity"))
                or not isinstance(session, ControllerBrokerSession)
                or not session.authenticated
                or session.config.machine_id != controller.machine_id
                or any(controller.document[name] != getattr(session.config, name)
                       for name in ("policy_digest", "egress_digest", "broker_digest",
                                    "proof_digest", "effective_isolation_digest",
                                    "evidence_id"))
                or not isinstance(executor, FixedLifecycleExecutorV2)):
            raise LifecycleV2Error("lifecycle_plan_invalid")
        self.controller = controller
        self.broker = broker
        self.executor = executor
        self.session = session
        self.plan_identity = hashlib.sha256(
            controller.canonical_bytes + b"\0" + broker.canonical_bytes).hexdigest()
        self.events: list[str] = []
        self._sticky: str | None = None
        self._quiesce_ack: dict[str, Any] | None = None
        self._started: list[DerivedServiceConfigV2] = []

    def _execute(self, verb: str, plan: DerivedServiceConfigV2) -> bool:
        if verb not in LIFECYCLE_VERBS_V2:
            return False
        try:
            result = self.executor.execute(verb, plan)
        except Exception:
            result = None
        passed = (isinstance(result, Mapping) and set(result) == {"ok", "code"}
                  and result.get("ok") is True and result.get("code") == "completed")
        if passed:
            self.events.append(verb)
        return passed

    def _run(self, verb: str, plan: DerivedServiceConfigV2) -> None:
        if self._sticky is not None:
            raise LifecycleV2Error(self._sticky)
        if not self._execute(verb, plan):
            self._sticky = "lifecycle_action_failed"
            raise LifecycleV2Error(self._sticky)

    def start_closed(self) -> tuple[str, ...]:
        """Configure both, then start controller before broker; admission stays closed."""

        actions = (
            ("credential-controller-configure-v2", self.controller),
            ("credential-broker-configure-v2", self.broker),
            ("credential-controller-start-v2", self.controller),
            ("credential-broker-start-v2", self.broker),
        )
        for verb, plan in actions:
            try:
                self._run(verb, plan)
                if verb.endswith("start-v2"):
                    self._started.append(plan)
            except LifecycleV2Error:
                first = self._sticky or "lifecycle_action_failed"
                for started in reversed(self._started):
                    stop = ("credential-broker-stop-v2" if started.component == "broker"
                            else "credential-controller-stop-v2")
                    self._execute(stop, started)
                if not self.verify_cleanup()["complete"]:
                    self.events.append("cleanup_incomplete")
                self._sticky = first
                raise LifecycleV2Error(first) from None
        return tuple(self.events)

    def retain_quiesce_ack(self, acknowledgement: VerifiedQuiesceReceiptV2) -> None:
        if (self._sticky is not None
                or not isinstance(acknowledgement, VerifiedQuiesceReceiptV2)
                or acknowledgement._issuer is not _QUIESCE_RECEIPT_ISSUER
                or acknowledgement._used
                or acknowledgement.machine_id != self.controller.machine_id
                or acknowledgement.broker_epoch != self.session.broker_epoch
                or acknowledgement.controller_epoch != self.session.controller_epoch
                or acknowledgement.session_owner != self.session.owner
                or acknowledgement.plan_identity != self.plan_identity
                or dict(acknowledgement.configured_digests) != self.session.config.configured_digests()
                or acknowledgement.drain_status != "drained"
                or acknowledgement.active_operation_count != 0
                or acknowledgement.reason_code != "drained"):
            self._sticky = self._sticky or "quiesce_incomplete"
            raise LifecycleV2Error(self._sticky)
        self._quiesce_ack = acknowledgement

    def stop(self) -> tuple[str, ...]:
        """Consume the retained verified closed/drained ACK and stop in reverse."""

        if self._sticky is not None:
            raise LifecycleV2Error(self._sticky)
        if self._quiesce_ack is None:
            self._sticky = "quiesce_incomplete"
            raise LifecycleV2Error(self._sticky)
        receipt = self._quiesce_ack
        self._quiesce_ack = None
        receipt.consume()
        first = None
        for verb, plan in (("credential-broker-stop-v2", self.broker),
                           ("credential-controller-stop-v2", self.controller)):
            if not self._execute(verb, plan) and first is None:
                first = "lifecycle_action_failed"
        cleanup_complete = self.verify_cleanup()["complete"]
        if first is not None or not cleanup_complete:
            self._sticky = first or "cleanup_incomplete"
            if not cleanup_complete:
                self.events.append("cleanup_incomplete")
            raise LifecycleV2Error(self._sticky)
        return tuple(self.events)

    def verify_cleanup(self) -> dict[str, Any]:
        """Observe both components and return exact complete/incomplete/unknown detail."""

        expected = {"observed", "owned", "unit_absent", "process_absent",
                    "socket_absent", "cgroup_absent", "descriptor_absent"}
        components = {}
        first = None
        for plan in (self.broker, self.controller):
            try:
                value = self.executor.observe_absence(plan)
            except Exception:
                components[plan.component] = "unknown"
                first = first or "cleanup_unknown"
                continue
            if not isinstance(value, Mapping) or set(value) != expected:
                components[plan.component] = "unknown"
                first = first or "cleanup_unknown"
            elif any(value.get(name) is not True for name in expected):
                components[plan.component] = "incomplete"
                first = first or "cleanup_incomplete"
            else:
                components[plan.component] = "complete"
        return {"complete": first is None, "code": first or "complete",
                "components": MappingProxyType(components)}


class ControllerLifecycleAuthorityV2:
    """Exact controller-side ACTIVATE/QUIESCE sender and ACK verifier."""

    __slots__ = ("session", "_pending", "_quiesced", "_active", "plan_identity",
                 "_public_generation", "_public_receipts", "_last_quiesce_status",
                 "_public_lock", "_public_transition")

    def __init__(self, session: ControllerBrokerSession, *, plan_identity: str) -> None:
        if (not isinstance(session, ControllerBrokerSession)
                or not isinstance(plan_identity, str)
                or _DIGEST.fullmatch(plan_identity) is None):
            raise LifecycleV2Error("lifecycle_session_invalid")
        self.session = session
        self.plan_identity = plan_identity
        self._pending: dict[str, Any] | None = None
        self._quiesced = False
        self._active = False
        self._public_generation = 0
        self._public_receipts = {}
        self._last_quiesce_status = None
        self._public_lock = threading.RLock()
        self._public_transition = None

    def _rotate_public_generation(self, transition, *, preserve_revoke=False):
        with self._public_lock:
            self._public_generation += 1
            self._public_transition = transition
            if preserve_revoke:
                self._public_receipts = {
                    key: receipt for key, receipt in self._public_receipts.items()
                    if receipt.action == "revoke"
                }
            else:
                self._public_receipts.clear()

    def public_acceptance_reservations(self) -> Mapping[str, int]:
        """Return bounded non-secret reservation counts for status/tests."""

        with self._public_lock:
            requests = sum(receipt.action == "request"
                           for receipt in self._public_receipts.values())
            return MappingProxyType({
                "request_receipts": requests,
                "action_receipts": len(self._public_receipts) - requests,
                "total": len(self._public_receipts),
            })

    def close_public_acceptance(self) -> None:
        """Release all outstanding reservations during exact-owned cleanup."""

        self._rotate_public_generation("cleanup")

    def begin_public_acceptance(self, *, action: str, active_operation_count: int,
                                lifecycle_state: str, admission_open: bool):
        """Mint one current-state receipt for an exact public action attempt."""

        with self._public_lock:
            current_state = (
                "quiescing" if self._public_transition is not None
                or self._quiesced or self._pending is not None else
                "active" if self._active else "closed"
            )
            request_reservations = sum(
                receipt.action == "request"
                for receipt in self._public_receipts.values()
            )
            nonrequest_reservations = len(self._public_receipts) - request_reservations
            count_valid = type(active_operation_count) is int and (
                (action == "bind" and active_operation_count == 0
                 and not self._public_receipts)
                or (action == "request"
                    and nonrequest_reservations == 0
                    and 0 <= active_operation_count
                    and active_operation_count + request_reservations
                    < _FIXED_BOUNDS["max_active_operations"])
                or (action == "revoke"
                    and nonrequest_reservations == 0
                    and 0 <= active_operation_count
                    <= _FIXED_BOUNDS["max_active_operations"])
            )
            state_valid = (
                (action == "bind" and current_state == "closed")
                or (action == "request" and current_state == "active")
                or (action == "revoke" and current_state in {"closed", "active"})
            )
            if (action not in {"bind", "request", "revoke"} or not count_valid
                    or not state_valid or lifecycle_state != current_state
                    or admission_open != (current_state == "active")
                    or not self.session.authenticated or self.session.broker_epoch is None):
                raise LifecycleV2Error("public_acceptance_refused")
            receipt = _PublicAcceptanceLifecycleReceiptV2(
                _PUBLIC_ACCEPTANCE_RECEIPT_ISSUER, authority=self, action=action,
                generation=self._public_generation, initial_state=current_state,
                active_operation_count=active_operation_count,
            )
            self._public_receipts[id(receipt)] = receipt
            return receipt

    def finish_public_acceptance(self, receipt, *, accepted: bool) -> bool:
        """Consume the receipt and recheck lifecycle state after the action."""

        with self._public_lock:
            if (type(receipt) is not _PublicAcceptanceLifecycleReceiptV2
                    or receipt._issuer is not _PUBLIC_ACCEPTANCE_RECEIPT_ISSUER
                    or receipt.authority is not self or receipt.session is not self.session
                    or self._public_receipts.get(id(receipt)) is not receipt
                    or type(accepted) is not bool):
                raise LifecycleV2Error("public_acceptance_refused")
            del self._public_receipts[id(receipt)]
            if not accepted:
                return True
            if not self.session.authenticated or self.session.broker_epoch is None:
                return False
            if receipt.action == "bind":
                return (receipt.initial_state == "closed" and not self._active
                        and not self._quiesced and self._pending is None
                        and self._public_transition is None
                        and self._public_generation == receipt.generation)
            if receipt.action == "request":
                return (receipt.initial_state == "active" and self._active
                        and not self._quiesced and self._pending is None
                        and self._public_transition is None
                        and self._public_generation == receipt.generation)
            if receipt.initial_state == "closed":
                return (not self._active and not self._quiesced and self._pending is None
                        and self._public_transition is None
                        and self._public_generation == receipt.generation)
            return (not self._active and self._quiesced and self._pending is None
                    and self._public_transition is None
                    and self._last_quiesce_status == ("drained", 0, "drained")
                    and self._public_generation > receipt.generation)

    def activate(self, *, now_ms: int, expires_at_unix_ms: int,
                 audit_authority: ControllerAuditAuthorityV2,
                 readiness_observer) -> dict[str, Any]:
        try:
            readiness = readiness_observer()
        except Exception:
            readiness = None
        expected_readiness = {"binding_ready", "proof_ready", "egress_ready",
                              "sealed_expectations_ready", "active_operation_count",
                              "drain_status", *self.session.config.configured_digests()}
        current = self.session.config.configured_digests()
        if (not isinstance(audit_authority, ControllerAuditAuthorityV2)
                or audit_authority.session is not self.session
                or audit_authority.activation_ready is not True
                or not callable(readiness_observer)
                or not isinstance(readiness, Mapping) or set(readiness) != expected_readiness
                or any(readiness.get(name) is not True for name in (
                    "binding_ready", "proof_ready", "egress_ready",
                    "sealed_expectations_ready"))
                or readiness.get("active_operation_count") != 0
                or readiness.get("drain_status") != "drained"
                or any(readiness.get(name) != value for name, value in current.items())
                or type(now_ms) is not int or type(expires_at_unix_ms) is not int
                or not now_ms < expires_at_unix_ms <= now_ms + 30000
                or self.session.config.evidence_id is None):
            raise LifecycleV2Error("activation_refused")
        with self._public_lock:
            if (self._quiesced or self._active or self._pending is not None
                    or self._public_transition is not None):
                raise LifecycleV2Error("activation_refused")
            self._public_generation += 1
            self._public_transition = "activating"
            self._public_receipts.clear()
            self._last_quiesce_status = None
        sequence = self.session._next_outgoing
        values = {
            "protocol": PROTOCOL, "type": "ACTIVATE_V2",
            "machine_id": self.session.config.machine_id,
            "broker_epoch": self.session.broker_epoch,
            "controller_epoch": self.session.controller_epoch,
            "request_sequence": sequence,
            **self.session.config.configured_digests(),
            "activation_expires_at_unix_ms": expires_at_unix_ms,
        }
        digest = digest_document("activation_digest", values)
        try:
            sent = self.session.send_frame({
                "type": "ACTIVATE_V2", **self.session.config.configured_digests(),
                "activation_digest": digest,
                "activation_expires_at_unix_ms": expires_at_unix_ms,
            }, now_ms=now_ms)
        except ControllerServiceV2Error as exc:
            raise LifecycleV2Error(exc.code) from None
        with self._public_lock:
            self._pending = dict(sent)
        return sent

    def acknowledge_activation(self, ack: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        pending = self._pending
        if (pending is None or pending.get("type") != "ACTIVATE_V2"
                or not isinstance(ack, Mapping) or ack.get("type") != "ACTIVATE_ACK_V2"):
            raise LifecycleV2Error("activation_ack_invalid")
        try:
            self.session.require_received_frame(ack, message_type="ACTIVATE_ACK_V2")
        except ControllerServiceV2Error:
            raise LifecycleV2Error("activation_ack_invalid") from None
        success = (ack.get("admission_state"), ack.get("activate_decision"),
                   ack.get("active_operation_count"), ack.get("reason_code"))
        refused_admission = (success[0] == "closed" and success[1] == "refused"
                             and type(success[2]) is int and 0 <= success[2] <= 16
                             and success[3] == "admission_closed")
        if (ack.get("reply_to") != pending["sequence"]
                or ack.get("activation_digest") != pending["activation_digest"]
                or ack.get("activation_expires_at_unix_ms") != pending["activation_expires_at_unix_ms"]
                or (not refused_admission and success not in {
                                   ("open", "activated", 0, "activated"),
                                   ("closed", "refused", 0, "proof_unproven"),
                                   ("closed", "refused", 0, "proof_mismatch"),
                                   ("closed", "refused", 0, "digest_mismatch"),
                                   ("closed", "refused", 0, "evidence_missing"),
                                   ("closed", "refused", 0, "expired"),
                                   ("closed", "refused", 0, "quiescing"),
                                   ("closed", "refused", 0, "identity_mismatch")})
                or type(ack.get("acknowledged_at_unix_ms")) is not int
                or not now_ms - 1000 <= ack["acknowledged_at_unix_ms"] <= now_ms
                or any(ack.get(name) != pending.get(name) for name in (
                    "machine_id", "broker_epoch", "controller_epoch"))):
            raise LifecycleV2Error("activation_ack_invalid")
        with self._public_lock:
            self._pending = None
            self._active = success[0] == "open"
            self._public_generation += 1
            self._public_transition = None
        return {"ok": self._active, "code": ack["reason_code"],
                "admission_open": self._active}

    def quiesce(self, *, now_ms: int, drain_deadline_unix_ms: int,
                reason_code: str) -> dict[str, Any]:
        if (reason_code not in {"operator_stop", "restart", "revoke", "expiry",
                                       "proof_drift", "egress_drift", "identity_drift", "cleanup"}
                or type(now_ms) is not int or type(drain_deadline_unix_ms) is not int
                or not now_ms < drain_deadline_unix_ms <= now_ms + 5000):
            raise LifecycleV2Error("quiesce_refused")
        with self._public_lock:
            if (self._quiesced or not self._active or self._pending is not None
                    or self._public_transition is not None):
                raise LifecycleV2Error("quiesce_refused")
            self._public_generation += 1
            self._public_transition = "quiescing"
            self._public_receipts = {
                key: receipt for key, receipt in self._public_receipts.items()
                if receipt.action == "revoke"
            }
            self._last_quiesce_status = None
        sequence = self.session._next_outgoing
        digest = digest_document("quiesce_digest", {
            "protocol": PROTOCOL, "type": "QUIESCE_V2",
            "machine_id": self.session.config.machine_id,
            "broker_epoch": self.session.broker_epoch,
            "controller_epoch": self.session.controller_epoch,
            "request_sequence": sequence, "reason_code": reason_code,
            "drain_deadline_unix_ms": drain_deadline_unix_ms,
        })
        try:
            sent = self.session.send_frame({
                "type": "QUIESCE_V2", "reason_code": reason_code,
                "drain_deadline_unix_ms": drain_deadline_unix_ms,
                "quiesce_digest": digest,
            }, now_ms=now_ms)
        except ControllerServiceV2Error as exc:
            raise LifecycleV2Error(exc.code) from None
        with self._public_lock:
            self._pending = dict(sent)
            self._quiesced = True
        return sent

    def acknowledge_quiesce(self, ack: Mapping[str, Any], *, now_ms: int) -> VerifiedQuiesceReceiptV2:
        pending = self._pending
        if (pending is None or pending.get("type") != "QUIESCE_V2"
                or not isinstance(ack, Mapping) or ack.get("type") != "QUIESCE_ACK_V2"):
            raise LifecycleV2Error("quiesce_ack_invalid")
        try:
            self.session.require_received_frame(ack, message_type="QUIESCE_ACK_V2")
        except ControllerServiceV2Error:
            raise LifecycleV2Error("quiesce_ack_invalid") from None
        status = (ack.get("drain_status"), ack.get("active_operation_count"),
                  ack.get("reason_code"))
        valid_status = ((status == ("drained", 0, "drained"))
                        or (status[0] == "timeout" and type(status[1]) is int
                            and 1 <= status[1] <= 16 and status[2] == "drain_timeout")
                        or status == ("refused", 0, "identity_mismatch"))
        if (ack.get("reply_to") != pending["sequence"]
                or ack.get("quiesce_digest") != pending["quiesce_digest"]
                or ack.get("drain_deadline_unix_ms") != pending["drain_deadline_unix_ms"]
                or ack.get("admission_state") != "closed"
                or not valid_status
                or type(ack.get("acknowledged_at_unix_ms")) is not int
                or not now_ms - 5000 <= ack["acknowledged_at_unix_ms"] <= now_ms
                or any(ack.get(name) != pending.get(name) for name in (
                    "machine_id", "broker_epoch", "controller_epoch"))):
            raise LifecycleV2Error("quiesce_ack_invalid")
        with self._public_lock:
            self._pending = None
            self._active = False
            self._public_generation += 1
            self._last_quiesce_status = status
            self._public_transition = None
        return VerifiedQuiesceReceiptV2(
            _QUIESCE_RECEIPT_ISSUER, machine_id=ack["machine_id"],
            broker_epoch=ack["broker_epoch"], controller_epoch=ack["controller_epoch"],
            reply_to=ack["reply_to"], quiesce_digest=ack["quiesce_digest"],
            drain_deadline_unix_ms=ack["drain_deadline_unix_ms"],
            drain_status=ack["drain_status"],
            active_operation_count=ack["active_operation_count"],
            reason_code=ack["reason_code"],
            configured_digests=MappingProxyType(self.session.config.configured_digests()),
            session_owner=self.session.owner,
            plan_identity=self.plan_identity,
        )


def derived_config_document(*, machine_id: str, component: str,
                            unit_identity: str, service_uid: int, service_gid: int,
                            executable_digest: str, config_identity: str,
                            policy_digest: str, egress_digest: str,
                            broker_digest: str, proof_digest: str,
                            effective_isolation_digest: str,
                            evidence_id: str | None,
                            peer_executable_digest: str,
                            peer_config_digest: str,
                            own_config_digest: str,
                            controller_endpoint_identity: str,
                            lease_endpoint_identity: str,
                            guest_endpoint_identity: str) -> dict[str, Any]:
    return {
        "schema_version": 2, "machine_id": machine_id, "component": component,
        "unit_identity": unit_identity, "service_uid": service_uid,
        "service_gid": service_gid, "executable_digest": executable_digest,
        "config_identity": config_identity, "policy_digest": policy_digest,
        "egress_digest": egress_digest, "broker_digest": broker_digest,
        "proof_digest": proof_digest,
        "effective_isolation_digest": effective_isolation_digest,
        "evidence_id": evidence_id,
        "peer_executable_digest": peer_executable_digest,
        "peer_config_digest": peer_config_digest,
        "own_config_digest": own_config_digest,
        "controller_endpoint_identity": controller_endpoint_identity,
        "lease_endpoint_identity": lease_endpoint_identity,
        "guest_endpoint_identity": guest_endpoint_identity,
        "bounds": dict(_FIXED_BOUNDS),
    }


__all__ = [
    "ControllerLifecycleAuthorityV2", "DerivedServiceConfigV2",
    "FixedLifecycleExecutorV2", "LIFECYCLE_VERBS_V2",
    "LifecycleV2Error", "ManagedCredentialLifecycleV2", "OwnershipObservationV2",
    "VerifiedQuiesceReceiptV2", "canonical_config_bytes", "derived_config_document",
    "verify_owned_config",
]
