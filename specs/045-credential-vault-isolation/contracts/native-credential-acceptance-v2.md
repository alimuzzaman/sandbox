# Native credential acceptance v2 (local contract)

Status: locally implemented, unproven. This contract is not live evidence and
does not make managed native credential mediation adoptable.

The only public operation is `native credential-acceptance`. It routes through
the runtime operation boundary. It never reads the binding repository, proof
store, egress policy, controller socket, lifecycle state, or credential source.
The v1 and unknown protocol paths stay closed.

## Input and output

The public tagged `bind`, `request`, and `revoke` objects retain the exact
secret-free fields documented by v1. A source reference is an opaque registered
`source/key` identity. No credential, header, body, environment value, path to
a source, descriptor, lease ID, operation ID, session object, authority object,
or arbitrary protocol field is accepted.

The public projector returns only the fixed decision, state, reason, exact
binding identity, canonical scope or correlation/request digest,
`proof_candidate`, and `adoptable=false`. It never projects source references,
controller epochs, authority output, audit identities, diagnostics, or helper
output. A partial result is `credential_acceptance_indeterminate`, never
success.

## Authenticated controller composition

The sealed adapter accepts only `CredentialAcceptanceControllerV2`. Its factory
consumes one opaque `public_acceptance` receipt minted by the authenticated T040
controller session and requires the exact T041 operation authority plus the
exact T042 lifecycle authority from that same session. A forged, replayed,
cross-session, stale, v1, or unknown composition is refused.

The controller-process interface supplies six narrow authorities: current
status, binding/CAS projection, egress projection, and bind/request/revoke
actions. The public layer passes only the canonical request and exact immutable
non-secret projections. It validates the current machine, both epochs, all
sealed configured digests, lifecycle/admission state, active-operation count,
binding/version/owner, canonical HTTPS scope, and egress/broker digests before
an action can run. Revoke deliberately does not depend on a healthy egress
grant; it remains an admission-closing operation.

The interface bundle is pinned to the exact authenticated session, operation
authority, and lifecycle authority before the public composition receipt is
consumed. Immediately before an action, that lifecycle authority mints one
opaque one-attempt receipt bound to the action, current lifecycle generation,
session, admission state, and observed active-operation count. Bind requires a
closed lifecycle and zero active operations. Request permits the reviewed
capacity of zero through fifteen while admission is active. Revoke permits zero
through sixteen, but public success requires the exact lifecycle authority to
finish QUIESCE, close admission, and clear its pending transition. A successful
action projection is checked against the receipt after the action; activation,
quiesce, disconnect, replay, or cross-authority composition makes the outcome
indeterminate rather than successful.

Request admission is reserved inside the lifecycle authority under one lock.
The authority adds the observed active count to its own outstanding live
request receipts, refuses a total above sixteen, and stores no more than sixteen
request reservations even when seventeen callers concurrently present the same
zero-count status snapshot. Bind/revoke reservations serialize mutation intent;
revoke may still be admitted to close sixteen outstanding requests. Every
reservation is removed once by terminal success/refusal/indeterminate handling,
generation rotation, quiesce, or exact-owned cleanup. A second release is a
bounded refusal, never a capacity credit.

Every egress scope field (`scheme`, `host`, `port`, `method`, and `path`) and
both egress/broker digests must exactly equal the controller-owned binding
projection. The terminal projector uses disjoint reason allowlists: only
`ready` can accompany an accepted result, and `ready` can never accompany a
refusal.

Status, binding, or egress uncertainty fails before an action with a bounded
refusal. Once an action is entered, an exception or malformed/partial terminal
result is indeterminate because an effect may have occurred. The CLI does not
retry or mint a new correlation identity. Binding CAS, controller operation
tombstones, revoke state, and the v2 audit/effect state machine remain owned by
their controller authorities.

Default unsealed composition returns `managed_runtime_unproven`. A sealed
adapter with no exact authenticated service returns
`credential_acceptance_unavailable`. Offline injected tests prove only codec,
projection, routing, and fail-closed composition behavior. They do not prove
Linux kernel transport, installed lifecycle, credential use, or Ubuntu 24.04
acceptance.
