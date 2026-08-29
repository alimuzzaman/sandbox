# Managed Credential Vault

Sandbox's Credential Vault is a proof-gated, managed-native-only contract for
one exact outbound API operation. It is not a replacement for `sb secrets`,
the existing native credential file injector, or the Compose runtime.

## Current support

Inspect the declaration and the status-only binding projection with:

```sh
./sb native credential-status --json
```

The command is read-only. It reports `support_tier`, `evidence_id`,
`adoptable`, prerequisite/effective-observation results, and binding IDs,
versions, exact non-secret scopes, lifecycle states, and expiry timestamps.
It never returns source contents, credential values, authorization headers,
leases, or reversible hashes. The current managed-native declaration is
`implemented_unproven` and `adoptable: false`; status therefore remains
blocked until the authorized Ubuntu 24.04 proof matrix and this feature's live
hostile checks are complete.

## Boundary

The v1 design uses an explicit application-layer request broker. A reviewed
guest client submits a binding ID/version, exact HTTPS host/port/method/path,
bounded headers/body, and a correlation ID. The broker validates the request
and proof digests before resolving a registered opaque source reference. Only
the broker applies the registered `authorization_bearer` or `x_api_key`
profile. Redirects, guest-supplied authentication headers, arbitrary proxy
requests, and transparent HTTPS MITM are refused.

Credential bytes must remain outside guest environment, argv, files, mounts,
snapshots, policy/registry/audit records, control channels, and retained
output. The one-use lease callback is an internal broker seam, not a public
plaintext-return API. Expiry, revoke, source-policy failure, proof drift, and
restart recovery fail closed; restart re-enters `credential_pending`.

## Storage and residual risk

Durable state stores only binding metadata, source references, policy/egress/
broker digests, owner, version, lifecycle state, and timestamps. The existing
owner-only native credential store remains a plaintext-at-rest residual risk;
this feature does not claim encryption or universal process-memory
zeroization. The fixed privileged helper does not receive credential bytes.

The supported boundary is one local control plane on Ubuntu 24.04 with the
existing managed-native isolation evidence. Compose, Herd, Valet, generic host
jobs, Kubernetes, multi-tenant control planes, HA stores, snapshots, and
transparent interception are unsupported and must remain explicit refusals.

## Development evidence

The foundational contracts are covered by the focused unit suites documented in
[`specs/045-credential-vault-isolation/quickstart.md`](../specs/045-credential-vault-isolation/quickstart.md).
These tests prove canonicalization, lifecycle/CAS rules, metadata persistence,
source ownership checks, one-use lease behavior, secret-free serialization,
and fail-closed capability reporting. They do not replace the authorized live
native proof gate or authorize credential-bearing use.
