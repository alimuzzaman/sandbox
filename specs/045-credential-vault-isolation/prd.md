# Product Requirements Draft: Managed Credential Vault and Isolation Evidence

**Status**: Ready for Specification

**Created**: 2026-08-25

**Last Refined**: 2026-08-25

**Input**: "Adopt a safe, managed-native outbound credential vault and tighten isolation evidence inspired by OpenSandbox, while prioritizing a few low-risk lifecycle and capability features worth adopting."

**Drafting Model**: `gpt-5.6` (root planning pass; Terra refinement was not available as a root override)

**Final Validation**: PASS — gpt-5.6-sol High

**Validated On**: 2026-08-25

**Artifact Owner**: `speckit.prd.refine`

**Next Stage**: `speckit.specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox already protects secret references and can inject a selected credential
into a bounded trusted child process. Managed-native guests also have a
default-deny egress broker. The missing product capability is safe outbound use
by an untrusted guest: a workload should be able to call one approved external
service without receiving the real credential in its environment, arguments,
filesystem, logs, or retained job output. The current managed-native path uses
a separate plaintext owner-only `NativeCredentialStore`; it is not the same
source seam as the `sb secrets` broker and must not be treated as one.

OpenSandbox's Credential Vault demonstrates the desired product shape—request
binding at the egress boundary, explicit destination matching, short-lived
capabilities, and reinjection after lifecycle events—but its documented
transparent HTTPS interception and Kubernetes-specific controls are not a safe
default for this repository. This feature should adopt the narrow, auditable
behavior while preserving Sandbox's existing least-disclosure and
managed-native boundaries.

## Users and Desired Outcomes

- **Sandbox operator**: Declare which external service a managed-native instance
  may call and which approved credential reference may be used, without placing
  the secret in guest configuration.
- **Sandbox workload/agent**: Make an approved outbound request using a stable
  endpoint contract while seeing only a non-secret placeholder or no credential.
- **Security reviewer**: Confirm from bounded evidence that the secret was never
  exposed to the guest or retained in logs, argv, environment, policy records,
  or audit output, and that revocation and expiry take effect.

## Goals

- Provide a managed-native-only outbound credential mediation path with explicit
  host, scheme, port, method, path, and credential-reference binding, initially
  through a reviewed application-layer broker contract rather than transparent
  interception of arbitrary HTTPS clients.
- Keep the real credential outside the guest and out of transport-visible
  request metadata; retain only opaque references and policy digests in Sandbox
  state. Do not assume the current native store can be passed through the
  existing `sb secrets` API without a new broker-only resolver boundary.
- Make every binding expiring, revocable, instance-scoped, and fail-closed when
  the egress policy, broker identity, or isolation proof is stale.
- Preserve and strengthen current isolation evidence: policy digests,
  namespaces, AppArmor/seccomp, capabilities, devices, cgroups, mounts,
  reachability, and default-deny networking.
- Adopt two lower-risk OpenSandbox patterns where they fit Sandbox: explicit
  lifecycle hooks with bounded failure behavior, and capability/proof reporting
  that distinguishes declared support from verified effective state.

## Non-Goals

- Replacing the existing `sb secrets` source broker, inspection policy, or
  machine-local at-rest store.
- Supporting Docker Compose, Herd, macOS, Kubernetes, or a general multi-tenant
  control plane in the first release; managed-native Ubuntu 24.04 is the only
  target.
- Generic transparent HTTPS MITM, caller-supplied arbitrary proxy code, or
  credential injection into guest environment variables, argv, files, snapshots,
  or command output.
- Implementing OpenSandbox-style pause/resume snapshots, node-agent pools, or
  hardware VM runtimes as part of this feature.
- Claiming a hardware isolation boundary or production readiness without live
  evidence on the supported host matrix.

## Product Scenarios

### Scenario 1 — Bound outbound API request

- **Starting state**: A managed-native instance has an enforced default-deny
  network policy and an operator-approved binding for one HTTPS service.
- **User action**: The workload makes a request matching the binding.
- **Expected outcome**: The request is relayed with the credential applied at
  the trusted boundary; the guest receives no real credential and the operation
  returns only bounded non-secret result data.

### Scenario 2 — Reject a near miss

- **Starting state**: A binding exists for one exact service, method, and path.
- **User action**: The workload changes the host, port, method, path, redirect,
  expiry, or credential reference.
- **Expected outcome**: The broker denies the request before upstream
  connection or credential use and records only a stable reason code.

### Scenario 3 — Revoke and recover

- **Starting state**: A binding is active and the instance or broker is restarted
  or the binding is revoked.
- **User action**: The operator revokes it, or the instance lifecycle restarts.
- **Expected outcome**: Existing and future use is stopped according to the
  configured policy; restart recovery rehydrates only from an opaque reference
  and re-verifies the effective isolation and egress proof before allowing use.
  Revocation cannot undo a request already accepted by the upstream service.

### Scenario 4 — Verify the boundary

- **Starting state**: A candidate managed-native instance is installed on the
  supported host matrix.
- **User action**: The operator runs preflight, status, and hostile workload
  probes.
- **Expected outcome**: The report distinguishes declared capability,
  prerequisite readiness, effective isolation, egress binding state, and any
  unproven or drifted gate; it never reports success from code presence alone.

## Proposed Product Behavior

- Bind one credential reference to one instance and an exact outbound request
  scope. A binding may be narrower than the network grant, never broader.
- Require default-deny egress, an instance-bound policy digest, an expiry, and a
  revocation state before credential use. Unknown, expired, stale, or ambiguous
  matches fail closed.
- A reviewed guest-side client uses a configured non-secret broker/API contract;
  arbitrary `curl`, Git, and SDK HTTPS traffic is out of scope for the MVP. The
  broker may apply only the approved authentication form (initially
  bearer/API-key headers; other forms require separate review). The guest never
  gets the real value through environment, argv, a mounted file, a snapshot, or
  retained output.
- Return bounded status and failure codes. Audit records contain operation,
  binding, instance, actor, decision, reason, expiry, and policy digests only.
- Restart and cleanup are idempotent. Secret material is loaded only for the
  broker process lifetime and best-effort zeroization/cleanup is performed when
  the binding is removed; Python/runtime memory does not provide a proof that
  every copy was cleared. Durable state stores references and digests, not
  values.
- Lifecycle hooks are limited to pre-start checks and bounded periodic health
  actions. A failing pre-start gate prevents workload entry; periodic failures
  are observable and do not silently weaken isolation.

## Constraints and Dependencies

- Existing `sb secrets` remains the only CLI/MCP source/reference policy
  boundary; the feature must not read secret files directly or add plaintext
  CLI/MCP inputs. Managed-native currently resolves from a separate
  `NativeCredentialStore`, so the plan must define a broker-only reference
  resolver/lease interface rather than wiring a caller-visible plaintext return
  path.
- Existing managed-native isolation is currently marked
  `implemented_unproven`/`adoptable: false` in its support matrix. This feature
  cannot promote that status without live Ubuntu 24.04 evidence.
- The current native egress broker supports digest-bound, expiring, hostname/IP
  grants and bounded TCP CONNECT but does not yet perform HTTP request binding;
  the plan must treat request parsing and TLS handling as a security-critical
  design decision, not a mechanical extension.
- The feature must preserve the Compose default and all unrelated dirty work.
- External OpenSandbox behavior is evidence, not authority; its documented
  Credential Vault, egress, secure-runtime, and lifecycle semantics can drift.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Scope | Managed-native Ubuntu 24.04 only for v1 | Keeps the new trust boundary within the existing native helper and proof model | Existing runtime policy + user request |
| Secret placement | Broker lifetime only; no guest env/argv/file | Matches the strongest OpenSandbox behavior without weakening current least disclosure | Existing secret/isolation policy |
| At-rest storage | Accept current owner-only plaintext native store as a separately tracked residual risk; do not broaden it | Prevents this plan from silently claiming encryption that does not exist | Sol review correction |
| Matching | Exact scheme/host/port/method/path; deny redirects unless separately bound | Prevents broad host grants from becoming credential grants | Security assumption for this plan |
| TLS | No generic transparent MITM in MVP; require a separate design gate for HTTPS request transformation | MITM adds CA distribution, SNI/ALPN, body-size, and logging risks | Sol review gate / explicit non-goal |
| Recovery | Rehydrate from opaque reference, then re-verify policy and broker proof | Avoids treating restart as authorization | Existing digest/CAS patterns |
| Adoption extras | Lifecycle hooks and capability/proof reporting only | High value with bounded blast radius; defer pools, snapshots, and multi-runtime support | Product prioritization assumption |

## Open Questions

- None for the planning baseline. HTTPS request transformation remains a
  separately gated design option, not an implicit implementation detail.

## Acceptance Outcomes

- In the managed-native acceptance harness, 100% of approved binding cases
  complete or return a bounded upstream error, while 100% of wrong-host,
  wrong-port, wrong-method, wrong-path, expired, revoked, redirect, and
  unknown-reference cases are denied before credential use.
- Hostile probes find zero real credential bytes in guest environment, argv,
  declared mounts, guest files, policy/registry/audit records, retained output,
  or process-control channels before the upstream request. Responses from an
  approved upstream are treated as untrusted; exact redaction is defense in
  depth and does not guarantee that transformed credential reflections cannot
  appear in returned data.
- Revocation prevents new use immediately and closes active broker sessions
  within the configured bounded deadline; restart recovery requires fresh
  effective-isolation and egress-proof verification. Upstream effects already
  accepted before revocation are reported as non-reversible.
- Existing managed-native verification gates remain green, and any missing
  prerequisite or drift is reported as blocked/unproven rather than success.
- Existing `sb secrets` inspection, use-profile, audit, and non-native runtime
  behavior pass their regression suite unchanged.

## Risks and Assumptions

- **Risk**: An HTTP/TLS parsing or proxying bug could turn a narrow credential
  binding into secret exfiltration or policy bypass.
- **Risk**: The privileged native helper and broker become a higher-value target;
  ownership, digest, service sandboxing, and restart cleanup must be proven.
- **Risk**: Native credential bytes are currently plaintext owner-only files at
  rest. This remains an explicit residual-risk gate; it is not equivalent to
  OpenSandbox's process-memory-only vault and may block production adoption.
- **Risk**: Existing native support is not yet adoptable; implementation without
  live proof would create a false security claim.
- **Risk**: Credential use may be replayed or redirected if binding semantics are
  broader than the network grant; exact matching and redirect denial are required.
- **Assumption**: The first consumer can use a documented application-layer
  broker/request contract rather than arbitrary applications requiring
  transparent interception.
- **Assumption**: Existing machine-local secret references are acceptable as the
  source of broker material for v1; redesign of at-rest encryption is separate.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed or explicitly recorded as planning assumptions.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: READY FOR SPECKIT

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
