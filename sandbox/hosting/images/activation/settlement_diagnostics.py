"""Closed, advisory reasons for read-only settlement refusals; no host I/O."""

import re


_SETTLEMENT_DIAGNOSTIC_RULES = {
    "target_identity_changed": ("evidence_changed", "target", {"identity_before", "identity_after"}),
    "daemon_identity_changed": ("evidence_changed", "daemon", {"first", "second"}),
    "container_set_changed": ("evidence_changed", "container_set", {"first", "second", "comparison"}),
    "container_process_disappeared": ("evidence_changed", "owned_container", {"first", "second"}),
    "container_process_owner_unavailable": ("process_owner_unavailable", "owned_container", {"first", "second"}),
    "container_binding_changed": ("evidence_changed", "owned_container", {"comparison"}),
    "container_paused": ("container_paused", "owned_container", {"first", "second"}),
    "container_state_invalid": ("container_state_invalid", "owned_container", {"first", "second"}),
    "container_not_stopped": ("not_quiescent", "owned_container", {"first"}),
    "container_restart_enabled": ("not_quiescent", "owned_container", {"first"}),
    "helper_activity_present": ("not_quiescent", "helper_activity", {"first", "second"}),
    "retained_data_consumer_running": ("not_quiescent", "data_consumer", {"first"}),
}


def settlement_diagnostic(value, code):
    """Return a fresh validated mapping, or discard an unsupported diagnostic."""
    if type(value) is not dict or type(code) is not str:
        return None
    fields = {"schema_version", "reason", "subject", "sample"}
    if set(value) not in (fields, fields | {"container_id"}):
        return None
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        return None
    if any(type(value[key]) is not str for key in ("reason", "subject", "sample")):
        return None
    rule = _SETTLEMENT_DIAGNOSTIC_RULES.get(value["reason"])
    if rule is None or (code, value["subject"]) != rule[:2] or value["sample"] not in rule[2]:
        return None
    if value["subject"] == "owned_container":
        identity = value.get("container_id")
        if type(identity) is not str or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
            return None
    elif "container_id" in value:
        return None
    return dict(value)


class SettlementDiagnosticRefusal(ValueError):
    """An existing refusal code with optional, non-authoritative context."""

    def __init__(self, code, diagnostic):
        super().__init__(code)
        self.diagnostic = settlement_diagnostic(diagnostic, code)


def settlement_refusal(code, reason, subject, sample, container_id=None):
    value = {"schema_version": 1, "reason": reason, "subject": subject, "sample": sample}
    if container_id is not None:
        value["container_id"] = container_id
    return SettlementDiagnosticRefusal(code, value)
