"""Server-only broker. Source implementation is not installed capability.

Admission, effect boundary and fixed consumer manifest are separately owned.
Missing owners refuse; no dictionary, authentication receipt or ledger evidence
can substitute for their genuinely committed receipt. No value getter or wire
callback exists. Compose one broker per authenticated operation in a worker.
"""
from __future__ import annotations

import hashlib
import json

from sandbox.secrets.models import SecretBrokerError
from .authentication import require_authenticated
from .models import CredentialIntent


class BrokerError(ValueError):
    def __init__(self, code="credential_binding_unavailable"):
        self.code = code
        super().__init__(code)


class _PrivateToken:
    __slots__ = ()

    def __new__(cls):
        raise TypeError("broker proofs are privately issued")

    def __repr__(self):
        return "<private hosting broker proof>"

    def __reduce__(self):
        raise TypeError("broker proofs are not serializable")


class RegisteredCredentialBroker:
    """Construct only through trusted service composition, never from wire."""

    def __init__(self):
        raise TypeError("use trusted broker composition")

    def _profile(self, intent):
        if type(intent) is not CredentialIntent or require_authenticated(self._actor) != intent.principal_id:
            raise BrokerError("credential_actor_changed")
        profile = self._secrets.hosting_profiles.get(intent.profile_ref)
        if profile is None or not profile.matches(intent):
            raise BrokerError("credential_profile_changed")
        return profile

    def preflight(self, intent):
        """Return a private proof; public_preflight exposes only names/status.

        Admission must call require_preflight, never accept a caller-created
        PreflightEvidence. Resolution includes the entire canonical intent plus
        exact source/profile revisions. Artifact existence/current installation
        and recipient resolution remain the service's independent obligations.
        """
        profile = self._profile(intent)
        keys = tuple(sorted({item.source_key for item in profile.mappings if item.kind == "provider"}))
        try:
            snapshot = self._secrets._hosting_snapshot(profile.source, keys,
                expected_revision=intent.source_revision, profile_name=profile.name)
            names = snapshot["names"]
            registration = snapshot["registration"]
        except SecretBrokerError:
            names = tuple((key, "unavailable") for key in keys)
            registration = None
        digest = hashlib.sha256(json.dumps({"intent": intent.intent_digest(),
            "source": intent.source_ref, "source_revision": intent.source_revision,
            "profile": profile.name, "profile_revision": profile.revision,
            "source_registration": registration},
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        proof = object.__new__(_PrivateToken)
        self._proofs[proof] = (intent.canonical_json(), digest, names, registration)
        return proof

    def public_preflight(self, proof):
        record = self._proofs.get(proof)
        if record is None:
            raise BrokerError("credential_preflight_invalid")
        return {"names": [{"name": key, "status": status} for key, status in record[2]]}

    def require_preflight(self, proof, *, intent):
        """Private admission port: revalidate proof, never mint from wire names.

        Returns ledger evidence, not an execution grant. The admission service
        must additionally verify full registered resolution and commit its row.
        """
        self._profile(intent)
        record = self._proofs.get(proof)
        if record is None or record[0] != intent.canonical_json():
            raise BrokerError("credential_preflight_invalid")
        if all(status == "present" for _, status in record[2]):
            try:
                self._secrets._hosting_snapshot(intent.source_ref, tuple(key for key, _ in record[2]),
                    expected_revision=intent.source_revision, profile_name=intent.profile_ref,
                    expected_registration=record[3])
            except SecretBrokerError:
                raise BrokerError("credential_source_changed") from None
        from sandbox.jobs.credential_operations import PreflightEvidence, NameStatus
        return PreflightEvidence(intent.intent_digest(), record[1],
                                 tuple((key, NameStatus(status)) for key, status in record[2]))

    @staticmethod
    def _committed(receipt, intent):
        # This import must be supplied by the actual durable admission owner.
        # There is deliberately no substitute receipt, bool, or successful stub.
        try:
            from .admission import require_committed
        except ImportError:
            raise BrokerError() from None
        from sandbox.jobs.credential_operations import OperationEvidence, OperationState
        row = require_committed(receipt, intent_digest=intent.intent_digest())
        if (type(row) is not OperationEvidence or row.canonical_intent != intent.canonical_json()
                or row.scope != intent.request_scope() or row.admission_version is None
                or row.admitted_at is None or row.cancel_requested
                or row.state not in (OperationState.ADMITTED, OperationState.RUNNING)):
            raise BrokerError("credential_admission_invalid")
        return row

    def issue(self, committed_receipt, intent):
        self._profile(intent)
        row = self._committed(committed_receipt, intent)
        # Retained evidence must match an authentic successful proof issued by
        # THIS service/profile registry/actor instance for THIS canonical intent.
        candidates = [record for record in self._proofs.values()
                      if record[0] == intent.canonical_json()
                      and all(status == "present" for _, status in record[2])]
        from sandbox.jobs.credential_operations import PreflightEvidence, NameStatus
        for record in candidates:
            evidence = PreflightEvidence(intent.intent_digest(), record[1],
                tuple((key, NameStatus(status)) for key, status in record[2]))
            digest = hashlib.sha256(evidence.canonical_json().encode("utf-8")).hexdigest()
            if row.preflight_digest == digest:
                grant = object.__new__(_PrivateToken)
                self._grants[grant] = (committed_receipt, intent, record[1], row.operation_id, record[3])
                return grant
        raise BrokerError("credential_preflight_invalid")

    def consume(self, grant, consumer_id, effect_step):
        record = self._grants.get(grant)
        if record is None:
            raise BrokerError("credential_grant_invalid")
        receipt, intent, resolution_digest, operation_id, registration = record
        profile = self._profile(intent)
        row = self._committed(receipt, intent)
        if row.operation_id != operation_id:
            raise BrokerError("credential_admission_invalid")
        selected = tuple(item for item in profile.mappings if item.consumer_id == consumer_id)
        if not selected:
            raise BrokerError("credential_consumer_denied")
        if any(item.kind != "provider" for item in selected):
            raise BrokerError("credential_generated_authority_required")
        try:
            from .manifest import require_consumer
            from .service import credential_effect_boundary
        except ImportError:
            raise BrokerError() from None
        # Fixed trusted registry selects the real callable and validates exact
        # consumer revision, typed slot, purpose and measured artifact selector.
        consumer = require_consumer(intent, consumer_id=consumer_id)
        keys = tuple(sorted({item.source_key for item in profile.mappings if item.kind == "provider"}))

        def invoke(values):
            # Source/provider resolution can block after entering the effect
            # boundary. Refresh the registered consumer and both authorities at
            # actual delivery, rather than relying on pre-resolution checks.
            self._profile(intent)
            self._committed(receipt, intent)
            if require_consumer(intent, consumer_id=consumer_id) is not consumer:
                raise BrokerError("credential_consumer_changed")
            checkpoint.require_current()
            slots = {item.destination: values[item.source_key] for item in selected}
            try:
                return consumer.consume(slots)
            finally:
                slots.clear()

        # Service owns full installation/current actor/policy/artifact/recipient
        # resolution, cancellation/deadline, step CAS, target-wide fence, and
        # completion. It MUST hold the target transaction across yield and must
        # freeze unknown completion. No job DB transaction spans provider calls.
        with credential_effect_boundary(receipt, intent=intent, consumer_id=consumer_id,
                effect_step=effect_step, resolution_digest=resolution_digest) as checkpoint:
            self._profile(intent)
            self._committed(receipt, intent)
            result = self._secrets._hosting_snapshot(profile.source, keys,
                expected_revision=intent.source_revision, profile_name=profile.name,
                expected_registration=registration, consumer=invoke)
            checkpoint.complete(result["code"])
        return {"code": result["code"]}


def _compose_registered_broker(secret_service, authenticated_receipt):
    """Trusted orchestration only; never expose this factory through transport."""
    require_authenticated(authenticated_receipt)
    broker = object.__new__(RegisteredCredentialBroker)
    broker._secrets = secret_service
    broker._actor = authenticated_receipt
    broker._proofs = {}
    broker._grants = {}
    return broker


__all__ = ["RegisteredCredentialBroker", "BrokerError"]
