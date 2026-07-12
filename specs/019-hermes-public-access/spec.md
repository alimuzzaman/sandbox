# Feature Specification: Hermes Public Dashboard Access

**Feature Branch**: `019-hermes-public-access`
**Created**: 2026-07-12
**Status**: Draft
**Input**: User description: "Expose the existing Hermes dashboard at `hermes.asb.bd` without an SSH tunnel, using authenticated Cloudflare-based access, optional Basic Auth, a loopback-only dashboard, a plan/confirm workflow, and verified rollback."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Plan Protected Public Access (Priority: P1)

The Sandbox operator can inspect exactly what would be required to make the existing
Hermes dashboard available at `hermes.asb.bd`, without making any live changes.

**Why this priority**: A privileged dashboard must not be exposed until the operator
can review ownership, authentication, dependencies, and rollback impact.

**Independent Test**: With a healthy loopback dashboard and no configured public route,
the operator requests a plan and receives the proposed hostname, access policy,
connector, proxy, health checks, conflicts, and rollback actions while remote and
Cloudflare state remain unchanged.

**Acceptance Scenarios**:

1. **Given** the Hermes operational acceptance gate is current, **When** the operator
   requests a public-access plan for `hermes.asb.bd`, **Then** the system produces a
   read-only, sanitized plan with the required preconditions and rollback actions.
2. **Given** the Hermes acceptance gate is stale, incomplete, or tied to a different
   revision, **When** the operator requests a plan or apply, **Then** the system refuses
   to expose the dashboard and makes no external change.
3. **Given** an unmanaged conflicting hostname, route, or policy exists, **When** the
   operator requests a plan, **Then** the system reports the conflict and does not claim
   ownership or overwrite it.

---

### User Story 2 - Publish an Authenticated Dashboard (Priority: P2)

After reviewing a plan and explicitly confirming it, the Sandbox operator can reach
the dashboard at `https://hermes.asb.bd` only after passing a narrow, multi-factor
identity policy. The dashboard itself remains private to the remote host.

**Why this priority**: This delivers the requested browser access while preserving the
existing isolation and authentication boundary.

**Independent Test**: In an approved disposable or production-equivalent environment,
an anonymous request is rejected before it reaches Hermes, the authorized identity can
load the dashboard and interactive session, and the dashboard has no public listener.

**Acceptance Scenarios**:

1. **Given** a reviewed plan, valid prerequisites, and explicit confirmation, **When**
   the operator publishes the route, **Then** the declared hostname is protected by a
   deny-by-default identity policy before requests reach the dashboard.
2. **Given** an anonymous or unauthorized browser, **When** it visits the public
   hostname, **Then** access is denied and Hermes does not receive an application
   request.
3. **Given** the exact authorized operator completes the required multi-factor check,
   **When** they visit the public hostname, **Then** the dashboard, navigation, chat,
   streamed output, and interactive terminal session work.
4. **Given** the public route is active, **When** the dashboard service is inspected,
   **Then** it remains reachable only through its loopback listener and the existing
   SSH-forwarded path still works.

---

### User Story 3 - Add or Rotate a Secondary Access Secret (Priority: P3)

The operator can optionally add, rotate, or remove a second browser credential without
weakening the primary identity policy or recreating unrelated public-access resources.

**Why this priority**: A separately revocable credential can provide defense in depth,
but it must not turn a shared secret into the primary authorization mechanism.

**Independent Test**: With secondary access enabled, an identity-authorized request
without the secondary credential is rejected; after a credential rotation, the old
credential fails and the new one works while the public route and primary identity
policy remain unchanged.

**Acceptance Scenarios**:

1. **Given** secondary access is disabled, **When** the operator publishes the route,
   **Then** the primary identity policy alone controls browser admission.
2. **Given** secondary access is enabled, **When** an identity-authorized browser omits
   or supplies an invalid secondary credential, **Then** the request is rejected before
   Hermes receives it.
3. **Given** a secondary credential is rotated or removed, **When** the operation
   completes, **Then** plaintext is not retained or displayed and public-access
   ownership, primary policy, and connector state are preserved.

---

### User Story 4 - Recover Safely from Exposure Failure (Priority: P4)

The operator can inspect health, remove public access, and recover from a failed
publication without losing Hermes CLI, gateway, repositories, backups, or SSH access.

**Why this priority**: A privileged service must fail closed and leave a known recovery
path.

**Independent Test**: Simulate failures at proxy validation, connector startup, policy
verification, public-route creation, and authenticated health; each leaves no anonymous
public endpoint and retains loopback/SSH recovery.

**Acceptance Scenarios**:

1. **Given** a failure during publication, **When** automatic recovery runs, **Then**
   integration-owned changes are restored in a safe order and anonymous public access is
   not possible.
2. **Given** an active public route, **When** the operator plans and confirms removal,
   **Then** only integration-owned public-access resources are removed and SSH-forwarded
   dashboard access remains available.
3. **Given** a degraded route, **When** the operator runs diagnostics, **Then** the
   result distinguishes dashboard, local proxy, identity policy, connector, hostname,
   and recovery state without exposing credentials or personal session data.

### Edge Cases

- The public hostname is already owned by an unmanaged route, connector, or identity
  policy.
- The configured identity policy is broad, missing multi-factor requirements, inactive,
  or does not exactly cover the requested hostname.
- The dashboard acceptance evidence, Hermes revision, or Sandbox integration schema is
  stale after an update.
- The remote dashboard is running but not loopback-only, the local proxy port is busy,
  or the connector targets an unexpected service.
- A connector credential, API credential, or secondary password is missing, malformed,
  revoked, or appears in a command argument or result.
- A browser reaches the public hostname during a partial apply, rollback, connector
  restart, policy change, or DNS propagation delay.
- Browser interactive-session traffic fails while ordinary page loads succeed.
- The existing SSH-forwarded path is unavailable after an attempted public-route change.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST manage public dashboard access only for an explicit
  configured remote and explicit fully qualified hostname.
- **FR-002**: The first supported public hostname MUST be `hermes.asb.bd`.
- **FR-003**: The system MUST require current, revision-compatible Hermes operational
  acceptance evidence before planning, publishing, changing, or removing public access.
- **FR-004**: The system MUST provide a read-only plan before every public-access
  mutation and require an explicit confirmation for publish, rotation, removal, and
  destructive recovery actions.
- **FR-005**: The public route MUST require a deny-by-default identity policy that
  permits only explicitly designated operator identities and requires multi-factor
  authentication.
- **FR-006**: The public route MUST reject anonymous and unauthorized requests before
  they reach the Hermes dashboard.
- **FR-007**: The dashboard MUST remain bound to a loopback-only listener; the feature
  MUST NOT add an insecure mode or a direct public dashboard listener.
- **FR-008**: The public transport MUST avoid opening an inbound Hermes origin route and
  must be limited to the exact declared hostname and intended local dashboard target.
- **FR-009**: The system MUST preserve SSH-forwarded dashboard access as a recovery path
  before, during safe staging, and after public-route changes.
- **FR-010**: The system MUST optionally support a secondary Basic Auth credential after
  the primary identity policy, while keeping it disabled by default.
- **FR-011**: The system MUST never place plaintext passwords, authentication tokens,
  connector credentials, cookies, private keys, or complete identity claims in source
  control, arguments, persisted public state, logs, or result envelopes.
- **FR-012**: The system MUST report the ownership, health, and drift state of the
  dashboard, local proxy, public connector, hostname, and identity policy separately.
- **FR-013**: The system MUST update or remove only resources it can prove it owns and
  MUST fail with an actionable conflict for unmanaged resources.
- **FR-014**: The system MUST preserve enough non-secret state to restore the prior
  integration-owned route, service, and hostname configuration after a failed mutation.
- **FR-015**: A failed publish, rotation, removal, or health check MUST leave no
  unauthenticated public dashboard endpoint and MUST retain Hermes CLI, gateway,
  repositories, backups, and SSH access.
- **FR-016**: The authenticated public route MUST support dashboard navigation, API
  requests, redirects, streaming output, and interactive terminal traffic.
- **FR-017**: The system MUST reject wildcard hostnames, broad identity policies,
  wildcard connector targets, missing access protection, and missing required secret
  references before public mutation.
- **FR-018**: The system MUST make public-route lifecycle commands available only to an
  explicit operator CLI workflow; Sandbox MCP may report status but MUST NOT publish,
  remove, or provision public access.
- **FR-019**: Documentation MUST state the trust boundary, supported recovery path,
  secondary-credential limitations, emergency containment steps, and the need for
  separate operator approval before live changes.

### Key Entities *(include if feature involves data)*

- **Public exposure**: The desired and observed state for one dashboard hostname,
  including mode, revision evidence, health, ownership references, and rollback data.
- **Identity policy reference**: A non-secret reference to the exact protected hostname,
  allowed identity set, multi-factor requirement, session policy, and ownership state.
- **Connector route reference**: A non-secret reference to the public connector, exact
  hostname, expected local target, and connector health.
- **Secondary credential configuration**: An optional username and non-plaintext
  credential verifier, with enabled/disabled state and rotation metadata.
- **Rollback record**: The immutable non-secret snapshot of integration-owned resources
  and service states needed for one safe recovery attempt.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A public-access plan completes without changing remote, hostname, policy,
  connector, or dashboard state in 100% of automated plan tests.
- **SC-002**: 100% of publication attempts without current acceptance evidence or
  explicit confirmation are rejected before an external mutation is attempted.
- **SC-003**: 100% of anonymous and unauthorized public-route test requests are denied
  before reaching Hermes, while the authorized multi-factor identity can complete the
  dashboard’s core navigation and interactive-session workflow.
- **SC-004**: 100% of injected failures at the supported publication stages leave no
  anonymous public endpoint and preserve the documented SSH-forwarded recovery path.
- **SC-005**: The system distinguishes the health of every public-access layer in one
  diagnostic result within 15 seconds on a reachable remote.
- **SC-006**: No automated test, diagnostic, or result envelope exposes a plaintext
  credential, token, cookie, private key, or full identity claim.
- **SC-007**: An operator can remove an integration-owned public route with explicit
  confirmation while retaining a healthy loopback dashboard and SSH recovery path.

## Assumptions

- The first and only supported audience is a single trusted Sandbox operator.
- `scaleway-sandbox` remains a provisioned Linux remote with a healthy current Hermes
  dashboard, system service support, and existing SSH access.
- The operator owns `asb.bd` in a Cloudflare account that can provide a narrow identity
  policy and an outbound connector without exposing the Hermes origin publicly.
- Exact operator identity/group values, connector credentials, and API credentials are
  supplied through approved secret handling at live-apply time and are not required for
  local specification, unit tests, or implementation verification.
- The initial release uses Cloudflare Access as the primary browser authentication
  boundary; upstream Hermes hosted OAuth and multi-operator access are out of scope.
- The initial release attaches to pre-created exact Cloudflare Access, Tunnel, and DNS
  resources and validates their configuration. It does not create, modify, or delete
  Cloudflare identity policies, tunnel routes, or DNS records.
- Secondary Basic Auth is optional and disabled unless an operator explicitly enables
  it using an approved secret reference.
- No live public-access change is authorized by this specification or implementation;
  each live mutation requires a separately reviewed plan and current explicit approval.
