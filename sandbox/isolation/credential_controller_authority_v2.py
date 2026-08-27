"""Injected, operation-bound controller authority for Credential Broker v2.

This inert module owns no repository, source, proof, egress, descriptor, socket,
audit, application, or runtime implementation.  Each authority is supplied as
one narrow callable.  Import and construction perform no I/O.

The controller/application Python processes are trusted. Same-process
reflection, monkeypatching, or low-level object mutation is process compromise
and outside this model. Untrusted guests can reach only the authenticated,
data-only cross-process protocol and cannot inject these callables or objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from abc import ABC, abstractmethod
import re
from typing import Any, Callable, Mapping

from sandbox.isolation.credential_controller_protocol_v2 import (
    PROTOCOL,
    ProtocolV2Error,
    authorization_digest,
    decode_lease_ack,
    encode_lease_frame,
)
from sandbox.isolation.credential_controller_service_v2 import (
    ControllerBrokerSession,
    ControllerServiceConfig,
    ControllerServiceV2Error,
)


_SAFE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_AUTH_FORMS = frozenset(("authorization_bearer", "x_api_key"))
_BINDING_KEYS = frozenset((
    "binding_id", "binding_version", "auth_form",
    "binding_expires_at_unix_ms", "source_handle",
))
_PROOF_KEYS = frozenset((
    "policy_digest", "proof_digest", "effective_isolation_digest", "evidence_id",
))
_EGRESS_KEYS = frozenset(("egress_digest", "broker_digest"))
_ACTIVATION_KEYS = frozenset(("admission_open", "activation_expires_at_unix_ms"))
_MEMFD_KEYS = frozenset(("descriptor", "descriptor_size", "anonymous_memfd",
                         "close_on_exec", "seals"))
_REQUIRED_SEALS = frozenset(("write", "grow", "shrink", "seal"))
_ACK_KEYS = frozenset((
    "protocol", "type", "machine_id", "broker_epoch", "controller_epoch",
    "sequence", "reply_to", "operation_id", "request_digest", "binding_id",
    "binding_version", "decision_id", "authorization_digest",
    "authorization_expires_at_unix_ms",
))
_CLAIM_KEYS = frozenset((
    "protocol", "type", "machine_id", "broker_epoch", "controller_epoch",
    "sequence", "reply_to", "claim_state", "operation_id", "request_digest",
    "binding_id", "binding_version", "scheme", "host", "port", "method",
    "path", "content_type", "header_bytes", "body_bytes",
    "request_deadline_unix_ms", "correlation_id",
))
_NO_PENDING_KEYS = frozenset((
    "protocol", "type", "machine_id", "broker_epoch", "controller_epoch",
    "sequence", "reply_to", "claim_state", "retry_after_ms",
))
_REFUSE_REASONS = frozenset((
    "admission_closed", "request_invalid", "binding_missing", "binding_mismatch",
    "binding_stale", "binding_expired", "source_unavailable", "proof_unproven",
    "proof_mismatch", "egress_denied", "authorization_expired", "lease_invalid",
    "revoked", "deadline_exceeded", "capacity_exceeded", "audit_unavailable",
    "internal_refusal",
))


class ControllerAuthorityV2Error(RuntimeError):
    """Sticky-safe, bounded authority refusal."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if isinstance(code, str) and _SAFE.fullmatch(code) else "internal_refusal"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ControllerAuthorityInterfaces:
    binding_authority: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    source_authority: Callable[[Mapping[str, Any]], Any]
    scope_authority: Callable[[Mapping[str, Any], Mapping[str, Any]], bool]
    proof_authority: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    egress_authority: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    activation_authority: Callable[[], Mapping[str, Any]]
    expiry_authority: Callable[[Mapping[str, Any], int], bool]
    resolver: Callable[[Any], bytearray]

    def __post_init__(self) -> None:
        if any(not callable(getattr(self, field)) for field in self.__dataclass_fields__):
            raise ControllerAuthorityV2Error("authority_interfaces_invalid")


def _exact(value: Any, keys: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ControllerAuthorityV2Error(code)
    return dict(value)


class ControllerOperationAuthorityV2:
    """One authenticated session's bounded decision and lease dispatcher."""

    __slots__ = ("session", "config", "interfaces", "_decision_factory",
                 "_lease_factory", "_pending", "_tombstones", "_closed",
                 "_terminal_code", "_claim_poll")

    def __init__(self, session: ControllerBrokerSession,
                 interfaces: ControllerAuthorityInterfaces, *,
                 decision_id_factory: Callable[[], str],
                 lease_id_factory: Callable[[], str]) -> None:
        if (not isinstance(session, ControllerBrokerSession)
                or not isinstance(interfaces, ControllerAuthorityInterfaces)
                or not callable(decision_id_factory) or not callable(lease_id_factory)):
            raise ControllerAuthorityV2Error("controller_authority_invalid")
        self.session = session
        self.config: ControllerServiceConfig = session.config
        self.interfaces = interfaces
        self._decision_factory = decision_id_factory
        self._lease_factory = lease_id_factory
        self._pending: dict[str, dict[str, Any]] = {}
        self._tombstones: set[str] = set()
        self._closed = False
        self._terminal_code = "controller_authority_closed"
        self._claim_poll = None

    def poll_claim(self, *, now_ms: int, wait_deadline_unix_ms: int) -> dict[str, Any]:
        """Send and retain the sole claim request this authority may evaluate."""

        if (self._closed or not self.session.authenticated or self._claim_poll is not None
                or type(now_ms) is not int or type(wait_deadline_unix_ms) is not int
                or not now_ms < wait_deadline_unix_ms <= now_ms + 1000):
            raise ControllerAuthorityV2Error("admission_closed")
        try:
            sent = self.session.send_frame({
                "type": "CLAIM_NEXT_V2",
                "wait_deadline_unix_ms": wait_deadline_unix_ms,
            }, now_ms=now_ms)
        except ControllerServiceV2Error as exc:
            raise ControllerAuthorityV2Error(exc.code) from None
        self._claim_poll = {
            "protocol": PROTOCOL,
            "machine_id": self.config.machine_id,
            "broker_epoch": self.session.broker_epoch,
            "controller_epoch": self.session.controller_epoch,
            "owner": self.session,
            "sequence": sent["sequence"],
            "wait_deadline_unix_ms": wait_deadline_unix_ms,
        }
        return sent

    def _consume_claim(self, claim: Mapping[str, Any], *, now_ms: int) -> bool:
        """Consume one exact session-accepted reply to the outstanding poll."""

        poll = self._claim_poll
        if poll is None or not isinstance(claim, Mapping):
            raise ControllerAuthorityV2Error("request_invalid")
        try:
            self.session.require_received_frame(claim, message_type="CLAIMED_V2")
        except ControllerServiceV2Error:
            raise ControllerAuthorityV2Error("request_invalid") from None
        self._claim_poll = None
        expected_identity = {
            "protocol": poll["protocol"],
            "machine_id": poll["machine_id"],
            "broker_epoch": poll["broker_epoch"],
            "controller_epoch": poll["controller_epoch"],
            "reply_to": poll["sequence"],
        }
        if (poll["owner"] is not self.session or type(now_ms) is not int
                or now_ms > poll["wait_deadline_unix_ms"]
                or any(claim.get(key) != value for key, value in expected_identity.items())
                or claim.get("type") != "CLAIMED_V2"):
            raise ControllerAuthorityV2Error("request_invalid")
        if claim.get("claim_state") == "no_pending":
            if set(claim) != _NO_PENDING_KEYS:
                raise ControllerAuthorityV2Error("request_invalid")
            return False
        if claim.get("claim_state") != "claimed" or set(claim) != _CLAIM_KEYS:
            raise ControllerAuthorityV2Error("request_invalid")
        return True

    def _refuse(self, claim: Mapping[str, Any], *, now_ms: int,
                reason_code: str, decision_id: str) -> dict[str, Any]:
        frame = {
            "type": "REFUSE_V2", "operation_id": claim["operation_id"],
            "request_digest": claim["request_digest"],
            "binding_id": claim["binding_id"],
            "binding_version": claim["binding_version"],
            "decision_id": decision_id, "reason_code": reason_code,
        }
        sent = self.session.send_frame(frame, now_ms=now_ms)
        self._tombstones.add(claim["operation_id"])
        return sent

    def decide(self, claim: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        """Evaluate one claimed projection; never resolve credential bytes."""

        if (self._closed or not self.session.authenticated
                or len(self._pending) + len(self._tombstones) >= 16):
            raise ControllerAuthorityV2Error("admission_closed")
        if not self._consume_claim(claim, now_ms=now_ms):
            return {"ok": True, "code": "no_pending",
                    "retry_after_ms": claim["retry_after_ms"]}
        operation_id = claim.get("operation_id")
        if operation_id in self._pending or operation_id in self._tombstones:
            raise ControllerAuthorityV2Error("request_invalid")
        decision_id = None
        try:
            decision_id = self._decision_factory()
            binding = _exact(self.interfaces.binding_authority(claim), _BINDING_KEYS,
                             "binding_missing")
            if (binding["binding_id"] != claim["binding_id"]
                    or binding["binding_version"] != claim["binding_version"]):
                return self._refuse(claim, now_ms=now_ms,
                                    reason_code="binding_mismatch", decision_id=decision_id)
            if binding["auth_form"] not in _AUTH_FORMS:
                return self._refuse(claim, now_ms=now_ms,
                                    reason_code="binding_mismatch", decision_id=decision_id)
            source_handle = self.interfaces.source_authority(binding)
            if source_handle is None:
                return self._refuse(claim, now_ms=now_ms,
                                    reason_code="source_unavailable", decision_id=decision_id)
            if self.interfaces.scope_authority(binding, claim) is not True:
                return self._refuse(claim, now_ms=now_ms,
                                    reason_code="binding_mismatch", decision_id=decision_id)
            proof = _exact(self.interfaces.proof_authority(binding, claim), _PROOF_KEYS,
                           "proof_unproven")
            egress = _exact(self.interfaces.egress_authority(binding, claim), _EGRESS_KEYS,
                            "egress_denied")
            activation = _exact(self.interfaces.activation_authority(), _ACTIVATION_KEYS,
                                "admission_closed")
            if activation["admission_open"] is not True or self.config.evidence_id is None:
                return self._refuse(claim, now_ms=now_ms,
                                    reason_code="admission_closed", decision_id=decision_id)
            expected = {**proof, **egress}
            configured = self.config.configured_digests()
            if any(expected.get(name) != configured.get(name) for name in expected):
                return self._refuse(claim, now_ms=now_ms,
                                    reason_code="proof_mismatch", decision_id=decision_id)
            authorization_expires = min(
                now_ms + 5000, binding["binding_expires_at_unix_ms"],
                activation["activation_expires_at_unix_ms"],
                claim["request_deadline_unix_ms"],
            )
            expiry_values = {
                "binding_expires_at_unix_ms": binding["binding_expires_at_unix_ms"],
                "authorization_expires_at_unix_ms": authorization_expires,
                "activation_expires_at_unix_ms": activation["activation_expires_at_unix_ms"],
                "request_deadline_unix_ms": claim["request_deadline_unix_ms"],
            }
            if (authorization_expires <= now_ms
                    or self.interfaces.expiry_authority(expiry_values, now_ms) is not True):
                return self._refuse(claim, now_ms=now_ms,
                                    reason_code="authorization_expired", decision_id=decision_id)
            digest_values = {
                "protocol": PROTOCOL, "machine_id": self.config.machine_id,
                "broker_epoch": self.session.broker_epoch,
                "controller_epoch": self.session.controller_epoch,
                "operation_id": claim["operation_id"],
                "request_digest": claim["request_digest"],
                "binding_id": binding["binding_id"],
                "binding_version": binding["binding_version"],
                "auth_form": binding["auth_form"], **proof, **egress,
                "binding_expires_at_unix_ms": binding["binding_expires_at_unix_ms"],
                "authorization_expires_at_unix_ms": authorization_expires,
                "decision_id": decision_id,
            }
            digest = authorization_digest(digest_values)
            outbound = self.session.send_frame({
                "type": "AUTHORIZE_V2", **{key: digest_values[key] for key in (
                    "operation_id", "request_digest", "binding_id", "binding_version",
                    "auth_form", "policy_digest", "egress_digest", "broker_digest",
                    "proof_digest", "effective_isolation_digest", "evidence_id",
                    "binding_expires_at_unix_ms", "authorization_expires_at_unix_ms",
                    "decision_id",
                )}, "authorization_digest": digest,
            }, now_ms=now_ms, temporal_context={
                "activation_expires_at_unix_ms": activation["activation_expires_at_unix_ms"],
                "request_deadline_unix_ms": claim["request_deadline_unix_ms"],
            })
            self._pending[operation_id] = {
                "claim": dict(claim), "binding": binding, "source_handle": source_handle,
                "proof": proof, "egress": egress, "activation": activation,
                "authorization": outbound,
            }
            return outbound
        except ControllerAuthorityV2Error as exc:
            if decision_id is not None:
                return self._refuse(
                    claim, now_ms=now_ms,
                    reason_code=exc.code if exc.code in _REFUSE_REASONS else "internal_refusal",
                    decision_id=decision_id,
                )
            raise
        except (ControllerServiceV2Error, ProtocolV2Error) as exc:
            raise ControllerAuthorityV2Error(getattr(exc, "code", "internal_refusal")) from None
        except Exception:
            if decision_id is not None:
                return self._refuse(claim, now_ms=now_ms,
                                    reason_code="internal_refusal", decision_id=decision_id)
            raise ControllerAuthorityV2Error("internal_refusal") from None

    def acknowledge_and_dispatch(self, ack: Mapping[str, Any], *, now_ms: int,
                                 lease_sequence: int, memfd_factory: Callable[[bytearray], Mapping[str, Any]],
                                 dispatcher: Any,
                                 descriptor_closer: Callable[[int], Any]) -> dict[str, Any]:
        """Resolve only after the exact AUTHORIZED acknowledgement, then send once."""

        if (not callable(memfd_factory) or not callable(descriptor_closer)
                or not self.session.owns_lease_socket(dispatcher)
                or dispatcher._session is not self.session
                or dispatcher.machine_id != self.config.machine_id
                or dispatcher.broker_epoch != self.session.broker_epoch
                or dispatcher.controller_epoch != self.session.controller_epoch
                or dispatcher.owner != self.session.owner):
            raise ControllerAuthorityV2Error("lease_dispatch_invalid")
        if (not isinstance(ack, Mapping) or set(ack) != _ACK_KEYS
                or ack.get("protocol") != PROTOCOL
                or ack.get("type") != "AUTHORIZED_V2"):
            raise ControllerAuthorityV2Error("authorization_ack_invalid")
        pending = self._pending.get(ack.get("operation_id"))
        if pending is None:
            raise ControllerAuthorityV2Error("authorization_ack_invalid")
        authorization = pending["authorization"]
        exact = {
            "operation_id", "request_digest", "binding_id", "binding_version",
            "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
        }
        if (any(ack.get(key) != authorization.get(key) for key in exact)
                or ack.get("reply_to") != authorization.get("sequence")
                or ack.get("machine_id") != self.config.machine_id
                or ack.get("broker_epoch") != self.session.broker_epoch
                or ack.get("controller_epoch") != self.session.controller_epoch
                or now_ms >= authorization["authorization_expires_at_unix_ms"]):
            raise ControllerAuthorityV2Error("authorization_ack_invalid")
        try:
            self.session.require_received_frame(ack, message_type="AUTHORIZED_V2")
        except ControllerServiceV2Error:
            raise ControllerAuthorityV2Error("authorization_ack_invalid") from None
        try:
            self.session.bind_lease_socket(
                dispatcher, operation_id=authorization["operation_id"],
                authorization_digest=authorization["authorization_digest"],
                authorization_expires_at_unix_ms=(
                    authorization["authorization_expires_at_unix_ms"]))
        except ControllerServiceV2Error:
            raise ControllerAuthorityV2Error("lease_dispatch_invalid") from None
        self._pending.pop(ack["operation_id"])
        self._tombstones.add(ack["operation_id"])
        material = None
        descriptor = None
        result = None
        failure_code = None
        try:
            try:
                material = self.interfaces.resolver(pending["source_handle"])
            except Exception:
                raise ControllerAuthorityV2Error("source_unavailable") from None
            if type(material) is not bytearray or not 1 <= len(material) <= 16384:
                raise ControllerAuthorityV2Error("source_unavailable")
            try:
                raw_memfd = memfd_factory(material)
            except Exception:
                raise ControllerAuthorityV2Error("lease_dispatch_invalid") from None
            if (isinstance(raw_memfd, Mapping)
                    and type(raw_memfd.get("descriptor")) is int
                    and raw_memfd["descriptor"] >= 0):
                descriptor = raw_memfd["descriptor"]
            memfd = _exact(raw_memfd, _MEMFD_KEYS, "lease_dispatch_invalid")
            try:
                seals = frozenset(memfd["seals"])
            except Exception:
                raise ControllerAuthorityV2Error("lease_dispatch_invalid") from None
            if (type(memfd["descriptor"]) is not int or memfd["descriptor"] < 0
                    or memfd["descriptor_size"] != len(material)
                    or memfd["anonymous_memfd"] is not True
                    or memfd["close_on_exec"] is not True or seals != _REQUIRED_SEALS):
                raise ControllerAuthorityV2Error("lease_dispatch_invalid")
            descriptor = memfd["descriptor"]
            lease_expiry = min(now_ms + 5000,
                               authorization["authorization_expires_at_unix_ms"],
                               pending["binding"]["binding_expires_at_unix_ms"],
                               pending["activation"]["activation_expires_at_unix_ms"],
                               pending["claim"]["request_deadline_unix_ms"])
            lease = {
                "machine_id": self.config.machine_id,
                "broker_epoch": self.session.broker_epoch,
                "controller_epoch": self.session.controller_epoch,
                "operation_id": authorization["operation_id"],
                "request_digest": authorization["request_digest"],
                "binding_id": authorization["binding_id"],
                "binding_version": authorization["binding_version"],
                "auth_form": authorization["auth_form"],
                "policy_digest": authorization["policy_digest"],
                "egress_digest": authorization["egress_digest"],
                "broker_digest": authorization["broker_digest"],
                "proof_digest": authorization["proof_digest"],
                "effective_isolation_digest": authorization["effective_isolation_digest"],
                "evidence_id": authorization["evidence_id"],
                "decision_id": authorization["decision_id"],
                "authorization_digest": authorization["authorization_digest"],
                "authorization_expires_at_unix_ms": authorization["authorization_expires_at_unix_ms"],
                "lease_id": None, "lease_sequence": lease_sequence,
                "lease_expires_at_unix_ms": lease_expiry,
                "descriptor_size": len(material),
            }
            try:
                lease["lease_id"] = self._lease_factory()
            except Exception:
                raise ControllerAuthorityV2Error("lease_dispatch_invalid") from None
            try:
                packet = encode_lease_frame(lease, now_ms=now_ms, deadline_caps={
                "authorization_expires_at_unix_ms": authorization["authorization_expires_at_unix_ms"],
                "binding_expires_at_unix_ms": pending["binding"]["binding_expires_at_unix_ms"],
                "activation_expires_at_unix_ms": pending["activation"]["activation_expires_at_unix_ms"],
                "request_deadline_unix_ms": pending["claim"]["request_deadline_unix_ms"],
                })
            except Exception:
                raise ControllerAuthorityV2Error("lease_dispatch_invalid") from None
            try:
                dispatched = dispatcher.exchange(
                    packet, descriptor, min(1000, lease_expiry - now_ms))
            except ControllerServiceV2Error as exc:
                raise ControllerAuthorityV2Error(
                    "lease_ack_invalid" if exc.code in {
                        "lease_ack_invalid", "lease_ack_provenance_invalid"}
                    else "lease_dispatch_invalid") from None
            except Exception:
                raise ControllerAuthorityV2Error("lease_dispatch_invalid") from None
            if isinstance(dispatched, bytes):
                try:
                    lease_ack = decode_lease_ack(dispatched)
                except ProtocolV2Error:
                    raise ControllerAuthorityV2Error("lease_ack_invalid") from None
                expected = {name: lease[name] for name in (
                    "machine_id", "broker_epoch", "controller_epoch", "lease_id",
                    "lease_sequence", "authorization_digest",
                )}
                if any(lease_ack.get(name) != value for name, value in expected.items()):
                    raise ControllerAuthorityV2Error("lease_ack_invalid")
                result = {"ok": lease_ack["outcome_class"] == "completed",
                          "code": lease_ack["reason_code"],
                          "lease_id": lease["lease_id"],
                          "audit_root_id": lease_ack["audit_root_id"],
                          "post_phase_id": lease_ack["post_phase_id"],
                          "post_commit_id": lease_ack["post_commit_id"],
                          "outcome_class": lease_ack["outcome_class"],
                          "effect_certainty": lease_ack["effect_certainty"]}
            else:
                raise ControllerAuthorityV2Error("lease_ack_invalid")
        except ControllerAuthorityV2Error as exc:
            failure_code = exc.code
        except Exception:
            failure_code = "lease_dispatch_invalid"
        finally:
            try:
                dispatcher.close()
            except Exception:
                if failure_code is None:
                    failure_code = "lease_dispatch_invalid"
            if material is not None:
                material[:] = bytes(len(material))
            if descriptor is not None:
                try:
                    descriptor_closer(descriptor)
                except Exception:
                    self._terminal_code = "descriptor_cleanup_failed"
                    self._closed = True
        if self._terminal_code != "controller_authority_closed":
            raise ControllerAuthorityV2Error(self._terminal_code)
        if failure_code is not None:
            raise ControllerAuthorityV2Error(failure_code)
        if result is None:
            raise ControllerAuthorityV2Error("lease_dispatch_invalid")
        return result

    def close(self) -> dict[str, Any]:
        self._closed = True
        self._pending.clear()
        return {"ok": self._terminal_code == "controller_authority_closed",
                "code": self._terminal_code}


__all__ = [
    "ControllerAuthorityInterfaces", "ControllerAuthorityV2Error",
    "ControllerOperationAuthorityV2",
]
