# Data Model: Managed Credential Vault and Isolation Evidence

## Design rules

The production authority and lease model is v2 as defined by
[`contracts/credential-broker-controller-authority-v2.md`](./contracts/credential-broker-controller-authority-v2.md).
The earlier v1 transport is fake/local-only history and cannot be negotiated,
translated, or wired as a production fallback.

- Durable records contain opaque references, digests, timestamps, state, and
  audit metadata only. Credential bytes are never serialized into policy,
  registry, job, snapshot, status, or audit records.
- Every mutable record is instance-scoped, owner-scoped, versioned, and updated
  with compare-and-swap semantics.
- A credential binding is narrower than the associated network grant. A network
  grant alone never authorizes credential use.
- A missing, stale, ambiguous, or unverifiable proof is a refusal, not a
  degraded success.

## Entities

### `CredentialBinding`

| Field | Meaning | Invariant |
|---|---|---|
| `binding_id` | Stable opaque identifier | Unique within the control plane |
| `instance_id` | Managed-native instance owner | Must match the verified machine identity |
| `source_reference` | Approved opaque credential reference | Never a plaintext value or arbitrary path |
| `policy_digest` | Managed isolation policy identity | Must match effective policy before use |
| `egress_digest` | Exact network grant-set identity | Must authorize at least the binding destination |
| `broker_digest` | Broker configuration/protocol identity | Must match the supervised broker |
| `scheme`, `host`, `port` | Exact upstream destination | Canonicalized; HTTPS/443 in MVP unless separately approved |
| `method`, `path` | Exact request scope | Canonicalized; no wildcard in MVP |
| `auth_profile` | Registered header profile | `authorization_bearer` or `x_api_key` only in MVP; guest cannot choose the header name |
| `expires_at` | Absolute expiry | Must be future at creation and rechecked at use |
| `state` | Lifecycle state | Must follow the state machine below |
| `version` | CAS version | Increments on every mutation |
| `owner` | Authorized operator identity | Must match instance ownership policy |
| `created_at`, `updated_at` | Audit-safe timestamps | Monotonic per record |

### `SecretReference`

An opaque reference resolved by the trusted control plane. It identifies an
approved source/key without exposing a value to callers. The resolver must
validate source registration, ownership, file identity and bounds using the
existing source policy or an explicitly registered adapter.

### `BrokerLease`

An in-memory, short-lived v2 carrier issued only by the persistent per-machine
controller after the broker has acknowledged one exact operation authorization.
It binds machine, controller/broker epochs, operation/request, binding/version,
fixed `auth_form`, policy/egress/broker/proof/effective-isolation digests,
evidence and decision identities, authorization digest, sequence, expiry, and
descriptor size. It carries one sealed anonymous credential descriptor and is
atomically consumed on the first transfer attempt. Lease sequence is scoped to
the exact controller/broker epoch pair. A terminal 444-byte `LEASE_ACK_V2`
returns on the same lease socket only after the matching POST audit commit; ACK
timeout or loss is terminal and never retries the credential-bearing request.

### `ControllerAuthorization`

An ephemeral controller decision for one broker-created operation and request
digest. The controller is the sole binding/source/proof/egress authority. The
broker does not re-evaluate proof; it authenticates the controller process and
exactly matches the authorization's proof, effective-isolation, and evidence
identity against sealed helper-produced configured expectations. The
authorization digest commits to every decision field and is never a bearer
capability.

### `OperationAuditRoot`

A secret-free semantic audit identity with distinct PRE and POST phase IDs,
canonical fingerprints, durable commit IDs, and replay tombstones. Transport
sequence is not audit identity. An identical semantic retry with the next
transport sequence returns the same commit; different content for an existing
phase ID conflicts. A durable PRE without POST after controller crash gets an
internal recovery tombstone with an indeterminate/possible outcome before new
activation; it does not require or reconstruct the lost operation ID.

### Broker lease transport

The production v2 transport is one sealed Linux anonymous memory descriptor
created by the persistent controller and transferred with exactly one
`SCM_RIGHTS` entry in one 732-byte big-endian
`AF_UNIX` `SOCK_SEQPACKET` frame.
The broker owns the abstract-namespace listening and accepted socket
descriptors; the dispatcher owns the client socket and original anonymous
descriptor until the single transfer attempt. The root helper and service
supervisor own or inherit none of those descriptors.

Before transfer, the controller verifies the connected peer against the exact
broker PID, service UID, process start identity, unit/cgroup identity, and
executable/config digest reported through bounded secret-free lifecycle state.
The broker independently verifies the lease sender's exact controller UID/GID,
PID, process start identity, executable digest, unit/cgroup digest, and config
digest from kernel-observed state and sealed expectations before receiving a
descriptor; UID alone is never sufficient. This is a single-host,
single-control-plane trust boundary: root and other processes running as that
trusted owner are outside the hostile-workload threat model.

The non-secret frame binds protocol v2, both epochs, lease ID/sequence,
operation/request, authorization digest, authentication form, machine,
binding/version, proof/effective-isolation/evidence and required digests,
decision, both expiries, and descriptor size.
The broker rejects a wrong peer, extra/missing descriptor, wrong anonymous-file
type or seals, stale epoch, identity/digest mismatch, expiry, oversize, duplicate
lease ID, truncation, or trailing data before use. It atomically records the
lease ID as consumed before reading/applying the credential. Transfer or
acknowledgement failure is terminal and never retried. Fixed offsets, widths,
padding, hash, bounds, and endian rules live only in the v2 contract.

### `BrokerRequest`

A normalized request from the reviewed guest client: binding ID/version,
scheme/host/port, method/path, bounded headers/body, deadline, and correlation
ID. It contains no credential value. It is rejected before resolution when its
scope does not match the binding.

### `CapabilityProof`

An operator-facing snapshot containing capability name, runtime, support tier,
evidence ID, prerequisite readiness, effective observation results, policy/
egress/broker digests, and bounded failure reasons. It never includes secret
values or secret-derived reversible data.

### `LifecycleRecord`

An append-only audit-safe event with controller event/audit phase identity,
binding/instance IDs, actor, decision, reason code, state transition, expiry,
digests, and outcome class. Durable lifecycle/audit records never persist the
broker operation ID, request digest, lease ID, or authorization digest.
It distinguishes an effect whose audit append failed from an effect that was
never attempted.

## Lifecycle state machine

| State | Meaning | Allowed next states |
|---|---|---|
| `unconfigured` | No desired binding exists | `credential_pending`, `revoked` |
| `credential_pending` | Desired metadata exists but bytes/proof are not ready | `ready`, `blocked`, `revoked`, `expired` |
| `ready` | Binding and all effective proofs match | `revoking`, `expired`, `credential_pending`, `blocked` |
| `revoking` | New use is closed and active sessions are draining | `revoked`, `blocked` |
| `revoked` | Explicitly disabled; stale state cannot reopen it | `credential_pending` only after a new versioned authorization |
| `expired` | Deadline passed | `credential_pending` only after a new versioned authorization |
| `blocked` | A required prerequisite, proof, source, or digest is missing/stale | `credential_pending`, `revoked` |

### Transition rules

1. Create/update stores desired metadata and enters `credential_pending`; it
   never loads bytes into durable state.
2. `credential_pending → ready` requires the persistent controller to evaluate
   source, policy, egress, proof, and effective isolation, followed by the
   broker's exact match against sealed configured expectations. Lease creation
   occurs only after operation authorization acknowledgement.
3. `ready → revoking` closes admission before draining active sessions.
4. `revoking → revoked` is complete only after the configured active-session
   deadline or a bounded failure report; a timeout never reopens admission.
5. Any expiry, restart, proof drift, source failure, or digest mismatch moves
   the binding away from `ready` and refuses credential use.
6. A new authorization gets a new version and must not reuse an old lease.

## Trust/data-flow boundary

```text
operator -> binding metadata (reference + exact scope + digests)
per-machine controller -> binding/proof/egress authorization + durable audit
per-machine controller -> resolver -> one-use v2 broker lease channel
untrusted guest client -> explicit request contract -> unprivileged broker
broker -> exact TLS upstream with approved auth header
untrusted upstream -> bounded/redacted-best-effort response -> guest client
durable state/audit <- IDs, digests, state, reason codes only
```

The controller and application Python processes are trusted. Code capable of
same-process reflection, monkeypatching, closure inspection, low-level object
construction/mutation, or module-global mutation has already compromised that
trusted process and is outside the hostile-guest model. Exact bridge/capability
types and receipts are public-API misuse checks, not cryptographic authority
against such code. The untrusted guest has no Python object/code execution in
either trusted process and never receives the process-local bridge. Its only
entry is the authenticated guest socket and exact `BrokerRequest` wire schema,
which contains data fields only and cannot select imports, callbacks, Python
objects, controller filesystem paths, validators, clocks, sessions, or legacy
handlers.

The one-use broker launch channel is the sealed anonymous descriptor transfer
defined above. The fixed root helper remains responsible for fixed
network/machine and digest-bound broker lifecycle operations only. It does not
parse HTTP, resolve or receive credential bytes, own a lease descriptor/socket,
or expose a control socket to the workload.

## Persistence and recovery

Persist: binding metadata, opaque source reference, policy/egress/broker digests,
CAS version, state, expiry, owner, and audit-safe timestamps. Do not persist:
resolved bytes, bearer/API-key headers, request bodies containing credentials,
or a plaintext lease. Audit phase fingerprints/commit tombstones persist for
semantic idempotency, but operation/request/lease/authorization digests do not.
The abstract sockets, both process epochs, operations, transport sequences,
consumed-lease set, anonymous descriptor, and transfer frame are process-
lifetime state only.

After broker or machine restart, all bindings enter `credential_pending`. Recovery
must recreate a fresh process-bound lease and re-check the effective isolation
and egress proof before any request is admitted.
