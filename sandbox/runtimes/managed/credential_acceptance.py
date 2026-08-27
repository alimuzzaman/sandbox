"""Pure, sealed-proof-gated managed credential acceptance contract."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from types import MappingProxyType
from typing import Any, Callable

from sandbox.isolation.credential_binding import (
    CredentialBinding, canonical_auth_profile, canonical_binding_id,
    canonical_instance_id, canonical_owner, canonical_registered_source_reference,
)
from sandbox.isolation.models import canonical_digest

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_FIELDS = {
    "bind": frozenset({"action", "binding_id", "version", "machine_id", "owner",
                       "source_reference", "scheme", "host", "port", "method", "path",
                       "auth_profile", "expires_at", "policy_digest", "egress_digest",
                       "broker_digest"}),
    "request": frozenset({"action", "binding_id", "version", "machine_id", "owner",
                          "content_type", "deadline_seconds", "correlation_id"}),
    "revoke": frozenset({"action", "binding_id", "version", "machine_id", "owner"}),
}
_SUCCESS_REASONS = frozenset({"ready"})
_REFUSAL_REASONS = frozenset({
    "credential_acceptance_unavailable", "credential_acceptance_invalid",
    "credential_owner_mismatch", "credential_preflight_failed", "managed_runtime_unproven",
    "credential_broker_status_mismatch", "credential_binding_unhealthy",
    "credential_egress_refused", "credential_acceptance_indeterminate",
    "credential_protocol_unsupported", "credential_session_stale",
    "credential_binding_stale", "credential_binding_mismatch",
    "credential_lifecycle_refused", "credential_revoke_refused",
})
CONTROLLER_PROTOCOL_V2 = "credential-broker-controller-v2"
_ACCEPTANCE_ISSUER = object()
_INTERFACES_ISSUER = object()


@dataclass(frozen=True, slots=True, repr=False)
class ControllerAcceptanceInterfacesV2:
    """Controller-owned, non-secret application authorities for public intent.

    The public command cannot construct this object directly.  The authenticated
    controller composition factory binds it to one exact v2 operation/lifecycle
    session.  Every callable receives only the canonical public request and
    exact secret-free authority projections; credential resolution and wire I/O
    remain behind the controller.
    """

    _issuer: object
    _session: Any
    _operation_authority: Any
    _lifecycle_authority: Any
    status_authority: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    binding_authority: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    egress_authority: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    bind_authority: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    request_authority: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    revoke_authority: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]

    def __post_init__(self):
        if (self._issuer is not _INTERFACES_ISSUER
                or getattr(self._operation_authority, "session", None) is not self._session
                or getattr(self._lifecycle_authority, "session", None) is not self._session
                or any(
                not callable(getattr(self, name)) for name in (
                    "status_authority", "binding_authority", "egress_authority",
                    "bind_authority", "request_authority", "revoke_authority"))):
            raise ValueError("credential acceptance authorities are invalid")


def controller_acceptance_interfaces_v2(*, operation_authority, lifecycle_authority,
                                        status_authority, binding_authority,
                                        egress_authority, bind_authority,
                                        request_authority, revoke_authority):
    """Construct the controller-process authority bundle, never a CLI value."""

    from sandbox.isolation.credential_controller_authority_v2 import (
        ControllerOperationAuthorityV2,
    )
    from sandbox.isolation.credential_controller_lifecycle_v2 import (
        ControllerLifecycleAuthorityV2,
    )
    if (type(operation_authority) is not ControllerOperationAuthorityV2
            or type(lifecycle_authority) is not ControllerLifecycleAuthorityV2
            or lifecycle_authority.session is not operation_authority.session):
        raise ValueError("credential acceptance authorities are invalid")

    return ControllerAcceptanceInterfacesV2(
        _INTERFACES_ISSUER, operation_authority.session, operation_authority,
        lifecycle_authority, status_authority, binding_authority, egress_authority,
        bind_authority, request_authority, revoke_authority,
    )


class CredentialAcceptanceControllerV2:
    """Thin public projector over the sole injected controller authority.

    This class contains no binding, proof, egress, source, resolver, or audit
    decision.  Those decisions stay behind ``controller_action``.  The public
    surface can pass only the exact parsed opaque/non-secret request and can
    return only the existing bounded projector envelope.
    """

    protocol = CONTROLLER_PROTOCOL_V2

    __slots__ = ("_controller_authority", "_lifecycle_authority", "_interfaces")

    def __init__(self, issuer, controller_authority, lifecycle_authority, interfaces):
        if (issuer is not _ACCEPTANCE_ISSUER
                or type(interfaces) is not ControllerAcceptanceInterfacesV2):
            raise ValueError("credential v2 controller authority is required")
        self._controller_authority = controller_authority
        self._lifecycle_authority = lifecycle_authority
        self._interfaces = interfaces

    def invoke_v2(self, raw, *, proof_candidate_authority=None):
        try:
            request = parse_credential_acceptance(raw)
        except ValueError:
            return _refusal("request", "credential_acceptance_invalid")
        from sandbox.runtimes.managed.adapter import _is_proof_candidate_authority
        proof = _is_proof_candidate_authority(proof_candidate_authority)
        if not proof:
            return _refusal(request["action"], "managed_runtime_unproven")
        return self._invoke_authorities(request)

    def _invoke_authorities(self, request):
        action = request["action"]
        authority = self._controller_authority
        lifecycle = self._lifecycle_authority
        session = getattr(authority, "session", None)
        if self.protocol != CONTROLLER_PROTOCOL_V2:
            return _refusal(action, "credential_protocol_unsupported", proof=True)
        if (session is None
                or getattr(lifecycle, "session", None) is not session
                or getattr(session, "authenticated", False) is not True
                or getattr(session, "broker_epoch", None) is None):
            return _refusal(action, "credential_session_stale", proof=True)
        canonical = MappingProxyType(dict(request))
        try:
            raw_status = self._interfaces.status_authority(canonical)
        except Exception:
            return _refusal(action, "credential_acceptance_unavailable", proof=True)
        if (isinstance(raw_status, Mapping)
                and raw_status.get("protocol") != CONTROLLER_PROTOCOL_V2):
            return _refusal(action, "credential_protocol_unsupported", proof=True)
        status = _controller_status(raw_status, authority)
        if status is None:
            return _refusal(action, "credential_broker_status_mismatch", proof=True)
        if ((action == "request" and (status["admission_open"] is not True
                                      or status["lifecycle_state"] != "active"))
                or (action == "bind" and (status["admission_open"] is not False
                                           or status["lifecycle_state"] != "closed"))
                or (action == "revoke" and status["lifecycle_state"] == "indeterminate")):
            return _refusal(action, "credential_lifecycle_refused", proof=True)
        try:
            raw_binding = self._interfaces.binding_authority(canonical, status)
        except Exception:
            return _refusal(action, "credential_acceptance_unavailable", proof=True)
        binding = _binding_projection(raw_binding, request, authority)
        if binding is None:
            return _refusal(action, "credential_binding_mismatch", proof=True)
        if binding["binding_state"] == "stale":
            return _refusal(action, "credential_binding_stale", proof=True)
        allowed_binding_states = {
            "bind": {"prospective", "credential_pending", "revoked", "expired", "blocked"},
            "request": {"ready"},
            "revoke": {"credential_pending", "ready", "revoking", "revoked",
                       "expired", "blocked"},
        }
        if binding["binding_state"] not in allowed_binding_states[action]:
            return _refusal(action, "credential_binding_unhealthy", proof=True)
        if action != "revoke":
            try:
                raw_egress = self._interfaces.egress_authority(canonical, binding)
            except Exception:
                return _refusal(action, "credential_acceptance_unavailable", proof=True)
            if not _egress_projection(raw_egress, binding, authority):
                return _refusal(action, "credential_egress_refused", proof=True)
        handler = {"bind": self._interfaces.bind_authority,
                   "request": self._interfaces.request_authority,
                   "revoke": self._interfaces.revoke_authority}[action]
        try:
            lifecycle_receipt = lifecycle.begin_public_acceptance(
                action=action,
                active_operation_count=status["active_operation_count"],
                lifecycle_state=status["lifecycle_state"],
                admission_open=status["admission_open"],
            )
        except Exception:
            return _refusal(action, "credential_lifecycle_refused", proof=True)
        try:
            raw_result = handler(canonical, binding)
        except Exception:
            # An action exception may follow an effect.  Never report a safe
            # refusal or invite a new request identity in this state.
            try:
                lifecycle.finish_public_acceptance(lifecycle_receipt, accepted=False)
            except Exception:
                pass
            return _refusal(action, "credential_acceptance_indeterminate", proof=True)
        projected = _terminal_result(raw_result, request, proof=True)
        accepted = projected.get("ok") is True
        try:
            current = lifecycle.finish_public_acceptance(
                lifecycle_receipt, accepted=accepted,
            )
        except Exception:
            current = False
        if accepted and current is not True:
            return _refusal(action, "credential_acceptance_indeterminate", proof=True)
        return projected


def build_credential_acceptance_controller_v2(receipt, controller_authority,
                                              lifecycle_authority, interfaces):
    from sandbox.isolation.credential_controller_authority_v2 import (
        ControllerOperationAuthorityV2,
    )
    from sandbox.isolation.credential_controller_service_v2 import (
        AuthenticatedCompositionReceiptV2,
    )
    from sandbox.isolation.credential_controller_lifecycle_v2 import (
        ControllerLifecycleAuthorityV2,
    )
    if (type(receipt) is not AuthenticatedCompositionReceiptV2
            or type(controller_authority) is not ControllerOperationAuthorityV2
            or type(lifecycle_authority) is not ControllerLifecycleAuthorityV2
            or type(interfaces) is not ControllerAcceptanceInterfacesV2
            or lifecycle_authority.session is not controller_authority.session
            or interfaces._session is not controller_authority.session
            or interfaces._operation_authority is not controller_authority
            or interfaces._lifecycle_authority is not lifecycle_authority):
        raise ValueError("credential v2 controller receipt is required")
    try:
        receipt.consume_for_controller(
            controller_authority, purpose="public_acceptance",
        )
    except Exception:
        raise ValueError("credential v2 controller receipt is invalid") from None
    return CredentialAcceptanceControllerV2(
        _ACCEPTANCE_ISSUER, controller_authority, lifecycle_authority, interfaces,
    )


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("credential acceptance version is invalid")
    return value


def parse_credential_acceptance(value, *, now=None):
    if not isinstance(value, Mapping):
        raise ValueError("credential acceptance request is invalid")
    value = dict(value); action = value.get("action")
    if action not in _FIELDS or set(value) != _FIELDS[action]:
        raise ValueError("credential acceptance fields are invalid")
    binding_id = canonical_binding_id(value["binding_id"])
    machine_id = canonical_instance_id(value["machine_id"])
    owner = canonical_owner(value["owner"])
    version = _version(value["version"])
    common = {"action": action, "binding_id": binding_id, "version": version,
              "machine_id": machine_id, "owner": owner}
    if action == "bind":
        profile = canonical_auth_profile(value["auth_profile"])
        binding = CredentialBinding(
            binding_id=binding_id, instance_id=machine_id,
            source_reference=canonical_registered_source_reference(value["source_reference"]),
            policy_digest=value["policy_digest"], egress_digest=value["egress_digest"],
            broker_digest=value["broker_digest"], scheme=value["scheme"], host=value["host"],
            port=value["port"], method=value["method"], path=value["path"],
            auth_form=profile, expires_at=value["expires_at"], owner=owner,
            version=version, state="credential_pending",
        )
        if binding.is_expired(now=now or datetime.now(timezone.utc)):
            raise ValueError("credential acceptance expiry is not future")
        return {**common, "source_reference": binding.source_reference,
                "scheme": binding.scheme, "host": binding.host, "port": binding.port,
                "method": binding.method, "path": binding.path,
                "auth_profile": binding.auth_profile, "expires_at": binding.expires_at,
                "policy_digest": binding.policy_digest, "egress_digest": binding.egress_digest,
                "broker_digest": binding.broker_digest}
    if action == "request":
        if value["content_type"] not in {"application/json", "application/x-www-form-urlencoded"}:
            raise ValueError("credential acceptance content type is invalid")
        deadline = value["deadline_seconds"]
        if isinstance(deadline, bool) or not isinstance(deadline, int) or not 1 <= deadline <= 30:
            raise ValueError("credential acceptance deadline is invalid")
        correlation = canonical_binding_id(value["correlation_id"])
        return {**common, "content_type": value["content_type"],
                "deadline_seconds": deadline, "correlation_id": correlation}
    return common


def _refusal(action, code, *, proof=False):
    if code not in _REFUSAL_REASONS:
        code = "credential_acceptance_indeterminate"
    return {"ok": False, "action": action, "state": "blocked", "mutated": False,
            "decision": "refused", "reason": {"code": code},
            "proof_candidate": proof, "adoptable": False}


def _trusted_context(value, request):
    fields = {"binding_id", "version", "machine_id", "owner",
              "policy_digest", "egress_digest", "broker_digest",
              "executable_digest", "config_digest"}
    if not isinstance(value, Mapping) or set(value) != fields:
        return None
    value = dict(value)
    if any(value[name] != request[name] for name in
           ("binding_id", "version", "machine_id", "owner")):
        return None
    if any(not isinstance(value[name], str) or not _DIGEST.fullmatch(value[name])
           for name in ("policy_digest", "egress_digest", "broker_digest",
                        "executable_digest", "config_digest")):
        return None
    if request["action"] == "bind" and any(request[name] != value[name] for name in
                                             ("policy_digest", "egress_digest", "broker_digest")):
        return None
    return value


def _controller_status(value, authority):
    """Validate one current secret-free status from the authenticated session."""

    session = authority.session
    configured = authority.config.configured_digests()
    fields = {
        "protocol", "machine_id", "broker_epoch", "controller_epoch",
        "admission_open", "active_operation_count", "lifecycle_state",
        *configured,
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        return None
    value = dict(value)
    if (configured.get("evidence_id") is None
            or value["protocol"] != CONTROLLER_PROTOCOL_V2
            or value["machine_id"] != authority.config.machine_id
            or value["broker_epoch"] != session.broker_epoch
            or value["controller_epoch"] != session.controller_epoch
            or type(value["admission_open"]) is not bool
            or type(value["active_operation_count"]) is not int
            or not 0 <= value["active_operation_count"] <= 16
            or value["lifecycle_state"] not in {
                "closed", "active", "quiescing", "indeterminate"}
            or value["admission_open"] != (value["lifecycle_state"] == "active")
            or any(value.get(name) != expected
                   for name, expected in configured.items())):
        return None
    return MappingProxyType(value)


def _binding_projection(value, request, authority):
    """Require exact binding/CAS identity without accepting a source handle."""

    fields = {
        "binding_id", "version", "machine_id", "owner", "binding_state",
        "scheme", "host", "port", "method", "path", "auth_profile",
        "policy_digest", "egress_digest", "broker_digest",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        return None
    value = dict(value)
    try:
        canonical = {
            "binding_id": canonical_binding_id(value["binding_id"]),
            "machine_id": canonical_instance_id(value["machine_id"]),
            "owner": canonical_owner(value["owner"]),
            "auth_profile": canonical_auth_profile(value["auth_profile"]),
        }
        scope = CredentialBinding(
            binding_id=canonical["binding_id"], instance_id=canonical["machine_id"],
            source_reference="registered/OPAQUE", policy_digest=value["policy_digest"],
            egress_digest=value["egress_digest"], broker_digest=value["broker_digest"],
            scheme=value["scheme"], host=value["host"], port=value["port"],
            method=value["method"], path=value["path"],
            auth_form=canonical["auth_profile"], expires_at="2999-01-01T00:00:00Z",
            owner=canonical["owner"], version=value["version"],
            state="credential_pending",
        )
    except Exception:
        return None
    if (value["binding_id"] != request["binding_id"]
            or value["version"] != request["version"]
            or value["machine_id"] != request["machine_id"]
            or value["owner"] != request["owner"]
            or value["machine_id"] != authority.config.machine_id
            or any(value.get(name) != authority.config.configured_digests()[name]
                   for name in ("policy_digest", "egress_digest", "broker_digest"))
            or (request["action"] == "bind" and any(
                value.get(name) != request[name] for name in (
                    "scheme", "host", "port", "method", "path", "auth_profile",
                    "policy_digest", "egress_digest", "broker_digest")))):
        return None
    result = dict(value)
    result.update({"scheme": scope.scheme, "host": scope.host, "port": scope.port,
                   "method": scope.method, "path": scope.path,
                   "auth_profile": scope.auth_profile})
    return MappingProxyType(result)


def _egress_projection(value, binding, authority):
    fields = {"allowed", "scheme", "host", "port", "method", "path",
              "egress_digest", "broker_digest"}
    if not isinstance(value, Mapping) or set(value) != fields:
        return False
    value = dict(value)
    configured = authority.config.configured_digests()
    if (value.get("allowed") is not True
            or value.get("scheme") != "https" or value.get("port") != 443
            or value.get("egress_digest") != configured["egress_digest"]
            or value.get("broker_digest") != configured["broker_digest"]):
        return False
    if any(value.get(name) != binding[name]
           for name in ("scheme", "host", "port", "method", "path",
                        "egress_digest", "broker_digest")):
        return False
    try:
        CredentialBinding(
            binding_id=binding["binding_id"], instance_id=binding["machine_id"],
            source_reference="registered/OPAQUE", policy_digest=configured["policy_digest"],
            egress_digest=value["egress_digest"], broker_digest=value["broker_digest"],
            scheme=value["scheme"], host=value["host"], port=value["port"],
            method=value["method"], path=value["path"],
            auth_form=binding["auth_profile"],
            expires_at="2999-01-01T00:00:00Z", owner=binding["owner"],
            version=binding["version"], state="credential_pending",
        )
    except Exception:
        return False
    return True


def _terminal_result(raw, request, *, proof):
    action = request["action"]
    if not isinstance(raw, Mapping):
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    raw = dict(raw)
    if set(raw) != {"ok", "state", "mutated", "decision", "reason"} \
            or not isinstance(raw["ok"], bool) or not isinstance(raw["mutated"], bool):
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    reason = raw.get("reason")
    if not isinstance(reason, Mapping) or set(reason) != {"code"}:
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    coherent = {"bind": (True, "bound", True, "accepted"),
                "request": (True, "completed", True, "accepted"),
                "revoke": (True, "revoked", True, "accepted")}[action]
    observed = (raw["ok"], raw["state"], raw["mutated"], raw["decision"])
    if observed not in {coherent, (False, "refused", False, "refused"),
                        (False, "indeterminate", False, "indeterminate")}:
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    if observed == coherent and reason["code"] not in _SUCCESS_REASONS:
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    if observed == (False, "refused", False, "refused"):
        if reason["code"] not in _REFUSAL_REASONS:
            return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
        return _refusal(action, reason["code"], proof=proof)
    if observed == (False, "indeterminate", False, "indeterminate"):
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    result = {**raw, "action": action, "binding_id": request["binding_id"],
              "version": request["version"], "machine_id": request["machine_id"],
              "owner": request["owner"], "proof_candidate": proof, "adoptable": False}
    if action == "bind":
        result["scope"] = {key: request[key] for key in
                           ("scheme", "host", "port", "method", "path", "auth_profile")}
    elif action == "request":
        result["correlation_id"] = request["correlation_id"]
        result["request_digest"] = canonical_digest({key: request[key] for key in
                                                      ("binding_id", "version", "machine_id", "owner",
                                                       "content_type", "deadline_seconds", "correlation_id")})
    return result


def public_credential_acceptance_result(value):
    action = value.get("action") if isinstance(value, Mapping) and value.get("action") in _FIELDS else "request"
    code = (value.get("reason", {}).get("code") if isinstance(value, Mapping)
            and isinstance(value.get("reason"), Mapping) else None)
    return _refusal(action, code if code in _REFUSAL_REASONS
                    else "credential_acceptance_indeterminate")


def validate_credential_acceptance_service_result(value, raw_request):
    """Enforce the exact public envelope at the adapter trust boundary."""
    try:
        request = parse_credential_acceptance(raw_request)
    except ValueError:
        return _refusal("request", "credential_acceptance_invalid")
    action = request["action"]
    if not isinstance(value, Mapping):
        return _refusal(action, "credential_acceptance_indeterminate", proof=True)
    value = dict(value)
    refusal_keys = {"ok", "action", "state", "mutated", "decision", "reason",
                    "proof_candidate", "adoptable"}
    if set(value) == refusal_keys:
        code = value.get("reason", {}).get("code") if isinstance(value.get("reason"), Mapping) else None
        if (value.get("ok") is False and value.get("action") == action
                and value.get("state") == "blocked" and value.get("mutated") is False
                and value.get("decision") == "refused" and set(value["reason"]) == {"code"}
                and code in _REFUSAL_REASONS
                and isinstance(value.get("proof_candidate"), bool)
                and value.get("adoptable") is False):
            return value
        return _refusal(action, "credential_acceptance_indeterminate", proof=True)
    common = {"ok", "action", "state", "mutated", "decision", "reason",
              "binding_id", "version", "machine_id", "owner", "proof_candidate", "adoptable"}
    expected_keys = (common | {"scope"} if action == "bind" else
                     common | {"correlation_id", "request_digest"} if action == "request"
                     else common)
    expected_state = {"bind": "bound", "request": "completed", "revoke": "revoked"}[action]
    if (set(value) != expected_keys or value.get("ok") is not True
            or value.get("state") != expected_state or value.get("mutated") is not True
            or value.get("decision") != "accepted" or value.get("reason") != {"code": "ready"}
            or value.get("proof_candidate") is not True or value.get("adoptable") is not False
            or any(value.get(name) != request[name] for name in
                   ("binding_id", "version", "machine_id", "owner"))):
        return _refusal(action, "credential_acceptance_indeterminate", proof=True)
    if action == "bind":
        expected_scope = {key: request[key] for key in
                          ("scheme", "host", "port", "method", "path", "auth_profile")}
        if value.get("scope") != expected_scope:
            return _refusal(action, "credential_acceptance_indeterminate", proof=True)
    elif action == "request":
        expected_digest = canonical_digest({key: request[key] for key in
                                            ("binding_id", "version", "machine_id", "owner",
                                             "content_type", "deadline_seconds", "correlation_id")})
        if (value.get("correlation_id") != request["correlation_id"]
                or value.get("request_digest") != expected_digest):
            return _refusal(action, "credential_acceptance_indeterminate", proof=True)
    return value


class CredentialAcceptanceOperation:
    def __init__(self, *, owner_lookup, preflight, broker_status,
                 binding_health, egress_check, actions):
        self.owner_lookup = owner_lookup; self.preflight = preflight
        self.broker_status = broker_status
        self.binding_health = binding_health; self.egress_check = egress_check
        self.actions = actions

    def invoke(self, raw, *, proof_candidate_authority=None):
        try:
            request = parse_credential_acceptance(raw)
        except ValueError:
            return _refusal("request", "credential_acceptance_invalid")
        action = request["action"]
        proof = False
        try:
            identity = (_trusted_context(self.owner_lookup(request), request)
                        if callable(self.owner_lookup) else None)
            if identity is None:
                return _refusal(action, "credential_owner_mismatch")
            if not callable(self.preflight) or self.preflight(request, identity) is not True:
                return _refusal(action, "credential_preflight_failed")
            from sandbox.runtimes.managed.adapter import _is_proof_candidate_authority
            proof = _is_proof_candidate_authority(proof_candidate_authority)
            if not proof:
                return _refusal(action, "managed_runtime_unproven")
            status = self.broker_status(request, identity) if callable(self.broker_status) else None
            expected = {key: identity[key] for key in
                        ("machine_id", "policy_digest", "egress_digest", "broker_digest",
                         "executable_digest", "config_digest")}
            expected.update({"ok": True, "state": "credential_pending",
                             "admission_open": False})
            if not isinstance(status, Mapping) or any(status.get(key) != value
                                                      for key, value in expected.items()):
                return _refusal(action, "credential_broker_status_mismatch", proof=True)
            if not callable(self.binding_health) or self.binding_health(request, identity) is not True:
                return _refusal(action, "credential_binding_unhealthy", proof=True)
            if not callable(self.egress_check) or self.egress_check(request, identity) is not True:
                return _refusal(action, "credential_egress_refused", proof=True)
            handler = self.actions.get(action) if isinstance(self.actions, Mapping) else None
            if not callable(handler):
                return _refusal(action, "credential_acceptance_unavailable", proof=True)
            return _terminal_result(handler(request, identity), request, proof=True)
        except Exception:
            return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
