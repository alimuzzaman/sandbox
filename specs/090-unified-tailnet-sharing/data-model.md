# Data model: Unified tailnet sharing

Status: source design, no installed capability or migration.

## Canonical target union

- StackTarget: registered remote and stack slug, preserving existing 088 direct companions.
- RecordedTarget: registered remote/project/environment plus declared entry and safe path.
- VerifiedTailnetTarget: registered receiver identity, trusted observed node stable ID,
  canonical original HTTP(S) authority, explicit port, selected node-owned address set,
  safe path and private optional query, plus owner-delegation policy revision.

Only the final variant is new. Its selector does not encode or infer an initiating
person's direct target ACL. Public/LAN/loopback/link-local/metadata addresses and
subnet-route destinations without an actual tailnet-node address match refuse.
All normalized values are immutable after preparation; changing destination requires
new authorization. Sensitive URI content is private, repr-disabled and absent from
public inventory/audit/error projections.

## Authentic actor binding

The input to URL admission is the genuine AuthenticatedControlReceipt supplied by
ControlGate.admit_request and attached by trusted server middleware. It is passed in
process, never parsed from HTTP JSON/headers, serialized, pickled or reconstructed
from login/node strings. require_authenticated validates it in a synchronous worker.
An async handler uses the existing async validation/worker bridge; it never forges a
receipt or blocks an event loop with a synchronous check.

Retained nonsecret binding: principal_id, ControlGate policy_epoch, explicit
owner-network policy revision, receiver installation/service identity and operation
ID. Gate epoch and delegation policy revision are distinct. A private live actor
lease retains the genuine receipt for current revalidation by the owner factory.
Loss of that live lease, owner policy mismatch or failed admission refuses further
new-URL effects/forwarding. Restart is fail-closed for those leases, while durable
original public IDs remain available for revoke/recovery. No raw bearer is stored
in the actor binding. No copied 086 module or parallel issuer is introduced.

## Receiver evidence

A trusted owner-side observer normalizes Tailscale daemon state into a bounded frame:
receiver stable ID, backend running state, observation monotonic time, peer stable
ID, peer node addresses and canonical DNS name. Only node-owned TailscaleIPs count;
allowed subnet prefixes/route advertisements are not destination identities. DNS
answers must all bind to the same selected peer. Receiver restart/identity change,
ambiguous multi-peer answers or unknown observation age refuse.

A verified plan pins selected addresses, original HTTP Host/TLS name, peer stable ID,
port, scope, receiver identity, policy revision and expiry. Socket connectivity is
proved by bounded TCP/TLS establishment under the receiver's own network identity;
no HTTP request or supplied secret-link path is sent for verification. Actual app
requests occur only after authorized recipient admission. TLS validation remains on.

## Grants, sessions and recovery

Use canonical short derived IDs and existing opaque bearer generation. New URL
records add a private verified-target descriptor and actor/policy binding to the
same canonical grant lifecycle, not another token issuer. Exact wire/schema changes
must be versioned and receiver-capability negotiated before effects.

For URL grants: default one successful admission, configured finite integer1..20;
TTL60s..30days clamped by configured service/policy cutoff. Existing kinds retain
optional unlimited use semantics. One admission can make multiple scoped requests;
assets do not consume extra admissions. Session expiry is bounded by grant and
existing session maximum, with host-only secure cookies on an isolated grant origin.

States: prepared -> acceptance_unknown -> active -> revoking -> revoked; failures
retain cleanup_pending with original IDs. Expired records retain identity evidence.
Persist safe original effect identities before effects. A lost reply does not permit
remint. Actor/target replacement never authorizes cleanup on a replacement instance.
Private query/config and bearer material never enter public recovery records.

Each active forwarding operation is bound to a live original grant and a closeable
connection lease. Revocation invalidates new admission/forwarding and closes tracked
streams before reporting completion. An unclosed/unknown lease retains uncertainty.
Application sessions already established have their own backend lifetime.
