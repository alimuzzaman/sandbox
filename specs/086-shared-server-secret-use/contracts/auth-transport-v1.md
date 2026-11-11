# Authentication source contract v1

Source dependency `ce7663a` contains the original gate receipt API. This
contract agrees source interfaces; it does not attest installed handlers.

`sandbox.hosting.shared_credentials.authentication` exports
`AuthenticatedControlReceipt`, `AuthenticationError.code`, and
`require_authenticated(receipt) -> str`. The returned principal is the canonical
nonsecret actor partition. The receipt cannot be serialized or reconstructed
from wire, persisted data, bearer fingerprints or context frames.

`receipt.validate_current()` is for synchronous workers. Async request handlers
must await `receipt.validate_current_async()`. Every use validates the live
issuer. `ControlGate.admit_request` issues the receipt only after actual
admission, and middleware retains it in `request.state.control_receipt` before
the installation maintenance lease and attribution. Denial remains HTTP401.

Tailnet validation checks current membership enablement and the same retained
server-observed peer's login/node. WhoisCache defaults to 60 seconds, so identity
revocation observation has that cache ceiling; validation is not an uncached
Tailscale query. Gate restart invalidates its epoch and all old receipts. Token
receipts identify the configured service principal and expose no bearer.

Sharing owns `share/tailnet_actor.py` and `share/tailnet_transport.py` and may
consume only these public APIs. Its owner-delegation policy revision remains
distinct from gate `policy_epoch`. No sharing handler copies an issuer or takes
an actor from a request body.

The separate `owner_authentication.authenticate_owner_operation(admission)`
bridge accepts only the concrete `hosting.owner_job_admission.OriginalPeerAdmission`
from a live installed kernel-peer/original-job supervisor. It checks the complete
current installation and the existing admitted-owner-v1 policy. It preserves a
genuine retained initiating gate receipt; otherwise it issues truthful
`owner-peer` service provenance scoped to installation and kernel-owner UID.
Every use revalidates that same live peer, original job, and installation.
Owner-peer identity cannot assume a tailnet operator's ledger partition.
The 085 dependency is unavailable until its committed source is integrated;
missing adapters refuse `owner_peer_admission_unavailable`.
