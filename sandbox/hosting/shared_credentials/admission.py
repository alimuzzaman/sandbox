"""Server-only credential admission backed by the retained operation ledger.

ControlGate provenance and broker preflight are required independently. A plain
ledger record, client receipt or successful presence check cannot issue a grant.
The registered service supplies current installation/artifact/target resolution;
this module never substitutes owner context or initializes installation state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time
from typing import Callable

from sandbox.jobs.credential_operations import (
    CredentialOperationRepository, OperationEvidence, OperationState,
)
from .authentication import AuthenticatedControlReceipt, require_authenticated
from .models import CredentialIntent


class AdmissionError(ValueError):
    def __init__(self, code="credential_admission_invalid"):
        self.code = code
        super().__init__(code)


_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class CommittedCredentialReceipt:
    """Private in-process proof; revalidate its retained admission on every use."""
    _issuer: CredentialAdmissionService = field(repr=False)
    _intent: CredentialIntent = field(repr=False)
    _actor: AuthenticatedControlReceipt = field(repr=False)
    _operation_id: str
    _admission_version: int
    _preflight_digest: str
    _seal: object = field(repr=False)

    def __repr__(self):
        return "CommittedCredentialReceipt(<private>)"

    def __reduce__(self):
        raise TypeError("credential receipts are not serializable")


class CredentialAdmissionService:
    """Trusted composition port, never a request-body or callback dispatch API.

    ``revalidate`` belongs to the installed registered resolver. It checks the
    complete current installation, project, exact source/artifact and recipient
    binding. No HTTP caller can select it. Composition is not advertised until
    that resolver and the production execution adapter are actually installed.
    No database transaction spans resolver or broker calls.
    """

    def __init__(self, ledger: CredentialOperationRepository, broker, *,
                 revalidate: Callable[[CredentialIntent], None]):
        from .broker import RegisteredCredentialBroker
        if (type(ledger) is not CredentialOperationRepository
                or type(broker) is not RegisteredCredentialBroker or not callable(revalidate)):
            raise AdmissionError()
        self._ledger = ledger
        self._broker = broker
        self._revalidate = revalidate

    def _current(self, actor, intent):
        if (type(intent) is not CredentialIntent
                or require_authenticated(actor) != intent.principal_id):
            raise AdmissionError("credential_actor_changed")
        failed = False
        try:
            self._revalidate(intent)
        except Exception:
            failed = True
        # Raise outside the resolver's exception frame; never retain raw detail.
        if failed:
            raise AdmissionError("credential_binding_changed") from None

    def commit(self, actor: AuthenticatedControlReceipt, intent: CredentialIntent,
               preflight) -> CommittedCredentialReceipt | OperationEvidence:
        """Commit only genuine fresh broker proof to an existing reservation.

        A retained refusal/terminal outcome returns evidence for inspection, not
        a credential receipt. Replayed admitted/running requests likewise return
        their retained outcome rather than automatically reissuing execution.
        """
        if (type(intent) is not CredentialIntent
                or require_authenticated(actor) != intent.principal_id):
            raise AdmissionError("credential_actor_changed")
        row = self._ledger.replay(intent)
        if row is None:
            raise AdmissionError("credential_reservation_missing")
        if row.state is not OperationState.RESERVED:
            return row
        self._current(actor, intent)
        evidence = self._broker.require_preflight(preflight, intent=intent)
        expected_digest = hashlib.sha256(evidence.canonical_json().encode("utf-8")).hexdigest()
        if row.preflight_digest is None:
            row = self._ledger.record_preflight(intent.request_scope(), row.operation_id,
                expected_version=row.row_version, evidence=evidence)
        elif row.preflight_digest != expected_digest:
            raise AdmissionError("credential_preflight_changed")
        if row.state is not OperationState.RESERVED:
            return row
        # Source/profile and current authority can change during persistence.
        self._current(actor, intent)
        refreshed = self._broker.require_preflight(preflight, intent=intent)
        if refreshed.canonical_json() != evidence.canonical_json():
            raise AdmissionError("credential_preflight_changed")
        row = self._ledger.admit(intent.request_scope(), row.operation_id,
            expected_version=row.row_version, intent_digest=intent.intent_digest(),
            preflight_digest=expected_digest)
        if row.state is not OperationState.ADMITTED:
            return row
        return self._issue(actor, intent, row)

    def _issue(self, actor, intent, row):
        if (row.admission_version is None or row.admitted_at is None
                or row.preflight_digest is None):
            raise AdmissionError()
        receipt = object.__new__(CommittedCredentialReceipt)
        for name, value in (("_issuer", self), ("_intent", intent), ("_actor", actor),
                            ("_operation_id", row.operation_id),
                            ("_admission_version", row.admission_version),
                            ("_preflight_digest", row.preflight_digest), ("_seal", _SEAL)):
            object.__setattr__(receipt, name, value)
        # A cancel, expiry or drift racing the commit cannot return usable proof.
        self._require(receipt, intent.intent_digest())
        return receipt

    def _require(self, receipt, digest):
        self._current(receipt._actor, receipt._intent)
        intent = receipt._intent
        row = self._ledger.lookup(intent.request_scope(), operation_id=receipt._operation_id)
        if (row is None or row.intent_digest != digest
                or row.canonical_intent != intent.canonical_json()
                or row.admission_version != receipt._admission_version
                or row.preflight_digest != receipt._preflight_digest
                or row.admitted_at is None or row.cancel_requested
                or row.state not in (OperationState.ADMITTED, OperationState.RUNNING)
                or row.effective_deadline <= time.time()):
            raise AdmissionError()
        return row


def require_committed(receipt, *, intent_digest: str) -> OperationEvidence:
    if (type(receipt) is not CommittedCredentialReceipt
            or getattr(receipt, "_seal", None) is not _SEAL
            or type(getattr(receipt, "_issuer", None)) is not CredentialAdmissionService
            or receipt._intent.intent_digest() != intent_digest):
        raise AdmissionError()
    return receipt._issuer._require(receipt, intent_digest)


__all__ = ["AdmissionError", "CommittedCredentialReceipt", "CredentialAdmissionService",
           "require_committed"]
