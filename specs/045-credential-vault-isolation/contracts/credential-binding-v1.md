# Contract: Credential Binding v1

## Purpose

Define the durable, opaque desired state that connects one managed-native
instance, one approved reference, one exact application request, and one
verified isolation/egress policy.

## Required fields

```text
binding_id       opaque stable identifier
instance_id      managed-native instance identifier
source_reference approved opaque source/key reference
policy_digest    64-character policy identity
egress_digest    64-character grant-set identity
broker_digest    64-character broker contract/config identity
scheme           canonical HTTPS scheme for MVP
host             canonical DNS host
port             canonical upstream port (443 for MVP)
method           one approved HTTP method
path             canonical exact path; no wildcard in MVP
auth_form        bearer or api_key
expires_at       absolute UTC timestamp
owner            authorized instance owner
version          positive CAS version
state            lifecycle state from data-model.md
```

The representation is illustrative contract notation, not permission to expose
plaintext or accept an arbitrary path. Unknown fields, duplicate security
fields, embedded secret-looking values, and a broader-than-network scope must
be rejected.

## Operations

- **Create**: validate ownership, source-reference shape, exact scope, expiry,
  policy/egress relationship, and proof readiness; persist metadata in
  `credential_pending`.
- **Compare-and-swap update**: require the current `version`; create a new
  version and invalidate any old lease before changing scope or reference.
- **Revoke**: close admission first, transition through `revoking`, drain or
  time out active sessions, then persist `revoked`.
- **Recover**: reload metadata only, resolve a fresh short-lived lease, verify
  all digests/effective state, and transition to `ready`.
- **Inspect**: return IDs, scope, state, expiry, digests, and reason codes only.

## Invariants

1. The source reference is opaque in every caller-visible response.
2. A binding never widens its corresponding network grant.
3. No use is admitted from `unconfigured`, `credential_pending`, `revoking`,
   `revoked`, `expired`, or `blocked`.
4. Revoke and expiry are monotonic for a version.
5. Durable serialization contains no resolved credential bytes.
