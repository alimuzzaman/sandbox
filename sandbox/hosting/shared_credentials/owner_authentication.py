"""Authenticate the installed original-job peer without inventing a tailnet actor.

This bridge consumes the concrete sealed supervisor admission. UID fields, job
JSON, environment/context frames and arbitrary sockets cannot call the issuer.
An owner receipt is truthful service identity, distinct from a human gate actor.
It is usable only while the exact original kernel peer/job remain current.
"""
from __future__ import annotations

import hashlib
import json

from .authentication import (
    AuthenticatedControlReceipt, AuthenticationError, _ControlReceiptIssuer,
    require_authenticated,
)
from .models import InstallationBinding

_POLICY = {"revision": "admitted-owner-v1", "admitted_operator": "owner",
           "approved_sudo": True}
_POLICY_DIGEST = hashlib.sha256(json.dumps(_POLICY, sort_keys=True,
    separators=(",", ":")).encode()).hexdigest()


def authenticate_owner_operation(admission) -> AuthenticatedControlReceipt:
    """Source-v1 supervisor port. Never expose it as a wire proof parser.

    A genuine initiating ControlGate receipt is preserved if the supervisor has
    retained one in process. Otherwise owner-peer provenance uses the actual
    installation and UID, and cannot resume an operation under a tailnet actor.
    """
    failed = False
    receipt = None
    try:
        from sandbox.hosting.owner_job_admission import OriginalPeerAdmission
        from sandbox.hosting.owner_peers import AuthenticatedOwnerPeer
        from sandbox.installation.binding import validate_current
        if type(admission) is not OriginalPeerAdmission:
            raise AuthenticationError("owner_peer_admission_invalid")
        admission.require_current()
        peer = admission.peer
        if type(peer) is not AuthenticatedOwnerPeer:
            raise AuthenticationError("owner_peer_admission_invalid")
        owner = peer.owner
        binding = InstallationBinding.parse(admission.installation_binding)
        if (binding.policy_revision != _POLICY["revision"]
                or binding.policy_digest != _POLICY_DIGEST
                or binding.machine_identity != owner.machine_identity):
            raise AuthenticationError("owner_peer_policy_denied")
        validate_current(binding.as_dict(), home=owner.home)
        initiating = admission.initiating_control_receipt
        if initiating is not None:
            require_authenticated(initiating)
            return initiating
        principal = "owner:" + binding.installation_id + ":" + str(owner.uid)

        def current(candidate):
            # Concrete supervisor and installation checks, not caller-selected
            # callbacks or cached JSON evidence. Neither step creates state.
            admission.require_current()
            if (admission.peer is not peer or peer.owner != owner
                    or InstallationBinding.parse(admission.installation_binding) != binding
                    or candidate.via != "owner-peer" or candidate.service_principal != principal
                    or candidate.policy_epoch != binding.policy_digest):
                return False
            validate_current(binding.as_dict(), home=owner.home)
            return True

        issuer = _ControlReceiptIssuer(current)
        receipt = issuer._issue(via="owner-peer", policy_epoch=binding.policy_digest,
                                service_principal=principal)
        require_authenticated(receipt)
    except Exception:
        failed = True
    if failed:
        raise AuthenticationError("owner_peer_admission_unavailable") from None
    return receipt


__all__ = ["authenticate_owner_operation"]
