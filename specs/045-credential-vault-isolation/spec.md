# Feature Specification: Managed Credential Vault and Isolation Evidence

**Feature Branch**: `latest`

**Created**: 2026-08-25

**Status**: Draft

**Input**: Product requirements draft from `prd.md`, informed by the reviewed OpenSandbox Credential Vault and isolation documentation.

## Clarifications

### Session 2026-08-27

- Q: Does the prohibition on credential bytes in control/process-control
  channels include the trusted one-use control-plane-to-broker lease channel
  already required by the data model? → A: No. The prohibition covers every
  guest-visible, root-helper, supervisor, durable, status, audit, and retained
  channel. One ephemeral, authenticated, one-use lease channel wholly inside
  the trusted control-plane-to-broker boundary may carry credential material
  transiently. That channel must be unreachable and unobservable from the
  workload and sibling instances, must not involve the root helper or service
  supervisor, and must fail closed without replay or durable recovery.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Bind an approved credential to one outbound operation (Priority: P1)

As a Sandbox operator, I want to authorize one managed-native instance to call one exact external service operation with one approved credential reference, so that the workload cannot widen the authorization by changing its destination or request shape.

**Why this priority**: The binding is the core safety boundary. Without exact scope, credential mediation would turn a network grant into an unbounded secret relay.

**Independent Test**: Create a binding on a proof-qualified managed-native instance and exercise matching and near-miss requests. Matching requests are authorized; every destination, method, path, expiry, reference, or redirect mismatch is denied before credential use.

**Acceptance Scenarios**:

1. **Given** a proof-qualified managed-native instance with default-deny egress and an active binding for one HTTPS scheme, host, port, method, and path, **When** the workload submits that exact request, **Then** the operation is authorized with the approved authentication form and the workload receives only bounded non-secret result data.
2. **Given** the same binding, **When** the workload changes the scheme, host, port, method, path, credential reference, or redirect target, **Then** the operation is denied before the upstream connection or credential is used and a stable non-secret reason code is recorded.
3. **Given** two active bindings whose scopes overlap ambiguously, **When** the workload submits a request matching both, **Then** the request is denied rather than selecting a binding nondeterministically.

### User Story 2 - Use an approved service without receiving the credential (Priority: P1)

As a Sandbox workload author, I want to call an approved service through a documented request contract without receiving the real credential in my environment, arguments, files, snapshots, logs, or retained output.

**Why this priority**: The feature only adds value over the existing trusted-child and guest-readable-file paths if the hostile workload remains outside the credential trust boundary.

**Independent Test**: Run a reviewed client and hostile probes inside the same managed-native instance, then inspect the allowed exposure surfaces and the bounded broker response.

**Acceptance Scenarios**:

1. **Given** an active binding and a reviewed client contract, **When** the workload sends a valid request, **Then** the trusted boundary applies the credential and no real credential bytes are present in guest environment, argv, declared mounts, guest files, snapshots, policy records, audit records, guest-visible/helper/supervisor control channels, or retained output.
2. **Given** a malformed request, unsupported authentication form, oversized request or response, invalid certificate, unsupported redirect, or upstream timeout, **When** the workload submits it, **Then** the broker returns a bounded error code without disclosing credential material.
3. **Given** an approved upstream that returns data containing an authorization value or a transformed representation of it, **When** the response is returned, **Then** the system treats the upstream as an authorized but untrusted recipient, applies best-effort redaction where possible, and does not claim that response filtering proves confinement.

### User Story 3 - Revoke, expire, and recover a binding safely (Priority: P1)

As a Sandbox operator, I want expiry, revocation, restart, and cleanup to fail closed and to be observable, so that a stale process or stale policy cannot continue using a credential.

**Why this priority**: Lifecycle transitions are where in-memory credential systems commonly lose authorization state or accidentally reopen old access.

**Independent Test**: Exercise active use, expiry, explicit revoke, broker restart, machine restart, and cleanup while observing state, new-request refusal, active-session closure, and re-verification behavior.

**Acceptance Scenarios**:

1. **Given** an active binding, **When** the operator revokes it, **Then** new requests are refused immediately, active broker sessions close within the configured bound, and the binding cannot be recreated from stale state without a fresh authorization.
2. **Given** an expired binding, **When** the workload requests the bound operation, **Then** the request is denied before credential resolution and the state reports expiry without exposing the reference value.
3. **Given** a broker or managed-native instance restart, **When** the desired binding metadata is reloaded, **Then** the instance enters `credential_pending`, remains unusable for credential-bound requests, and transitions to ready only after the opaque reference is resolved and policy, broker, egress, and effective-isolation proofs match the desired digests.
4. **Given** a request already accepted by the upstream before revocation, **When** revocation completes, **Then** the report states that local future use was stopped but does not claim that the upstream effect was undone.

### User Story 4 - Verify capability, proof, and lifecycle state (Priority: P2)

As a security reviewer, I want status and lifecycle reports to distinguish declared support from effective verified state, so that code presence or a stale manifest cannot be mistaken for an isolation guarantee.

**Why this priority**: Managed-native is currently implemented but unproven. Honest capability reporting is required before enabling a credential boundary.

**Independent Test**: Run preflight and status checks on a supported host, on a host with a missing prerequisite, and after policy or broker drift. Compare each report with the observed state and verify that unproven or drifted states block credential use.

**Acceptance Scenarios**:

1. **Given** a host that has not passed the required live hostile, grant, revoke, exhaustion, warm-start, and cleanup evidence, **When** an operator requests credential mediation, **Then** the capability is reported as unproven and the feature is refused.
2. **Given** a proof-qualified host with matching policy, egress, broker, and binding digests, **When** the operator requests status, **Then** the report exposes only non-secret references, digests, lifecycle state, expiry, and bounded reason codes.
3. **Given** a stale or missing prerequisite during pre-start or periodic health evaluation, **When** the lifecycle hook runs, **Then** pre-start blocks workload entry and periodic failure is visible without weakening default-deny isolation.

### Edge Cases

- Unknown, malformed, duplicate, or ambiguous binding fields are rejected without attempting secret resolution.
- Redirects are denied unless the destination is separately and exactly bound; DNS changes outside the pinned authorization are denied.
- Unsupported methods, content types, protocol versions, duplicate security-sensitive headers, and hop-by-hop headers are rejected or normalized according to the documented request contract.
- Request and response limits, connection deadlines, cancellation, and concurrent-use limits return stable bounded errors rather than partial credential output.
- A failed audit outcome after an effect has occurred is reported as indeterminate; the system does not automatically replay a credential-bearing request.
- A stale, duplicated, malformed, wrong-process, wrong-instance, wrong-binding,
  expired, or unacknowledged trusted lease is consumed or refused terminally;
  the credential-bearing transfer and upstream request are never retried.
- Native credential bytes may remain in an owner-only host store for this release; this residual at-rest risk is not represented as process-memory-only vault protection.
- Compose, Herd, macOS, Kubernetes, ordinary remote workspaces, and unqualified durable jobs refuse this capability rather than silently falling back to a weaker runtime.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support credential mediation only for a managed-native Ubuntu 24.04 instance that has passed the required live isolation and egress proof gate.
- **FR-002**: The system MUST bind each credential authorization to exactly one instance, one opaque credential reference, one policy digest, one expiry, and one exact scheme, host, port, method, and path scope.
- **FR-003**: The system MUST reject a binding whose scope is broader than the corresponding default-deny network authorization or whose fields are missing, malformed, stale, or ambiguous.
- **FR-004**: The system MUST resolve credential material only inside the trusted control-plane-to-broker boundary and MUST NOT expose a public plaintext-return operation, arbitrary path resolver, or caller-supplied secret value.
- **FR-005**: The system MUST apply only the registered authentication profile explicitly allowed by the binding; the first release MUST support the fixed `authorization_bearer` and `x_api_key` profiles and MUST reject guest-supplied or unsupported header forms.
- **FR-006**: The system MUST provide a documented explicit application-layer request contract for the first consumer; arbitrary transparent `curl`, Git, package-manager, and SDK interception is not required.
- **FR-007**: The system MUST validate destination identity, certificate validity, request scope, redirect behavior, request/response bounds, and deadlines before or during upstream use according to the contract.
- **FR-008**: The system MUST keep the real credential out of workload environment, argv, guest-readable files, snapshots, policy and registry records, audit records, guest-visible/root-helper/supervisor/durable control channels, status, and retained output. One ephemeral, authenticated, one-use control-plane-to-broker lease channel wholly inside the trusted boundary MAY carry credential material transiently; it MUST be unreachable from the workload and sibling instances, MUST NOT involve the root helper or service supervisor, and MUST fail closed without replay or durable recovery.
- **FR-009**: The system MUST treat approved upstream services as credential recipients but untrusted response sources; response redaction MUST be defense in depth and MUST NOT be presented as proof of universal confinement.
- **FR-010**: The system MUST record only opaque references, policy and binding digests, lifecycle state, actor, operation, decision, expiry, and stable reason codes in durable state and bounded audit output.
- **FR-011**: The system MUST refuse new use immediately after expiry or revocation and MUST close active broker sessions within a documented bounded deadline.
- **FR-012**: The system MUST persist desired references and digests rather than credential values, enter `credential_pending` after broker or instance restart, and require fresh effective-state verification before returning to ready.
- **FR-013**: The system MUST perform idempotent cleanup and best-effort memory/material cleanup at broker replacement or binding removal without claiming universal memory zeroization.
- **FR-014**: The system MUST distinguish declared capability, prerequisite readiness, effective proof, binding state, and drift in operator-facing reports.
- **FR-015**: The system MUST block pre-start when an isolation, policy, egress, broker, source, or proof gate is missing or stale; periodic health failures MUST be observable and MUST NOT weaken isolation.
- **FR-016**: The system MUST refuse credential mediation for unsupported runtimes rather than falling back to Compose, ordinary host jobs, or guest-readable credential injection.
- **FR-017**: The system MUST preserve existing `sb secrets` trusted-child behavior, native guest-readable credential behavior, non-native runtime behavior, and default-deny managed-native egress semantics outside this feature.
- **FR-018**: The system MUST document the single-host, single-control-plane ownership model and MUST NOT represent local file locking or workspace identity as multi-tenant isolation or high availability.

### Initial Operational Limits

The first release MUST use these upper bounds unless a separately reviewed
contract version lowers them: 64 KiB total request headers, 1 MiB request body,
4 MiB response body, 16 concurrent requests per broker, 5 seconds to connect,
30 seconds total request time, and 5 seconds of inactivity. A caller may request
lower limits but may not raise them through the guest contract.

### Key Entities

- **Credential Binding**: An immutable, instance-scoped authorization containing the opaque reference, exact request scope, authentication form, policy/egress/broker digests, expiry, state, owner, and version.
- **Secret Reference**: A registered or otherwise approved opaque identifier for a credential source; it is never a durable plaintext value.
- **Broker Lease**: A short-lived, process-bound authorization allowing one broker instance to use one resolved credential for a bounded operation window.
- **Broker Request**: A normalized application-layer request with destination, method, path, bounded headers/body, binding version, and correlation identifier.
- **Capability Proof**: A report separating declared runtime support, prerequisite readiness, effective isolation observations, egress/broker state, and evidence identity.
- **Lifecycle Record**: An audit-safe record of binding creation, use decision, expiry, revoke, restart recovery, cleanup, and indeterminate outcomes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the supported acceptance host, 100% of matching requests in the documented test matrix either complete with a bounded response or return a bounded upstream error, and 100% of wrong-destination, wrong-method, wrong-path, expired, revoked, redirect, unknown-reference, and ambiguous-binding cases are denied before credential use.
- **SC-002**: For every accepted test run, hostile probes find zero real credential bytes in the guest environment, argv, declared mounts, guest files, snapshots, policy/registry/audit/status records, guest-visible/root-helper/supervisor/durable process-control channels, or retained output. The only permitted transient carrier is the authenticated one-use lease channel inside the trusted control-plane-to-broker boundary, and probes MUST verify that the guest, sibling instances, helper, and supervisor cannot discover, read, connect to, inherit, or replay it.
- **SC-003**: Revocation prevents every new request after the revocation decision and closes every active broker session within the documented deadline in 100% of acceptance runs.
- **SC-004**: Every restart recovery begins in `credential_pending`, and 100% of recovery attempts with stale or mismatched policy, broker, egress, source, or isolation proof remain blocked.
- **SC-005**: Capability reports identify the evidence identity and distinguish proven, unproven, blocked, and drifted states in 100% of preflight and status cases; no unproven host can enable the feature.
- **SC-006**: Existing secret-source, trusted-child use, native guest-readable injection, non-native runtime, and default-deny egress regression suites pass without behavior changes outside the feature’s explicit interfaces.
- **SC-007**: A first-time operator can follow the documented request-contract and proof-gate quickstart to determine whether the feature is usable, blocked, or unproven without receiving or entering a plaintext credential through a public CLI/MCP/API surface.

## Assumptions

- The first consumer can use the explicit broker request contract; transparent interception is a separate future design review.
- The existing machine-local approved reference source is acceptable for v1, with its owner-only plaintext-at-rest limitation recorded as a residual risk.
- The supported host matrix is Ubuntu 24.04 with the already documented managed-native prerequisites and live evidence; other runtimes remain explicit refusals.
- One local control plane owns each instance; this feature does not introduce a multi-tenant authorization service, distributed consensus, or HA store.
- Credential destinations are selected as trusted recipients that should not reflect or transform authorization values into response data; response redaction remains defense in depth.
- Existing default-deny networking, digest/CAS conventions, fixed helper verbs, and effective-state verification remain the governing isolation mechanisms.
