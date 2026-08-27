"""Pure, sealed-proof-gated managed credential acceptance contract."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re

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
_REASONS = frozenset({
    "ready", "credential_acceptance_unavailable", "credential_acceptance_invalid",
    "credential_owner_mismatch", "credential_preflight_failed", "managed_runtime_unproven",
    "credential_broker_status_mismatch", "credential_binding_unhealthy",
    "credential_egress_refused", "credential_acceptance_indeterminate",
})


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


def _terminal_result(raw, request, *, proof):
    action = request["action"]
    if not isinstance(raw, Mapping):
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    raw = dict(raw)
    if set(raw) != {"ok", "state", "mutated", "decision", "reason"} \
            or not isinstance(raw["ok"], bool) or not isinstance(raw["mutated"], bool):
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    reason = raw.get("reason")
    if not isinstance(reason, Mapping) or set(reason) != {"code"} or reason["code"] not in _REASONS:
        return _refusal(action, "credential_acceptance_indeterminate", proof=proof)
    coherent = {"bind": (True, "bound", True, "accepted"),
                "request": (True, "completed", True, "accepted"),
                "revoke": (True, "revoked", True, "accepted")}[action]
    observed = (raw["ok"], raw["state"], raw["mutated"], raw["decision"])
    if observed not in {coherent, (False, "refused", False, "refused"),
                        (False, "indeterminate", False, "indeterminate")}:
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
    return _refusal(action, code if code in _REASONS else "credential_acceptance_indeterminate")


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
                and code in _REASONS and isinstance(value.get("proof_candidate"), bool)
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
