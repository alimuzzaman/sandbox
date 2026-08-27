# Contract: Standalone Credential Broker Service v1

## Status

This security-reviewed contract defines the minimum service, transport,
lifecycle, and cleanup invariants needed to prepare T022 locally. T033 accepted
the concrete v1 transport design used by the local T034/T035 contract and
implementation increments. It does not enable a credential-bearing runtime path,
close T003, prove T022 or T029, or authorize an evidence/support-tier promotion
under T031.

The capability remains `implemented_unproven` with `adoptable=false` and no
evidence ID. T034 is complete as a fake/local contract suite; T035 and later
implementation remain open.

The local retained-guest and supplied-lease coordinator does not close T035.
T035 remains open for the production controller abstract `AF_UNIX`
`SOCK_SEQPACKET` listener/event loop, `recvmsg`/`SCM_RIGHTS` endpoint, kernel
peer observer, cross-process configuration and entrypoint, guest disconnect and
deadline loop, and lifecycle/audit observer. T022, T029, and T031 remain blocked;
this contract does not change support, evidence, or adoptability state.

## T033 security review decision

The selected v1 lease mechanism is one sealed Linux anonymous `memfd` transferred
once with `SCM_RIGHTS` in one bounded frame over a broker-created abstract
`AF_UNIX` `SOCK_SEQPACKET` socket. This is permitted only by the clarified
FR-008/SC-002 trusted-boundary exception. No guest, helper, supervisor, durable,
status, audit, or retained channel may carry or inherit the descriptor or bytes.

This design assumes the documented single trusted control-plane owner UID. Root
and another process running as that trusted owner can subvert local processes
and are outside the hostile-workload threat model; the feature makes no
multi-tenant host claim. The guest, sibling instances, broker service UID, and
upstream response remain untrusted.

## Components and ownership

The v1 runtime has four distinct boundaries:

1. The trusted control plane owns binding metadata, the registered-source
   resolver, proof evaluation, audit-safe lifecycle state, and one-use lease
   issuance.
2. One unprivileged credential-broker service exists for exactly one
   managed-native `machine_id`. It validates the explicit request contract and
   performs the approved upstream HTTPS operation.
3. The untrusted guest client uses one instance-bound request endpoint. It never
   connects to a root-helper, resolver, or broker-supervision socket.
4. The fixed root helper performs only digest-bound service lifecycle and
   cleanup-observation verbs. It never receives, resolves, reads, forwards, or
   logs credential bytes.

The service identity MUST bind the `machine_id`, policy digest, egress digest,
broker protocol/config digest, executable digest, endpoint identity, and fixed
resource limits. No unit name, filesystem path, command, user, port, or service
property may be supplied directly by the guest or an unvalidated caller.

## Guest request transport

The guest transport MUST be dedicated to one managed-native instance. A shared
host listener, generic forward proxy, root-helper control socket, or endpoint
reachable from another instance is forbidden.

The v1 guest transport is an exact private-veth TCP listener bound to the
instance's verified host-side interface/address and fixed plan-derived port. It
must prove all of the following before request parsing:

- the listener is bound only to the intended instance boundary;
- the peer belongs to the exact `machine_id` and cannot arrive through another
  host, sibling instance, loopback alias, or forwarded route;
- the broker epoch matches the supervised broker process and rotates on broker
  replacement or restart;
- the request binding ID/version, instance ID, and transport identity agree;
- network reconciliation adds only the exact guest-to-broker reachability and
  does not widen the existing upstream default-deny grant; and
- transport material is not a credential, is never an authorization header for
  the upstream, and is excluded from status, audit, retained output, and durable
  binding state.

Source address alone is not identity. Admission requires the exact verified
host veth, exact local/peer tuple, helper-owned per-instance network rule and
digests, request machine/binding/version, and current broker epoch. The listener
must use exact interface binding where Linux supports it and must reject traffic
arriving through loopback, forwarding, another interface, or another network
namespace. The epoch is non-secret freshness metadata, not a bearer capability.

## Internal operation claim and guest result boundary

The broker, not the guest or controller, creates a fresh opaque
`operation_id` after the complete `broker-request-v1` frame and transport have
passed validation. It also computes `request_digest` over the exact canonical
request frame. Both values remain broker-private or on the authenticated
trusted controller/lease channels. They MUST NOT appear in guest responses,
status, audit, retained output, logs, configuration, or durable binding state.

The broker owns this bounded in-memory state machine:

```text
pending -> claimed -> lease_bound -> completed
    |         |            |       -> refused
    |         |            `------- > indeterminate
    `---------`-------------------- > refused
```

`pending` means no trusted controller owns the operation. `claimed` means one
authenticated controller connection owns it but no credential descriptor has
been bound. `lease_bound` begins only after the exact operation ID, request
digest, machine, broker epoch, binding/version, policy/egress/broker digests,
expiry, and controller ownership all match. A deadline, revoke, or disconnect
before `lease_bound` is a terminal refusal. Loss of controller or acknowledgement
certainty after `lease_bound` is terminal `indeterminate`; it MUST NOT be
retried. Terminal records are removed after the guest result is delivered or
the guest disconnects. Retained terminal records remain strictly bounded so
they cannot permanently consume the 16 active-operation slots.

The controller boundary is a broker-owned abstract `AF_UNIX`
`SOCK_SEQPACKET` endpoint separate from the guest and descriptor endpoints. Its
canonical bounded messages are:

```text
CLAIM_NEXT  machine_id broker_epoch sequence
CLAIMED     machine_id broker_epoch operation_id request_digest binding_id binding_version
NO_PENDING
REFUSE      machine_id broker_epoch sequence operation_id request_digest code
```

`CLAIMED` additionally carries the exact reviewed request-scope projection:
HTTPS scheme, canonical host/port/method/path, total header bytes, body bytes,
content type, bounded deadline, and correlation ID. It carries neither header
values nor body bytes. The full `request_digest` still commits to the canonical
request including those omitted values, so the controller can authorize exact
scope and bounds before dispatch without receiving guest application content.

Before parsing a controller frame, the broker authenticates the exact configured
controller UID, PID/start identity, and executable identity from kernel-observed
peer state. The broker requires the current machine and broker epoch, a strictly
monotonic per-connection sequence, and exact claim ownership. Unknown message
types, duplicate/out-of-order sequences, another connection's operation,
stale epochs, and refusal codes outside the reviewed fixed allowlist fail
closed. `CLAIM_NEXT` never accepts an operation ID from the controller.
`CLAIMED` is trusted-channel metadata, not a bearer capability.

The trusted lease frame includes the broker-created `operation_id` and
`request_digest`. The descriptor endpoint MUST rendezvous them with the exact
claimed operation and controller owner before descriptor inspection or
credential use. A mismatch consumes/refuses that lease attempt and can never
fall back to a binding-only lookup. Any isolated pre-controller registry used
by local descriptor tests is explicitly legacy and MUST bind both fields; it is
not the new operation flow and is not runtime wiring.

The guest submits one canonical `broker-request-v1` frame and keeps that exact
connection open until one terminal result is written. No `pending` response is
part of the guest wire contract. Success is the exact bounded shape `ok`, HTTP
status, reviewed response headers, base64-encoded binary body, and
`correlation_id`. Failure is the exact bounded shape `ok=false`, reviewed safe
code/message, retryability, and `correlation_id`. Neither shape contains an
operation ID, lease ID, request digest, descriptor metadata, controller
identity, credential, or raw upstream diagnostic. EOF is not required to
finish request parsing; readily observable trailing bytes are refused.

The exact v1 guest response-header allowlist is lowercase `cache-control`,
`content-language`, `content-type`, `etag`, `last-modified`, and `retry-after`.
All other response headers, including `set-cookie`, `authorization`, location,
arbitrary `x-*` metadata, and differently cased duplicates, are refused rather
than copied to the guest. This is an allowlist, not a sensitive-header denylist.

Credential application crosses only `CredentialOperationAdapter`. Production
construction requires a descriptor-backed, request-scoped
`CredentialRequestBroker` configured with `VerifiedHttpsUpstream`, preserving
its proof, egress, concurrency, drain, error-normalization, and redaction gates.
Direct `VerifiedHttpsUpstream` construction is forbidden.

The reviewed supplied-lease entry point is
`CredentialRequestBroker.request_with_lease(request, lease,
transport_identity=...)`. It runs on the existing broker instance; it MUST NOT
clone the broker or copy its dependencies into a second admission authority.
The entry point therefore preserves that instance's closed state, revoked-
binding set, active-request/concurrency limit, drain state, proof and egress
checks, binding/scope validation, typed upstream, response normalization, and
credential redaction. The supplied object is only the one-use descriptor lease
created inside the broker after the exact peer, epoch, operation/request,
binding/digest, expiry, seal/type/size, and replay checks pass. It is consumed
once through the broker's existing credential callback; credential bytes remain
inside that callback and never become request metadata, return data, retained
state, or diagnostics. A supplied lease cannot bypass admission, replace proof
or egress decisions, select another upstream, reopen a closed/revoked binding,
or escape the broker's concurrency/drain accounting.

`CredentialOperationAdapter` may invoke only that supplied-lease entry point on
the reviewed existing broker instance. It never accepts an arbitrary completion
callback. An offline fake adapter requires an explicit test gate and injected
fake socket seam. The adapter returns a reviewed broker response or an explicit
reviewed refusal/indeterminate outcome. Exceptions or an invalid/missing
terminal result are `indeterminate` once lease use may have occurred.
A descriptor acknowledgement reports `completed` only after the terminal
broker/upstream outcome is known, `refused` only when non-effect is known, and
otherwise `indeterminate`.

## Trusted one-use lease channel

The guest request transport and the trusted lease channel are separate. The
trusted channel connects only the control-plane resolver to the exact supervised
broker process after request scope, proof, egress, binding version, expiry, and
transport checks pass.

The trusted control-plane lease dispatcher creates a `memfd` with close-on-exec
and sealing enabled, writes the bounded credential bytes, and applies write,
grow, shrink, and further-seal prohibitions. It then connects to the abstract
socket created and owned by the broker. The abstract address is a non-secret,
plan-derived endpoint identity; address squatting only causes refusal.

Before `sendmsg`, the dispatcher uses kernel peer credentials plus bounded
helper status to verify the exact broker PID, service UID, process start
identity, owned unit/cgroup identity, and executable/config digest. The broker
uses kernel peer credentials to require the configured control-plane owner UID
before accepting ancillary data. It rejects the peer before reading a frame.
The broker owns the listening and accepted descriptors. The dispatcher owns the
client and original `memfd` descriptors until the single send attempt. The root
helper and supervisor own, connect to, receive, or inherit none of them.

One binary frame binds protocol version, lease ID, broker epoch, machine ID,
operation ID, request digest, binding ID/version, policy/egress/broker digests,
expiry, and exact descriptor size. Exactly one `SCM_RIGHTS` descriptor is
allowed. The broker verifies the
anonymous-file type, required seals, size, frame bounds, peer, epoch, identities,
digests, and deadline. It atomically records the lease ID consumed before
reading the descriptor or contacting upstream. Missing/extra descriptors,
truncation, trailing data, stale/duplicate IDs, or any mismatch are terminal.

The accepted mechanism satisfies these rules:

- credential bytes never enter root-helper argv, stdin, environment, protocol,
  unit text, service properties, configuration files, staging paths, durable
  sockets, journals, status, audit, exceptions, or retained output;
- the helper protocol has no endpoint or descriptor verb and cannot receive or
  replay a lease through an authorized operation;
- the guest and sibling instances cannot discover or connect to the channel;
- the lease is process-bound, binding-version-bound, expires before or with the
  binding, and can be consumed at most once;
- the broker acknowledges only non-secret lease ID and bounded outcome metadata;
- failure or indeterminate acknowledgement closes the lease and is not retried
  as a credential-bearing request; and
- buffers and descriptors are closed promptly with best-effort cleanup, without
  claiming universal memory zeroization.

The dispatcher closes its descriptor and socket immediately after the one send
attempt. The broker closes the accepted socket and descriptor after one bounded
read/use. Restart destroys the abstract listener, rotates the epoch, and drops
the in-memory consumed-ID set; old frames fail the epoch/process checks. Revoke,
expiry, drift, or cleanup closes admission before descriptor/session draining.
Best-effort buffer cleanup is required, but neither `memfd` close nor process
exit is represented as universal memory zeroization.

All no-secret surfaces include argv, environment, unit text/properties,
helper stdin/stdout/stderr/protocol, guest request transport, plan/config and
runtime directories, filesystem paths, `/proc`-reported command/environment,
registry/policy/snapshot state, logs/journal/exceptions, status/audit/telemetry,
test failure output, retained job output, and cleanup/recovery records.

## Fixed helper protocol

The helper-side service protocol is limited to these intended fixed verbs:

```text
credential-broker-start  machine_id policy_digest egress_digest broker_digest
credential-broker-status machine_id policy_digest egress_digest broker_digest
credential-broker-stop   machine_id policy_digest egress_digest broker_digest
```

All arguments are validated non-secret identities. The helper derives the unit,
runtime directories, executable, and service properties from fixed code and a
helper-owned secret-free record. It discards child output except for bounded,
schema-checked non-secret status. It refuses absent ownership proof, changed
descriptions/digests, foreign units, unexpected files, symlinks, or caller-
selected service properties.

`start` creates a broker in closed/`credential_pending` state. It does not open
credential admission. `status` proves the exact process and digest identity but
does not make the capability adoptable. `stop` closes admission before bounded
drain and stops only the exact owned service.

## Lifecycle and cleanup order

The required lifecycle order is:

```text
predecessor/effective proof
  -> start broker closed
  -> establish the reviewed broker-owned seqpacket lease endpoint
  -> recheck policy/egress/broker/source/binding digests
  -> open admission
  -> close admission on revoke, expiry, drift, restart, or cleanup
  -> invalidate leases and drain within the documented bound
  -> stop and observe broker absence
  -> remove guest transport reachability
  -> continue machine/network/image/policy cleanup
```

Cleanup observation MUST distinguish `present`, `absent`, `drifted`, and
`unavailable`. A failed read is never absence. A foreign or drifted unit is never
stopped. Broker cleanup must precede network or machine removal, be idempotent,
and retain a recovery item when ownership or absence cannot be proved.

After a broker or machine restart, durable bindings enter
`credential_pending`; no old epoch, lease, descriptor, socket connection, or
process identity can reopen admission.

## Local verification boundary

Local unit and contract tests may prove validation, fixed argv, serialization,
no-secret surfaces, fail-closed state transitions, ownership checks, and cleanup
ordering. They do not prove Linux process isolation, systemd ownership, guest
reachability, hostile no-leak behavior, drain timing, or cleanup on Ubuntu 24.04.

T022 remains open until its local seams and authorized helper/service lifecycle
proof pass. T029 remains open until the public `./sb` acceptance path exercises
the full live matrix. T031 remains open until an independent reviewer accepts
the exact source revision, contracts, live evidence, cleanup evidence, and
support decision.

## Explicit refusals

- No Compose, Herd, macOS, generic host-job, Kubernetes, or remote-workspace
  fallback.
- No plaintext credential CLI, MCP, API, fixture, or helper operation.
- No transparent HTTPS interception, generic proxying, shared broker pool, or
  cross-instance transport.
- No support-tier or evidence-ID promotion from code presence or local tests.
