"""Internal held credential-effect boundary, not an installed dispatch factory.

Production composition is unavailable until the real registered resolver,
supervisor and consumers are assembled and reviewed. A controller transaction
alone never substitutes for canonical host ownership and the drained live gate.
No module import obtains authority, reads secrets, or constructs a supervisor.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import threading

from sandbox.hosting.recovery.repository import RecoveryRepository
from sandbox.jobs.credential_operations import EffectOutcome, OperationState
from .admission import require_committed
from .models import CredentialIntent


class CredentialExecutionError(ValueError):
    def __init__(self, code="credential_effect_authority_unavailable"):
        self.code = code
        super().__init__(code)


# Process-private scoped association, never read from request fields or disk.
_HELD = {}
_HELD_LOCK = threading.Lock()


def _require_host_writer(proof, expected):
    # No fallback to context, a gate token, callbacks or an ordinary job exists.
    # This port is source-proposed, not imported/assembled by an installed
    # factory yet. Import happens only when genuine supervisor composition calls.
    failed = False
    try:
        from sandbox.hosting.baselines.held_writer import require_held_writer
        require_held_writer(proof, expected=expected)
    except Exception:
        failed = True
    if failed:
        raise CredentialExecutionError() from None


def _host_binding(intent, row, canonical_target_key):
    failed = False
    binding = None
    try:
        from sandbox.hosting.baselines.held_writer import HeldWriterBinding
        installation = intent.expected_installation
        binding = HeldWriterBinding(
            installation_id=installation.installation_id,
            installation_incarnation=installation.incarnation,
            runtime_revision=installation.runtime_revision,
            policy_digest=installation.policy_digest,
            canonical_target_key=canonical_target_key,
            target_incarnation=intent.target_ref.incarnation,
            request_id=intent.request_id, operation_id=row.operation_id,
            intent_digest=intent.intent_digest())
    except Exception:
        failed = True
    if failed:
        raise CredentialExecutionError() from None
    return binding


@contextmanager
def held_credential_execution(receipt, *, intent: CredentialIntent, held_writer,
                              canonical_target_key: str,
                              target_repository: RecoveryRepository):
    """Trusted supervisor adapter, inside its actual ``supervisor.hold``.

    canonical_target_key is independently resolved registered server data. The
    supervisor's private proof must match it; a client declaration cannot supply
    ownership. Both opaque proofs are validated, never reconstructed here.
    Neither this context nor its exit completes/reopens the broader lifecycle.
    """
    if type(intent) is not CredentialIntent or type(target_repository) is not RecoveryRepository:
        raise CredentialExecutionError()
    row = require_committed(receipt, intent_digest=intent.intent_digest())
    if row.canonical_intent != intent.canonical_json():
        raise CredentialExecutionError()
    expected = _host_binding(intent, row, canonical_target_key)
    _require_host_writer(held_writer, expected)
    owner = (os.getpid(), threading.get_ident())
    association = (intent, held_writer, expected, target_repository, owner)
    with _HELD_LOCK:
        if receipt in _HELD:
            raise CredentialExecutionError("credential_effect_scope_busy")
        _HELD[receipt] = association
    try:
        yield
        require_committed(receipt, intent_digest=intent.intent_digest())
        _require_host_writer(held_writer, expected)
    finally:
        with _HELD_LOCK:
            if _HELD.get(receipt) is association:
                del _HELD[receipt]


class _Checkpoint:
    def __init__(self, ledger, scope, claim, held_writer, expected, receipt, intent):
        self._ledger, self._scope, self._claim = ledger, scope, claim
        self._held_writer, self._expected = held_writer, expected
        self._receipt, self._intent = receipt, intent
        self._owner = (os.getpid(), threading.get_ident())
        self._done = False

    def require_current(self):
        """Recheck both authorities at delivery, after any blocking resolution."""
        if self._done or self._owner != (os.getpid(), threading.get_ident()):
            raise CredentialExecutionError()
        with _HELD_LOCK:
            held = _HELD.get(self._receipt)
        if (held is None or held[1] is not self._held_writer
                or held[2] != self._expected or held[4] != self._owner):
            raise CredentialExecutionError()
        row = require_committed(self._receipt, intent_digest=self._intent.intent_digest())
        if (row.operation_id != self._claim.operation_id or row.scope != self._scope
                or row.canonical_intent != self._intent.canonical_json()):
            raise CredentialExecutionError()
        _require_host_writer(self._held_writer, self._expected)

    def complete(self, code):
        if self._done or code not in ("succeeded", "failed", "effects_unknown"):
            raise CredentialExecutionError("credential_effect_completion_invalid")
        # A successful consumer return is not proof that credential authority
        # remained current while it ran. Failure here leaves the step unknown.
        self.require_current()
        outcome = {"succeeded": EffectOutcome.SUCCEEDED,
                   "failed": EffectOutcome.FAILED,
                   "effects_unknown": EffectOutcome.UNKNOWN}[code]
        self._ledger.complete_effect(self._scope, self._claim, outcome=outcome)
        self._done = True

    def _unknown(self):
        # Completion may have committed before the response was lost. The
        # ledger accepts the same outcome, rejects a conflicting rewrite, and
        # never returns another claim token for replay. Do not dispatch again.
        try:
            self._ledger.complete_effect(self._scope, self._claim, outcome=EffectOutcome.UNKNOWN)
        except Exception:
            pass


@contextmanager
def credential_effect_boundary(receipt, *, intent: CredentialIntent,
                               consumer_id: str, effect_step: str,
                               resolution_digest: str):
    """Broker-only use under a genuine same-thread supervisor hold.

    Lock order: live supervisor writer, target mutation transaction, short job
    CAS. No job database transaction is held over the consumer invocation.
    Exceptions/missing completion retain unknown effect evidence, never retry.
    """
    row = require_committed(receipt, intent_digest=intent.intent_digest())
    with _HELD_LOCK:
        held = _HELD.get(receipt)
    if held is None:
        raise CredentialExecutionError()
    bound_intent, writer, expected, repository, owner = held
    if (owner != (os.getpid(), threading.get_ident())
            or bound_intent.canonical_json() != intent.canonical_json()
            or not any(item.consumer_id == consumer_id for item in intent.mappings)):
        raise CredentialExecutionError()
    # Compare only retained broker evidence, never caller-reported presence.
    if row.preflight_json is None:
        raise CredentialExecutionError()
    try:
        matches = json.loads(row.preflight_json)["resolution_digest"] == resolution_digest
    except (ValueError, KeyError, TypeError):
        matches = False
    if not matches:
        raise CredentialExecutionError("credential_resolution_changed")
    _require_host_writer(writer, expected)
    with repository.target_mutation_port("shared_credentials").target_mutation_transaction(
            expected.canonical_target_key):
        # Distinct same-controller operations also serialize here, while the
        # genuine canonical host/gate ownership remains required throughout.
        _require_host_writer(writer, expected)
        row = require_committed(receipt, intent_digest=intent.intent_digest())
        ledger = receipt._issuer._ledger
        if row.state is OperationState.ADMITTED:
            row = ledger.start(row.scope, row.operation_id, expected_version=row.row_version)
        row = require_committed(receipt, intent_digest=intent.intent_digest())
        claim = ledger.claim_effect(row.scope, row.operation_id,
            expected_version=row.row_version, effect_step=effect_step)
        if claim is None:
            raise CredentialExecutionError("credential_deadline_expired")
        checkpoint = _Checkpoint(ledger, row.scope, claim, writer, expected, receipt, intent)
        failed = False
        try:
            # Recheck after irreversible step admission, immediately before
            # the broker resolves/delivers the value to its trusted consumer.
            checkpoint.require_current()
            yield checkpoint
            if not checkpoint._done:
                failed = True
        except BaseException:
            failed = True
        finally:
            if failed:
                checkpoint._unknown()
        if failed:
            raise CredentialExecutionError("credential_effects_unknown") from None


__all__ = ["CredentialExecutionError", "held_credential_execution",
           "credential_effect_boundary"]
