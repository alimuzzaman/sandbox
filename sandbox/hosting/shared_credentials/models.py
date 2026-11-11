"""Closed, immutable nonsecret requests for credential-operation persistence.

These parsers establish syntax and authenticated actor provenance only. Registered
projects, targets, source/profile revisions, artifact bytes and consumer slots
must still be resolved by the service. A parsed intent grants no effects.
Wire target_ref is {target_id, copy_id, incarnation}; destination is a registered
slot name to resolve into a typed measured consumer binding before admission.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

from .authentication import AuthenticatedControlReceipt, require_authenticated

MAX_BODY_BYTES = 64 * 1024
MAX_MAPPINGS = 128
_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:@+-]{0,127}\Z")


class CredentialValidationError(ValueError):
    def __init__(self, code: str = "credential_request_invalid"):
        self.code = code
        super().__init__(code)


def _closed(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - optional:
        raise CredentialValidationError()
    return value


def _id(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or len(value.encode("utf-8")) > 128:
        raise CredentialValidationError()
    return value


def _request_id(value: object) -> str:
    # Match the canonical host writer/gate request identity. Credential intent
    # must not admit an ID the supervisor cannot bind later at its effect gate.
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", value) is None:
        raise CredentialValidationError()
    return value


def _revision(value: object) -> str:
    # Registered opaque revision, never a value-derived fingerprint.
    if (not isinstance(value, str) or not value
            or not all(c.isprintable() for c in value)
            or len(value.encode("utf-8")) > 128):
        raise CredentialValidationError()
    return value


def _hex(value: object, length: int) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{%d}" % length, value) is None:
        raise CredentialValidationError()
    return value


def _version(value: object) -> int:
    if type(value) is not int or value != 1:
        raise CredentialValidationError("credential_schema_unsupported")
    return value


def _positive(value: object) -> float:
    if type(value) not in (int, float):
        raise CredentialValidationError()
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise CredentialValidationError() from None
    if not math.isfinite(result) or result <= 0:
        raise CredentialValidationError()
    return result


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CredentialValidationError("credential_duplicate_field")
        result[key] = value
    return result


def _constant(value: str):
    raise CredentialValidationError()


def parse_json_body(body: bytes) -> dict:
    """Strict UTF-8, duplicate-free JSON; callers must bound stream reads too."""
    if type(body) is not bytes or len(body) > MAX_BODY_BYTES:
        raise CredentialValidationError("credential_body_invalid")
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise CredentialValidationError("credential_body_invalid") from None
    if type(value) is not dict:
        raise CredentialValidationError()
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True, slots=True)
class InstallationBinding:
    schema_version: int
    installation_id: str
    incarnation: str
    machine_identity: str
    ownership_marker: str
    runtime_revision: str
    policy_revision: str
    policy_digest: str
    execution_capabilities: tuple[tuple[str, int], ...]
    layout_kind: str

    @classmethod
    def parse(cls, value: object) -> InstallationBinding:
        data = _closed(value, set(cls.__dataclass_fields__))
        capabilities = data["execution_capabilities"]
        if type(capabilities) is not dict or len(capabilities) > 128:
            raise CredentialValidationError()
        caps = []
        for key, version in capabilities.items():
            if type(version) is not int or not 0 < version <= 2**31 - 1:
                raise CredentialValidationError()
            caps.append((_id(key), version))
        if data["layout_kind"] not in ("owner", "managed"):
            raise CredentialValidationError()
        return cls(_version(data["schema_version"]), _hex(data["installation_id"], 32),
                   _hex(data["incarnation"], 32), _hex(data["machine_identity"], 24),
                   _hex(data["ownership_marker"], 24), _hex(data["runtime_revision"], 24),
                   _revision(data["policy_revision"]), _hex(data["policy_digest"], 64),
                   tuple(sorted(caps)), data["layout_kind"])

    def as_dict(self) -> dict:
        return {name: dict(self.execution_capabilities) if name == "execution_capabilities"
                else getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class TargetReference:
    target_id: str
    copy_id: str
    incarnation: str

    @classmethod
    def parse(cls, value: object) -> TargetReference:
        data = _closed(value, {"target_id", "copy_id", "incarnation"})
        return cls(*(_id(data[key]) for key in ("target_id", "copy_id", "incarnation")))

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class CredentialMapping:
    kind: str
    source_key: str
    destination: str
    consumer_id: str
    consumer_revision: str
    purpose: str

    @classmethod
    def parse(cls, value: object) -> CredentialMapping:
        data = _closed(value, set(cls.__dataclass_fields__))
        if data["kind"] not in ("provider", "generated"):
            raise CredentialValidationError()
        return cls(data["kind"], _id(data["source_key"]), _id(data["destination"]),
                   _id(data["consumer_id"]), _revision(data["consumer_revision"]),
                   _id(data["purpose"]))

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class RequestScope:
    installation_id: str
    incarnation: str
    principal_id: str
    project_ref: str
    request_id: str

    @classmethod
    def parse_lookup(cls, body: bytes, receipt: AuthenticatedControlReceipt) -> RequestScope:
        data = _closed(parse_json_body(body),
                       {"schema_version", "installation_id", "incarnation", "project_ref", "request_id"})
        _version(data["schema_version"])
        return cls(_hex(data["installation_id"], 32), _hex(data["incarnation"], 32),
                   require_authenticated(receipt), _id(data["project_ref"]), _request_id(data["request_id"]))


@dataclass(frozen=True, slots=True, init=False)
class CredentialIntent:
    """Unresolved declaration. Only parse_operation supplies its actor field.

    Use request_scope() for retained lookup before resolving mutable references.
    requested_deadline is intent; effective deadline is the ledger's separately
    bounded server policy result. Neither the digest nor this record is a grant.
    """
    request_id: str
    action: str
    project_ref: str
    expected_installation: InstallationBinding
    target_ref: TargetReference
    source_commit: str
    artifact_digest: str
    manifest_digest: str
    environment: str
    source_ref: str
    source_revision: str
    profile_ref: str
    profile_revision: str
    mappings: tuple[CredentialMapping, ...]
    generate_requested: bool
    requested_deadline: float
    retry_of: str | None
    principal_id: str

    def request_scope(self) -> RequestScope:
        binding = self.expected_installation
        return RequestScope(binding.installation_id, binding.incarnation,
                            self.principal_id, self.project_ref, self.request_id)

    def effective_deadline(self, *, now: float, max_lifetime_seconds: float) -> float:
        """Cap the requested absolute Unix deadline to supplied finite job policy.

        The service must persist this result at admission and never recalculate it
        to extend a replay. This method does not read policy or authorize work.
        """
        current, lifetime = _positive(now), _positive(max_lifetime_seconds)
        maximum = current + lifetime
        if not math.isfinite(maximum):
            raise CredentialValidationError("credential_deadline_invalid")
        effective = min(self.requested_deadline, maximum)
        if effective <= current:
            raise CredentialValidationError("credential_deadline_expired")
        return effective

    def as_dict(self) -> dict:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        result["schema_version"] = 1
        result["expected_installation"] = self.expected_installation.as_dict()
        result["target_ref"] = self.target_ref.as_dict()
        result["mappings"] = [mapping.as_dict() for mapping in self.mappings]
        return result

    def canonical_json(self) -> str:
        return _canonical(self.as_dict())

    def intent_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def parse_operation(body: bytes, receipt: AuthenticatedControlReceipt) -> CredentialIntent:
    fields = set(CredentialIntent.__dataclass_fields__) - {"principal_id", "retry_of"}
    data = _closed(parse_json_body(body), fields | {"schema_version"}, {"retry_of"})
    _version(data["schema_version"])
    if data["action"] not in ("stack_up", "host_apply") or type(data["generate_requested"]) is not bool:
        raise CredentialValidationError()
    raw_mappings = data["mappings"]
    if type(raw_mappings) is not list or len(raw_mappings) > MAX_MAPPINGS:
        raise CredentialValidationError()
    mappings = tuple(CredentialMapping.parse(value) for value in raw_mappings)
    slots = [(value.consumer_id, value.destination) for value in mappings]
    if len(set(slots)) != len(slots):
        raise CredentialValidationError("credential_destination_duplicate")
    commit = data["source_commit"]
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None:
        raise CredentialValidationError()
    values = {key: _id(data[key]) for key in ("project_ref", "environment", "source_ref", "profile_ref")}
    values["request_id"] = _request_id(data["request_id"])
    values.update(action=data["action"], expected_installation=InstallationBinding.parse(data["expected_installation"]),
                  target_ref=TargetReference.parse(data["target_ref"]), source_commit=commit,
                  artifact_digest=_hex(data["artifact_digest"], 64), manifest_digest=_hex(data["manifest_digest"], 64),
                  source_revision=_revision(data["source_revision"]), profile_revision=_revision(data["profile_revision"]),
                  mappings=mappings, generate_requested=data["generate_requested"],
                  requested_deadline=_positive(data["requested_deadline"]),
                  retry_of=_request_id(data["retry_of"]) if "retry_of" in data else None,
                  principal_id=require_authenticated(receipt))
    if values["retry_of"] == values["request_id"]:
        raise CredentialValidationError("credential_retry_invalid")
    intent = object.__new__(CredentialIntent)
    for key, value in values.items():
        object.__setattr__(intent, key, value)
    return intent


__all__ = ["CredentialValidationError", "InstallationBinding", "TargetReference",
           "CredentialMapping", "RequestScope", "CredentialIntent", "parse_json_body",
           "parse_operation", "MAX_BODY_BYTES", "MAX_MAPPINGS"]
