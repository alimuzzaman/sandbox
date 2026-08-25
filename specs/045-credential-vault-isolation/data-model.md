# Data Model: Managed Credential Vault and Isolation Evidence

## Design rules

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

An in-memory, short-lived authorization for one broker process and one binding
version. It includes only the minimum resolved material and deadline needed for
one operation. It is invalidated on revoke, expiry, process replacement,
policy/broker digest drift, or instance shutdown.

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

An append-only audit-safe event with operation, binding/instance IDs, actor,
decision, reason code, state transition, expiry, digests, and outcome class.
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
2. `credential_pending → ready` requires successful reference resolution,
   broker lease creation, exact policy/egress/broker digest match, and effective
   isolation verification immediately before use.
3. `ready → revoking` closes admission before draining active sessions.
4. `revoking → revoked` is complete only after the configured active-session
   deadline or a bounded failure report; a timeout never reopens admission.
5. Any expiry, restart, proof drift, source failure, or digest mismatch moves
   the binding away from `ready` and refuses credential use.
6. A new authorization gets a new version and must not reuse an old lease.

## Trust/data-flow boundary

```text
operator -> binding metadata (reference + exact scope + digests)
trusted control plane -> resolver -> one-use broker launch channel
untrusted guest client -> explicit request contract -> unprivileged broker
broker -> exact TLS upstream with approved auth header
broker -> bounded response/error -> guest client
durable state/audit <- IDs, digests, state, reason codes only
```

The fixed root helper remains responsible for fixed network/machine operations
only. It does not parse HTTP, receive credential bytes, or expose a control
socket to the workload.

## Persistence and recovery

Persist: binding metadata, opaque source reference, policy/egress/broker digests,
CAS version, state, expiry, owner, and audit-safe timestamps. Do not persist:
resolved bytes, bearer/API-key headers, request bodies containing credentials,
or a plaintext lease.

After broker or machine restart, all bindings enter `credential_pending`. Recovery
must recreate a fresh process-bound lease and re-check the effective isolation
and egress proof before any request is admitted.
