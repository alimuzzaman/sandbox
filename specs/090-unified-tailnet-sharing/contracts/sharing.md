# Unified share admission and receiver contract

Status: proposed SOURCE contract. No public URL flag, READY capability or live route
is installed by this document. Existing stack/entry commands remain the canonical
engine and compatibility baseline.

## Actor transport

A registered handler on the existing authenticated control transport receives a
closed create/list/revoke request. Actor identity is exclusively request.state's
actual ControlGate-issued receipt, not any request field. Request bodies containing
actor/receipt/owner/policy override fields refuse. The handler delegates to the
same canonical share engine; it does not expose a second authentication server,
standalone share daemon or legacy autologin issuer.

The genuine shared authentication dependency must be integrated from its owning
source revision. Missing dependency, middleware attachment, live actor revalidation,
receiver binding or registered handler returns unavailable before effects. A local
controller CLI URL request must use this real authenticated transport; direct SSH
mint or a local fabricated actor cannot replace it. Existing non-URL paths retain
compatibility until separately verified composition.

Create target: discriminated stack | recorded-entry | tailnet-url. Each accepts only
its own selector fields; conflicts refuse before effects. URL input is sensitive.
CLI argv accepts only non-sensitive URL forms; query-bearing input must use a
foreground non-echoing/pipe input path, never a secret-valued argv/file fallback.
The wrapper must not advertise URL support before this contract is implemented.

For tailnet-url, the server loads its immutable owner-delegation policy; clients
cannot select that revision or receiver pins. It authenticates the actor, resolves
and verifies the node/address/DNS target, bounds TTL/use/scope, checks current
readiness and revalidates actor/policy immediately before each external effect and
before returning success. Deferred/background jobs cannot carry a serialized actor.

## Explicit authority policy

Policy meaning: currently admitted server-owner operators may delegate the receiver's
own network reachability through a finite scoped bearer. This intentionally may
exceed the initiating node's direct target ACL. It does not grant application login,
general panel control, public/LAN/metadata access or another grant's management.

Bind every grant to the exact policy revision, genuine actor principal/gate epoch,
receiver installation/service identity and immutable target descriptor. Revalidate
through owner-supplied live mechanisms, never body fields or attribution logs.
A policy revision change does not silently migrate old grants; it invalidates their
URL authorization until explicitly reissued. Global ACL edits are not required.

## Bounded source-design limits

- Management request body <=16KiB, one operation/request, duplicate/unknown fields refuse.
- URL <=4096 UTF-8 bytes; HTTP(S) only, explicit port1..65535, safe canonical path.
  No userinfo or fragment forwarding. Optional query stays private and is never
  requested for a probe. Reject ambiguous encodings, traversal and invalid authority.
- Trusted daemon frame <=2MiB and <=4096 peers, normalized to required identity fields;
  at most4 destination addresses, all node-owned and bound to one stable peer.
- Observation/resolve/connect deadlines2s each, aggregate admission deadline10s;
  target observation age <=5s at connection authorization. Unknown age refuses.
- Revalidate actor and policy at admission, immediately before mint and at forwarding
  authorization. Respect the actual ControlGate identity cache behavior; do not claim
  instant upstream identity revocation beyond its observed freshness guarantee.
- URL TTL60s..30days, default existing7day cutoff-clamped lifetime, finite uses default1,
  maximum20. Session lifetime <= grant/cutoff and existing12h maximum.
- Revocation closes tracked new-URL connections within a5s cleanup budget; incomplete
  closure returns cleanup_pending, never successful revocation. Already transmitted
  bytes cannot be retracted; previously established application sessions persist.

These are design limits to test, not measurements. Unsupported receiver versions or
profiles remain unavailable; raising bounds requires explicit schema/policy review.

## Public origin and application behavior

Use isolated host-only grant origins served by the existing canonical gate. A trusted
registered origin profile must bind the public authority to the grant and service;
no dynamic DNS/certificate/ingress mutation occurs as a mint side effect. A receiver
without that provisioned, owner-reviewed profile cannot enable URL grants.

Preserve the approved path at the application boundary. Forward original upstream
Host/TLS name to the pinned peer addresses; never resolve again while connecting.
Strip incoming forwarding/proxy authority and gate credentials. Preserve application
login and application Authorization only inside the approved scope.

Origin-relative navigation/assets/forms are the initial explicit compatibility
profile. Same-authority redirects can map to the grant origin only within scope;
external redirects and out-of-scope resources refuse. Never remove CSP, TLS or app
login checks to make compatibility pass. Host-only cookies cannot authorize siblings;
Set-Cookie domain/path that cannot be safely mapped refuses. Supported WebSocket
handshakes use the same grant/target authorization and tracked closeable lease.
An unsupported app/profile must be reported honestly, not silently broadened.

## Common lifecycle and safe projections

Use one canonical mint envelope/derived-ID engine with versioned target support,
one list/revoke path and durable original effect descriptors. Preserve 088 companion
and uncertainty fences. Persist prepared IDs before effects; transition unknown
before installation/mint; exact confirmation and final current actor/target/policy
check precede successful publication. Unknown acceptance never implies replay-safe mint.

Public projections expose only original ID, target kind, safe label, scope policy,
expiry/use counts and status/reason code. No private URL query, connection config,
actor receipt, bearer/hash/key or provider response body is projected or logged.
A refusal may name its stable code and safe remedy, never echo rejected input.

## Capability/readiness gate

A new URL capability must attest actual registered authenticated handler, genuine
actor dependency/revalidation, receiver identity/target observer, pinned connector,
origin profile, canonical storage/revoke and tracked streaming behavior. Presence of
this contract, protocol types or a parsed URL does not register that capability.
No missing app-auth/READY/parked-release requirement is bypassed. Human auth/release
review must explicitly approve receiver-owner network delegation before enablement.
