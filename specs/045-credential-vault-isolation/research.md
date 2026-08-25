# Research: Managed Credential Vault and Isolation Evidence

**Date**: 2026-08-25

**Scope**: Translate the reviewed OpenSandbox Credential Vault and isolation
behavior into a safe, evidence-gated Sandbox feature. This research is read-only
and does not claim live runtime proof.

The follow-up comparison of V8/`workerd`, QuickJS-ng, Wasmtime, gVisor,
Firecracker, and Scaleway Serverless Containers is recorded in
[`docs/v8-isolates-and-managed-sandbox-research.md`](../../docs/v8-isolates-and-managed-sandbox-research.md).

## Sources and evidence boundary

- OpenSandbox [Credential Vault guide](https://open-sandbox.ai/guides/credential-vault)
  and the reviewed pinned source document
  [credential-vault.md](https://github.com/opensandbox-group/OpenSandbox/blob/1264e7c82cefe45f3ac4018584b15638f75eb6d6/docs/guides/credential-vault.md)
  describe an egress-sidecar vault, exact request matching, `dns+nft`, and
  reinjection after sidecar replacement.
- OpenSandbox [network isolation](https://open-sandbox.ai/architecture/network-isolation),
  [secure container](https://open-sandbox.ai/guides/secure-container),
  [multi-tenancy](https://open-sandbox.ai/guides/multi-tenancy), and
  [pause/resume](https://open-sandbox.ai/guides/pause-resume) documents provide
  runtime and lifecycle comparison evidence.
- Current Sandbox code and evidence: `sandbox/secrets/`,
  `sandbox/isolation/`, `sandbox/runtimes/managed/`, `tests/live_native_acceptance.py`,
  and `specs/039-native-runtime-adoption/evidence/README.md`.
- The independent Sol High review is retained at
  `tmp/opensandbox-feature-plan-sol-review.md`. It is a review artifact, not a
  runtime acceptance result.

## Decision: explicit request broker for v1

The existing `EgressBroker` validates an opaque TCP CONNECT path, peer identity,
DNS/IP pins, SNI, and bounded transport. It cannot see an HTTP request after a
normal TLS handshake and therefore cannot safely add an authorization header to
arbitrary guest HTTPS clients.

The v1 boundary is an explicit application-layer request contract. A reviewed
guest client sends a normalized request to a per-instance broker. The broker
validates the immutable binding, resolves the opaque reference inside the
trusted control plane, originates a new TLS connection to the exact upstream,
adds only the approved bearer/API-key header, and returns bounded response data.
Arbitrary `curl`, Git, package-manager, and SDK transparency is not part of this
release.

This choice avoids importing the high-risk parts of transparent MITM: CA
distribution, TLS/ALPN handling, HTTP request smuggling, body buffering and
streaming ambiguity, service-mesh conflicts, and runtime-specific interception
compatibility. A later MITM proposal requires its own design and evidence gate.

## Comparison with current Sandbox

| Boundary | OpenSandbox | Current Sandbox | Consequence for this feature |
|---|---|---|---|
| Secret location | Egress sidecar memory; workload receives a fake or empty value | Trusted `sb secrets use` child receives the real value; native injector mounts a guest-readable file | Add a new broker-only path; do not relabel existing paths as a vault |
| Request scope | Exact scheme/host/port/method/path at transparent egress | Exact hostname/IP/port grants and SNI, but opaque CONNECT has no method/path | Keep network grants and add a separate credential-binding digest |
| Network default | Vault requires `dns+nft`; default-deny is recommended but compatibility mode can allow unmatched traffic | Managed-native has no default route and default-drop forwarding | Preserve the stronger default and never use a credential binding to widen egress |
| Privilege | Egress sidecar needs network redirection privilege | Fixed root helper mutates network; broker can remain unprivileged | Keep credential bytes out of the helper and its control protocol |
| Restart | In-memory vault is lost and must be reinjected | Native state is local and persistent; no broker-ready state exists yet | Persist references/digests only and require `credential_pending` recovery |
| Runtime claim | Docker/runc default, optional gVisor/Kata/Firecracker | Managed-native nspawn plus bwrap/AppArmor/seccomp; Compose is trusted orchestration | Refuse unsupported runtimes; do not claim Compose or runc hostile isolation |
| Multi-tenancy/HA | Kubernetes policy and external controls; local SQLite is documented | Local files/SQLite and one owner service | V1 is single-host/single-control-plane, not tenant isolation or HA |

## Current code facts that shape the design

1. `SecretService` and its registered-source/audit policy are appropriate for
   trusted-child use, but `run_with_secret` intentionally places the value in a
   child environment. It is not a hostile-workload vault.
2. `NativeCredentialStore` persists owner-only plaintext files. The existing
   `CredentialInjector` deliberately makes the file readable inside the guest.
   V1 records this as an explicit at-rest residual risk and does not claim
   process-memory-only protection.
3. Managed-native already provides digest/CAS-bound policy and grants,
   default-deny networking, exact veth/peer checks, a fixed privileged helper,
   an unprivileged supervised egress broker, and extensive effective-state
   verification. These are the foundation for the new seam.
4. The managed-native support matrix remains
   `implemented_unproven`, with no evidence ID and `adoptable: false`. The
   existing live acceptance matrix must close before this capability can be
   enabled or described as a proven boundary.
5. Compose, ordinary host jobs, and remote workspace ownership provide trusted
   orchestration and lifecycle controls, not hostile-code isolation. The feature
   must hard-refuse on those paths.

## Alternatives considered

### Transparent HTTPS MITM

Rejected for v1. It is the closest match to OpenSandbox's documented Vault, but
requires the broker to terminate or transform guest TLS, distribute a trusted
CA, handle HTTP/2 and ALPN, canonicalize all request forms, and define behavior
for redirects, streaming, unknown lengths, retries, and partial failures. The
current CONNECT broker cannot be extended mechanically to do this safely.

### Passing a credential file or environment variable into the guest

Rejected. It preserves the existing guest-readable or trusted-child semantics
and fails the feature's core outcome. It may remain available under its existing
explicit capability name, but it cannot satisfy outbound credential mediation.

### Loading credential bytes into the fixed root helper

Rejected. The helper needs only fixed network/machine verbs. Giving it request
parsing or secret material increases privilege and makes audit/cleanup harder.

### Reusing `sb secrets` with a public plaintext-return method

Rejected. The source registry and audit policy remain authoritative, but a new
internal resolver/lease must deliver bytes only through a one-use broker launch
channel. No CLI/MCP/API caller may retrieve plaintext.

### Supporting Compose or all runtime adapters together

Rejected. Compose accepts broad host paths and caller-selected features and lacks
the verified default-deny/isolation contract. Adapter parity is a future,
separate security project, not a reason to weaken managed-native gates.

## Adopted OpenSandbox patterns

- Exact immutable request bindings, including method and path rather than only a
  broad network destination.
- Fake/no guest credential values and last-boundary credential application.
- Desired-state references and digest-bound reinjection after restart/pause-like
  lifecycle events.
- Explicit capability/proof output rather than inferring support from code
  presence.
- Lifecycle hooks with bounded failure behavior.

## Deferred or rejected patterns

- Transparent MITM/CA injection and arbitrary client interception.
- Default-allow unmatched egress when a credential capability is active.
- Kubernetes pause/snapshot restore, node-agent pools, multi-tenancy, or HA
  claims.
- Ordinary runc/Docker as a hostile tenant boundary.
- Optional authentication/TLS for a vault control API.
- Complete memory zeroization, provider-side rollback, or universal response
  redaction claims.

## Research conclusion

Proceed to specification and design only behind the existing managed-native live
proof gate. The smallest safe implementation has three independent contracts:
`SecretReferenceResolver`, `CredentialBindingService`, and
`CredentialRequestBroker`. The broker is per-instance, explicit, unprivileged,
HTTPS-originating, strictly bounded, and fail-closed. At-rest encryption and
transparent interception are separate decisions, not hidden follow-up work.
