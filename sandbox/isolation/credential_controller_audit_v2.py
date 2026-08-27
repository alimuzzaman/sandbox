"""Durable, secret-free audit authority and typed effect result for protocol v2.

Construction is inert.  The controller repository is injected and must prove a
durable append before an acknowledgement can be returned.  Wire operation IDs
are used only to verify the reviewed fingerprint and are never persisted.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import re
from typing import Any, Callable, Iterable, Mapping

from .credential_controller_protocol_v2 import (
    PROTOCOL,
    ProtocolV2Error,
    digest_document,
    validate_controller_message,
)
from .credential_controller_service_v2 import (
    ControllerBrokerSession,
    ControllerServiceV2Error,
)


_SAFE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_COMMIT = re.compile(r"^commit-[a-z0-9]{9,56}$")
_AUDIT = re.compile(r"^audit-[a-z0-9]{10,57}$")
_MACHINE = re.compile(r"^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$")
_BINDING = re.compile(r"^binding-[a-z0-9]{8,55}$")
_DECISION = re.compile(r"^decision-[a-z0-9]{7,54}$")
_FORBIDDEN = frozenset((
    "operation_id", "request_digest", "lease_id", "authorization_digest",
    "auth_form", "source_handle", "source_reference", "credential",
    "descriptor", "headers", "body", "protocol", "broker_epoch",
    "controller_epoch", "sequence", "reply_to",
))
_COMMON_RECORD_KEYS = frozenset((
    "record_version", "event_id", "machine_id", "audit_root_id", "phase",
    "phase_id", "audit_fingerprint", "commit_id", "binding_id",
    "binding_version", "decision_id", "committed_at_unix_ms",
))
_PRE_RECORD_KEYS = _COMMON_RECORD_KEYS | {"event_code"}
_POST_RECORD_KEYS = _COMMON_RECORD_KEYS | {
    "pre_phase_id", "pre_commit_id", "outcome_class", "effect_certainty",
    "reason_code", "recovery",
}
_POST_COMBINATIONS = frozenset((
    ("completed", "completed", "upstream_completed"),
    ("refused", "none", "upstream_refused"),
    ("refused", "none", "deadline_exceeded"),
    ("refused", "none", "revoked"),
    ("refused", "none", "lease_invalid"),
    ("indeterminate", "possible", "guest_disconnected"),
    ("indeterminate", "possible", "deadline_exceeded"),
    ("indeterminate", "possible", "audit_unavailable"),
    ("indeterminate", "possible", "internal_indeterminate"),
    ("indeterminate", "completed", "audit_unavailable"),
    ("indeterminate", "completed", "internal_indeterminate"),
))
_FINGERPRINT_KEYS = {
    "audit_pre_fingerprint": ("machine_id", "operation_id", "binding_id",
        "binding_version", "decision_id", "audit_root_id", "phase_id", "event_code"),
    "audit_post_fingerprint": ("machine_id", "operation_id", "binding_id",
        "binding_version", "decision_id", "audit_root_id", "phase_id",
        "pre_commit_id", "outcome_class", "effect_certainty", "reason_code"),
}


def _fingerprint(name: str, value: Mapping[str, Any]) -> str:
    return digest_document(name, {key: value[key] for key in _FINGERPRINT_KEYS[name]})


class AuditV2Error(RuntimeError):
    """Bounded, sticky-safe audit failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if isinstance(code, str) and _SAFE.fullmatch(code) else "audit_unavailable"
        super().__init__(self.code)


class DurableAuditRepositoryV2(ABC):
    """The sole controller durable-audit storage contract."""

    @abstractmethod
    def records(self, machine_id: str) -> Iterable[Mapping[str, Any]]:
        """Return canonical committed records for one machine."""

    @abstractmethod
    def append(self, record: Mapping[str, Any]) -> bool:
        """Return true only after the exact record is durably committed."""


@dataclass(frozen=True, slots=True)
class EffectResultV2:
    """The only result a credential-effect executor may return."""

    outcome_class: str
    effect_certainty: str
    reason_code: str

    def __post_init__(self) -> None:
        if (self.outcome_class, self.effect_certainty, self.reason_code) not in _POST_COMBINATIONS:
            raise AuditV2Error("effect_result_invalid")


class CredentialEffectExecutorV2(ABC):
    """One typed, injected effect boundary; no public upstream is wired here."""

    @abstractmethod
    def execute(self, request: Mapping[str, Any], descriptor: int) -> EffectResultV2:
        """Execute exactly once after durable PRE acknowledgement."""


def _record_exact(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise AuditV2Error("audit_repository_invalid")
    value = dict(record)
    if value.get("phase") not in {"pre", "post"}:
        raise AuditV2Error("audit_repository_invalid")
    expected = _PRE_RECORD_KEYS if value.get("phase") == "pre" else _POST_RECORD_KEYS
    if set(value) != expected or any(key in value for key in _FORBIDDEN):
        raise AuditV2Error("audit_repository_invalid")
    if (value.get("record_version") != 2 or value.get("event_id") != value.get("commit_id")
            or not isinstance(value.get("machine_id"), str)
            or _MACHINE.fullmatch(value["machine_id"]) is None
            or not _AUDIT.fullmatch(value.get("audit_root_id", ""))
            or not _AUDIT.fullmatch(value.get("phase_id", ""))
            or not _COMMIT.fullmatch(value.get("commit_id", ""))
            or not _BINDING.fullmatch(value.get("binding_id", ""))
            or not _DECISION.fullmatch(value.get("decision_id", ""))
            or not isinstance(value.get("audit_fingerprint"), str)
            or re.fullmatch(r"[0-9a-f]{64}", value["audit_fingerprint"]) is None
            or type(value.get("binding_version")) is not int
            or not 1 <= value["binding_version"] <= 9007199254740991
            or type(value.get("committed_at_unix_ms")) is not int
            or not 1700000000000 <= value["committed_at_unix_ms"] <= 4102444800000):
        raise AuditV2Error("audit_repository_invalid")
    if value["phase"] == "pre":
        if value.get("event_code") != "credential_effect_pre":
            raise AuditV2Error("audit_repository_invalid")
    elif ((value.get("outcome_class"), value.get("effect_certainty"),
           value.get("reason_code")) not in _POST_COMBINATIONS
          or type(value.get("recovery")) is not bool
          or not _AUDIT.fullmatch(value.get("pre_phase_id", ""))
          or not _COMMIT.fullmatch(value.get("pre_commit_id", ""))):
        raise AuditV2Error("audit_repository_invalid")
    return value


class ControllerAuditAuthorityV2:
    """Durable semantic PRE/POST authority for one controller session."""

    __slots__ = ("session", "repository", "_commit_factory", "_phase_factory",
                 "_records", "_sticky", "_loaded")

    def __init__(self, session: ControllerBrokerSession,
                 repository: DurableAuditRepositoryV2, *,
                 commit_id_factory: Callable[[], str],
                 phase_id_factory: Callable[[], str]) -> None:
        if (not isinstance(session, ControllerBrokerSession)
                or not isinstance(repository, DurableAuditRepositoryV2)
                or not callable(commit_id_factory) or not callable(phase_id_factory)):
            raise AuditV2Error("audit_authority_invalid")
        self.session = session
        self.repository = repository
        self._commit_factory = commit_id_factory
        self._phase_factory = phase_id_factory
        self._records: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._sticky: str | None = None
        self._loaded = False

    def _load(self) -> None:
        """Explicitly and boundedly load durable tombstones; construction is inert."""

        if self._loaded:
            return
        try:
            iterator = iter(self.repository.records(self.session.config.machine_id))
            for index in range(4097):
                try:
                    raw = next(iterator)
                except StopIteration:
                    break
                if index == 4096:
                    raise AuditV2Error("audit_repository_invalid")
                record = _record_exact(raw)
                if record["machine_id"] != self.session.config.machine_id:
                    raise AuditV2Error("audit_repository_invalid")
                key = (record["machine_id"], record["audit_root_id"],
                       record["phase"], record["phase_id"])
                prior = self._records.get(key)
                if prior is not None and prior != record:
                    raise AuditV2Error("audit_conflict")
                same_phase = [item for item in self._records.values()
                              if item["machine_id"] == record["machine_id"]
                              and item["audit_root_id"] == record["audit_root_id"]
                              and item["phase"] == record["phase"]]
                if same_phase and same_phase[0]["phase_id"] != record["phase_id"]:
                    raise AuditV2Error("audit_conflict")
                self._records[key] = record
            for record in self._records.values():
                if record["phase"] != "post":
                    continue
                linked = [item for item in self._records.values()
                          if item["phase"] == "pre"
                          and item["audit_root_id"] == record["audit_root_id"]
                          and item["phase_id"] == record["pre_phase_id"]
                          and item["commit_id"] == record["pre_commit_id"]
                          and item["binding_id"] == record["binding_id"]
                          and item["binding_version"] == record["binding_version"]
                          and item["decision_id"] == record["decision_id"]]
                if len(linked) != 1:
                    raise AuditV2Error("audit_repository_invalid")
            self._loaded = True
        except AuditV2Error as exc:
            self._fail(exc.code)
        except Exception:
            self._fail("audit_repository_invalid")

    def _fail(self, code: str) -> None:
        if self._sticky is None:
            self._sticky = code
            try:
                self.session.admission_open = False
                self.session.close(code)
            except Exception:
                pass
        raise AuditV2Error(self._sticky)

    def _new_commit(self) -> str:
        try:
            value = self._commit_factory()
        except Exception:
            self._fail("audit_unavailable")
        if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
            self._fail("audit_unavailable")
        return value

    def _append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        value = _record_exact(record)
        key = (value["machine_id"], value["audit_root_id"], value["phase"], value["phase_id"])
        existing = self._records.get(key)
        if existing is not None:
            semantic = {k: v for k, v in existing.items()
                        if k not in {"event_id", "commit_id", "committed_at_unix_ms"}}
            candidate = {k: v for k, v in value.items()
                         if k not in {"event_id", "commit_id", "committed_at_unix_ms"}}
            if semantic != candidate:
                self._fail("audit_conflict")
            return existing
        same_phase = [item for item in self._records.values()
                      if item["machine_id"] == value["machine_id"]
                      and item["audit_root_id"] == value["audit_root_id"]
                      and item["phase"] == value["phase"]]
        if same_phase:
            self._fail("audit_conflict")
        try:
            if self.repository.append(value) is not True:
                self._fail("audit_unavailable")
        except AuditV2Error:
            raise
        except Exception:
            self._fail("audit_unavailable")
        self._records[key] = value
        return value

    def handle(self, message: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        """Commit an accepted PRE/POST before sending its exact acknowledgement."""

        if self._sticky is not None:
            raise AuditV2Error(self._sticky)
        if not self._loaded:
            raise AuditV2Error("audit_recovery_required")
        try:
            kind = message.get("type") if isinstance(message, Mapping) else None
            if kind not in {"AUDIT_PRE_V2", "AUDIT_POST_V2"}:
                raise AuditV2Error("audit_message_invalid")
            validate_controller_message(message, direction="broker_to_controller", now_ms=now_ms)
            self.session.require_received_frame(message, message_type=kind)
            digest_name = "audit_pre_fingerprint" if kind == "AUDIT_PRE_V2" else "audit_post_fingerprint"
            if message["audit_fingerprint"] != _fingerprint(digest_name, message):
                raise AuditV2Error("audit_message_invalid")
            phase = "pre" if kind == "AUDIT_PRE_V2" else "post"
            key = (message["machine_id"], message["audit_root_id"], phase, message["phase_id"])
            existing = self._records.get(key)
            commit_id = existing["commit_id"] if existing is not None else self._new_commit()
            common = {
                "record_version": 2, "event_id": commit_id,
                "machine_id": message["machine_id"],
                "audit_root_id": message["audit_root_id"], "phase": phase,
                "phase_id": message["phase_id"],
                "audit_fingerprint": message["audit_fingerprint"],
                "commit_id": commit_id, "binding_id": message["binding_id"],
                "binding_version": message["binding_version"],
                "decision_id": message["decision_id"],
                "committed_at_unix_ms": now_ms,
            }
            if phase == "pre":
                record = {**common, "event_code": message["event_code"]}
            else:
                pre = [item for item in self._records.values()
                       if item["audit_root_id"] == message["audit_root_id"]
                       and item["phase"] == "pre"
                       and item["commit_id"] == message["pre_commit_id"]]
                if len(pre) != 1:
                    raise AuditV2Error("audit_message_invalid")
                record = {**common, "pre_phase_id": pre[0]["phase_id"],
                          "pre_commit_id": message["pre_commit_id"],
                          "outcome_class": message["outcome_class"],
                          "effect_certainty": message["effect_certainty"],
                          "reason_code": message["reason_code"], "recovery": False}
            committed = self._append(record)
            return self.session.send_frame({
                "type": "AUDIT_ACK_V2", "reply_to": message["sequence"],
                "audit_root_id": message["audit_root_id"], "phase": phase,
                "phase_id": message["phase_id"],
                "audit_fingerprint": message["audit_fingerprint"],
                "commit_id": committed["commit_id"], "disposition": "committed",
            }, now_ms=now_ms)
        except AuditV2Error as exc:
            self._fail(exc.code)
        except (ProtocolV2Error, ControllerServiceV2Error):
            self._fail("audit_message_invalid")
        except Exception:
            self._fail("audit_unavailable")

    def recover_unclosed_pre(self, *, now_ms: int) -> int:
        """Close every durable PRE without POST before activation is allowed."""

        if self._sticky is not None:
            raise AuditV2Error(self._sticky)
        if type(now_ms) is not int or not 1700000000000 <= now_ms <= 4102444800000:
            self._fail("audit_recovery_unavailable")
        try:
            self._load()
        except AuditV2Error as exc:
            self._fail(exc.code)
        roots_with_post = {item["audit_root_id"] for item in self._records.values()
                           if item["phase"] == "post"}
        open_pre = [item for item in self._records.values()
                    if item["phase"] == "pre" and item["audit_root_id"] not in roots_with_post]
        for pre in open_pre:
            try:
                phase_id = self._phase_factory()
            except Exception:
                self._fail("audit_recovery_unavailable")
            if not isinstance(phase_id, str) or _AUDIT.fullmatch(phase_id) is None:
                self._fail("audit_recovery_unavailable")
            commit_id = self._new_commit()
            fingerprint = digest_document("audit_post_fingerprint", {
                "machine_id": pre["machine_id"], "operation_id": "operation-recovery",
                "binding_id": pre["binding_id"], "binding_version": pre["binding_version"],
                "decision_id": pre["decision_id"], "audit_root_id": pre["audit_root_id"],
                "phase_id": phase_id, "pre_commit_id": pre["commit_id"],
                "outcome_class": "indeterminate", "effect_certainty": "possible",
                "reason_code": "audit_unavailable",
            })
            self._append({
                "record_version": 2, "event_id": commit_id,
                "machine_id": pre["machine_id"], "audit_root_id": pre["audit_root_id"],
                "phase": "post", "phase_id": phase_id,
                "audit_fingerprint": fingerprint, "commit_id": commit_id,
                "binding_id": pre["binding_id"], "binding_version": pre["binding_version"],
                "decision_id": pre["decision_id"], "committed_at_unix_ms": now_ms,
                "pre_phase_id": pre["phase_id"], "pre_commit_id": pre["commit_id"],
                "outcome_class": "indeterminate", "effect_certainty": "possible",
                "reason_code": "audit_unavailable", "recovery": True,
            })
        return len(open_pre)

    @property
    def activation_ready(self) -> bool:
        if self._sticky is not None or not self._loaded:
            return False
        roots_pre = {item["audit_root_id"] for item in self._records.values() if item["phase"] == "pre"}
        roots_post = {item["audit_root_id"] for item in self._records.values() if item["phase"] == "post"}
        return roots_pre <= roots_post


__all__ = [
    "AuditV2Error", "ControllerAuditAuthorityV2", "CredentialEffectExecutorV2",
    "DurableAuditRepositoryV2", "EffectResultV2",
]
