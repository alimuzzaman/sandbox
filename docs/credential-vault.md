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

## Broker service lifecycle

The credential broker runs as its own unprivileged, per-instance service. The
root helper's entire protocol for it is three fixed verbs, each carrying only
the machine identity and the policy, egress, and broker digests:

```text
credential-broker-start  machine_id policy_digest egress_digest broker_digest
credential-broker-status machine_id policy_digest egress_digest broker_digest
credential-broker-stop   machine_id policy_digest egress_digest broker_digest
```

There is no endpoint, descriptor, path, unit, user, or service-property
argument, and no credential byte reaches the helper's argv, environment, unit
text, protocol, stdout, or stderr. Each verb emits exactly one bounded
document containing the identity, the observed state, the unit name, and
`admission_open: false`. The control-plane supervisor refuses any helper
output that is oversized, malformed, foreign, or claims open admission.

`credential-broker-start` is deliberately terminal in this release. It proves
policy and egress ownership, then refuses because the reviewed broker
executable and its helper-owned service record do not exist on any host yet.
Activating a unit for a service with no authorized Ubuntu lifecycle proof
would be inventing support, so start reports `blocked` and changes nothing.

`credential-broker-stop` closes admission and stops only the exact unit whose
description matches the requested identity. An absent unit is a successful,
idempotent no-op; a drifted or foreign unit is evidence and is never stopped;
an unanswered read is `unavailable`, never absence.

Cleanup runs the broker first: `credential_broker` is the leading step of the
managed-native cleanup order, ahead of services, machine, network, image, and
policy removal. The step is skipped entirely unless a broker supervisor or a
broker cleanup entry is explicitly composed, which is not the default. The
broker observes itself through its own status verb, so an unwired or silent
supervisor produces a retained recovery item rather than a false absence.

Composition stays inert: `managed_native_dependencies` leaves
`credential_broker_service` absent, and `managed_native_credential_broker_service`
must be called deliberately. Constructing it starts nothing.

## Acceptance seam

The public acceptance surface exists so the authorized live matrix can drive
the feature through `./sb` without any plaintext path:

```sh
./sb native credential-bind --json \
  --source-ref <alias>/<key> --binding-id <id> --instance-id <machine-id> \
  --host api.example.com --path /v1/ping --method GET \
  --auth-form authorization_bearer --expires-at 2030-01-01T00:00:00Z
./sb native credential-request --json --binding-id <id> --binding-version 1 \
  --method GET --path /v1/ping
./sb native credential-revoke  --json --binding-id <id> --binding-version 1
```

Every value above is non-secret. The command has no option, prompt, file, or
environment path that accepts a credential value, and the opaque source
reference is reduced to a digest in the emitted document rather than echoed.
Metadata that is missing, ambiguous, or not exact is refused with
`credential_metadata_invalid` and the rejected value is never echoed back.

All three actions are proof-gated and currently refuse with
`credential_acceptance_unproven`: the capability remains
`implemented_unproven`, `adoptable: false`, with a null evidence ID. They
mutate nothing, and no local result promotes the support tier.

## Development evidence

The foundational contracts are covered by the focused unit suites documented in
[`specs/045-credential-vault-isolation/quickstart.md`](../specs/045-credential-vault-isolation/quickstart.md).
These tests prove canonicalization, lifecycle/CAS rules, metadata persistence,
source ownership checks, one-use lease behavior, secret-free serialization,
and fail-closed capability reporting. They do not replace the authorized live
native proof gate or authorize credential-bearing use.
