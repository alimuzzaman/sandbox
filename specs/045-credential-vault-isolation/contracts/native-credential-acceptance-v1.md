# Native credential acceptance v1 (local contract)

Status: implemented seams, unproven. This contract is not live evidence and
does not make managed native credential mediation adoptable.

The only public operation is `native credential-acceptance`. It is routed as a
runtime `OperationRequest`; it never reads registry or runtime state directly
and never falls back to Compose or Herd.

## Input

Every message is one exact tagged object. Unknown or missing fields are
rejected. Identifiers and source references are opaque bounded strings. Digests
are lowercase 64-character SHA-256 values.

- `bind`: `action`, `binding_id`, positive `version`, `machine_id`, `owner`,
  opaque registered `source_reference`, `scheme=https`, exact lowercase `host`,
  `port=443`, `method` (`GET` or `POST`), exact non-wildcard HTTP `path`,
  reviewed `auth_profile` (`authorization_bearer` or `x_api_key`), future
  timezone-qualified `expires_at`, and exact
  `policy_digest`, `egress_digest`, and `broker_digest`.
- `request`: `action`, binding/version/machine/owner, reviewed `content_type`,
  `deadline_seconds` from 1 through 30, and opaque `correlation_id`.
- `revoke`: `action` plus binding/version/machine/owner only.

There is no public field for plaintext credentials, request body, guest header,
environment, filesystem path, descriptor, lease ID, or operation ID. Serialized
authority objects and extra fields are refused.

## Gate order

The operation checks an exact trusted binding lookup (binding/version,
machine/owner, and policy/egress/broker/executable/config digests),
managed-native preflight, sealed invocation-local proof-candidate authority,
`implemented_unproven`/`adoptable=false`, exact T036 ownership-enriched
`credential_pending` status with `admission_open=false` and matching digests,
binding health, and egress authorization
before invoking the tagged action. A missing dependency returns
`credential_acceptance_unavailable` with `mutated=false`.

## Output

The public projector allows only: `ok`, `action`, `state`, `mutated`, public
binding/machine/owner identifiers, version, canonical scope, request digest,
correlation ID, decision, bounded reason, sealed `proof_candidate` state, and
`adoptable=false`. Invalid/unavailable calls emit `proof_candidate=false` until
the sealed gate has passed. It removes source references, lease/operation IDs,
descriptors, diagnostics, helper output, headers, bodies, and unknown fields.

Partial or malformed results are refusals and are never evidence. The offline
harness may inject pure doubles, but it may not claim broker execution, live
credential use, or Ubuntu proof.
