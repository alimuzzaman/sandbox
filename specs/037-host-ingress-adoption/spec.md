# Feature Specification: Host Ingress Adoption

**Feature Branch**: `latest`

**Created**: 2026-08-01

**Status**: Draft

**Input**: Ready product requirements from `specs/037-host-ingress-adoption/prd.md`

## Clarifications

### Session 2026-08-02

- Q: Which ingress is the product default once this feature ships? → A: Sandbox's own
  Docker/Caddy proxy, on every supported platform and for every runtime, unchanged from its
  pre-adoption behavior.
- Q: Is the default Sandbox Caddy path gated by the adapter support/live-proof tiers used
  for incumbent adoption? → A: No. Proof tiers gate incumbent adoption only; the default
  path stays available even when no adapter is proven.
- Q: How does a user get incumbent adoption instead? → A: Explicit opt-in, selectable during
  setup and switchable on demand afterwards, per project or per machine.
- Q: Does this feature replace or remove the existing clean-URL path? → A: No. The existing
  path, including its privileged bootstrap, remains the working default; removal requires
  live parity plus explicit human approval per constitution principle VI.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Detect Real Port Ownership Before Acting (Priority: P1)

A developer requests a clean local URL on a machine where HTTP or HTTPS endpoints may
already be owned by another service. Sandbox identifies each actual conflicting bind
endpoint, distinguishes its own proxy from foreign owners, and never reports an unrelated
Docker failure or steals a port.

**Why this priority**: Safe detection is the gate for every fallback and adoption path;
getting bind scope wrong can either break another service or disable a valid clean URL.

**Independent Test**: Exercise free, Sandbox-owned, loopback-specific, wildcard-address,
single-protocol, split-owner, identifiable, and unidentified listeners and compare all
listener/process state before and after detection.

**Acceptance Scenarios**:

1. **Given** a service bound only to `127.0.0.1:80`, **When** Sandbox evaluates its own
   distinct loopback endpoint, **Then** it reports coexistence unless bind scopes actually
   overlap.
2. **Given** a service bound to all addresses on a required port, **When** clean URL setup
   runs, **Then** Sandbox reports the conflicting endpoint and owner where observable and
   does not start its proxy on that endpoint.
3. **Given** Sandbox's proxy already owns the required endpoints, **When** another instance
   is ensured, **Then** Sandbox recognizes its own owner and reuses it without adoption
   consent.
4. **Given** different products own HTTP and HTTPS required for one hostname, **When** a
   clean URL is requested, **Then** Sandbox does not split that hostname across ingresses
   and uses the per-port fallback unless one selected ingress can serve every promised
   protocol.

---

### User Story 2 - Adopt a Supported Incumbent Safely (Priority: P1)

A developer whose machine already runs a supported web ingress can opt in to adding a
Sandbox hostname through that incumbent while all pre-existing routes remain healthy.

**Why this priority**: This is the primary value of the opt-in mode: clean URLs coexist with
the host's established routing layer when the developer chooses it or when a foreign
listener owns the endpoints the default Sandbox Caddy ingress needs.

**Independent Test**: For each advertised adoptable product, add and update one route,
make a live request through it, validate incumbent routes before and after, then remove the
Sandbox route.

**Acceptance Scenarios**:

1. **Given** a supported incumbent owns every endpoint required by the requested URL and
   prior consent exists, **When** B supplies a resolved hostname, **Then** Sandbox adds an
   attributable route through that incumbent and does not run its own proxy.
2. **Given** the instance backend endpoint changes, **When** ensure runs again, **Then**
   only the unchanged owned route target is updated and all foreign routes are preserved.
3. **Given** an incumbent configuration is invalid before mutation, **When** adoption is
   requested, **Then** Sandbox makes no route change and returns the actual validation
   failure with the per-port URL.
4. **Given** candidate validation, reload, or post-apply health fails, **When** Sandbox
   attempts adoption, **Then** it restores the prior state and verifies previously healthy
   incumbent routes remain healthy.

---

### User Story 3 - Preserve Ownership Through Cleanup and Drift (Priority: P2)

A host owner can destroy an instance or uninstall Sandbox knowing that only unchanged
Sandbox-owned routes are removed and unresolved cleanup remains visible and retryable.

**Why this priority**: Route mutation touches infrastructure shared by unrelated projects;
reversibility is required for safe adoption.

**Independent Test**: Remove unchanged routes, edit an owned route externally, stop an
incumbent, and repeat destroy/uninstall while inspecting routes and recovery state.

**Acceptance Scenarios**:

1. **Given** an unchanged owned route in an available incumbent, **When** the instance is
   destroyed, **Then** only that route is removed and the incumbent remains healthy.
2. **Given** a marked route whose target or properties changed externally, **When** ensure,
   destroy, or uninstall runs, **Then** Sandbox leaves it untouched and reports drift.
3. **Given** the incumbent is unavailable during cleanup, **When** destroy or uninstall
   runs, **Then** cleanup is reported incomplete and a minimal non-secret recovery record
   remains for retry.
4. **Given** cleanup is repeated, **When** no owned route remains, **Then** the operation is
   successful and does not affect foreign state.

---

### User Story 4 - Understand Support, Consent, and Fallback (Priority: P2)

A developer can see whether an incumbent is adoptable, conditionally adoptable,
credential-pending, detect-only, outside-platform, or unidentified, and can grant or
decline first-use consent without blocking automation.

**Why this priority**: Accurate capability reporting prevents “detected” from being
mistaken for “safe to automate” and keeps MCP/CI non-interactive.

**Independent Test**: List support tiers, exercise interactive accept/decline, repeat a
decline, call from a non-interactive process, and pin conflicting project/machine choices.

**Acceptance Scenarios**:

1. **Given** first adoption of a user-owned incumbent from an interactive terminal,
   **When** the developer accepts, **Then** consent is recorded for that incumbent/machine
   before mutation; a decline is remembered.
2. **Given** no recorded consent or credentials in a non-interactive caller, **When** clean
   URL setup runs, **Then** it returns pending consent/credentials without prompting or
   mutating and preserves the per-port URL.
3. **Given** a detect-only product, **When** clean URL setup runs, **Then** Sandbox names
   the product and limitation and does not use a private interface or internal state.
4. **Given** project and machine-local pins disagree, **When** selection runs, **Then** the
   machine-local override wins and status reports its source.

### Edge Cases

- Only HTTPS conflicts while the requested URL is HTTP-only, or the reverse; only required
  protocol capabilities affect selection, while every foreign listener is preserved.
- An explicit hostname is incompatible with the selected incumbent; Sandbox preserves the
  identity and falls back rather than renaming it.
- A recognized process exposes insufficient evidence to prove its exact product/version;
  it remains unidentified or detect-only.
- A supported product is installed but its documented control surface is disabled,
  unauthenticated, unwritable, or unavailable; its effective tier degrades without mutation.
- A hostname already belongs to a foreign route, including a more general wildcard route;
  Sandbox refuses to shadow or overwrite it.
- B resolves the hostname to an address not accepted by the selected ingress; A does not
  activate or advertise the route.
- The selected incumbent disappears after successful adoption; status marks the clean URL
  unhealthy and offers only currently valid recovery paths.
- An incumbent supports HTTP but not requested TLS or wildcard hostnames; status reports
  the exact missing capability and never returns a broken URL.
- An adoptable incumbent is present and proven but no adoption was selected; the default
  Sandbox Caddy ingress is used and adoption is offered, not applied.
- No incumbent adapter has recorded live proof on this platform; the default Sandbox Caddy
  ingress still serves clean URLs and only adoption is reported unavailable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST inspect ownership and bind scope separately for every HTTP
  and HTTPS endpoint relevant to the requested clean URL before any mutation.
- **FR-002**: Detection MUST distinguish free, Sandbox-owned, adoptable, conditionally
  adoptable, credential-pending, detect-only, outside-platform, and unidentified states.
- **FR-003**: Detection MUST be read-only and MUST NOT stop, reload, reconfigure, or bind
  any service.
- **FR-004**: Detection MUST account for exact-address and wildcard-address overlap rather
  than treating every listener on the same numeric port as a conflict.
- **FR-005**: A hostname MUST have one authoritative ingress for every protocol Sandbox
  advertises for it; the system MUST NOT split one hostname across different products.
- **FR-006**: A foreign listener on an unrequested protocol MUST be preserved and reported
  but MUST NOT justify taking or rewriting it.
- **FR-007**: Sandbox's own Caddy proxy MUST be the default ingress on every supported
  platform and for every runtime, taking the required HTTP and HTTPS endpoints whenever they
  are free or already Sandbox-owned. This default MUST NOT depend on any incumbent adapter
  reaching an adoptable support tier.
- **FR-008**: Before route activation, A MUST supply B with acceptable listener addresses
  and hostname/TLS capabilities; A MUST activate only a hostname B has resolved to an
  accepted address.
- **FR-009**: C MUST supply backend runtime endpoints or document-root requirements; A MUST
  exclusively own hostname-route lifecycle, including Herd/Valet link/proxy/TLS actions.
- **FR-010**: Each advertised adoptable incumbent MUST use only a documented control
  surface and MUST pass live add, update, health, collision, and cleanup proof.
- **FR-011**: The initial coverage tiers MUST match the ready PRD matrix; detect-only or
  outside-platform products MUST NOT be advertised as adoptable.
- **FR-012**: A route MUST be attributable to Sandbox and one instance without relying on
  hostname alone.
- **FR-013**: Sandbox MUST refuse any hostname collision with a route it cannot prove it
  owns and leave the foreign route unchanged.
- **FR-014**: Sandbox MUST update or remove an owned route only when observed state matches
  its last applied state.
- **FR-015**: Route add, update, remove, detection, and status MUST be idempotent.
- **FR-016**: Configuration-file incumbents MUST have their complete current state
  validated before mutation and candidate state validated before activation.
- **FR-017**: A failed candidate validation, reload, or post-apply health check MUST restore
  prior state and return the per-port URL.
- **FR-018**: Adoption MUST preserve the health and routing behavior of every previously
  healthy incumbent route.
- **FR-019**: First mutation of each user-owned incumbent on a machine MUST require recorded
  interactive consent.
- **FR-020**: Non-interactive callers without consent or required credentials MUST NOT
  prompt, block, or mutate and MUST return a pending result.
- **FR-021**: A remembered decline MUST suppress repeated offers until an explicit user
  action reconsiders it.
- **FR-022**: Secrets used by a documented authenticated adapter MUST remain in gitignored
  machine-local secret storage and MUST NOT appear in output, logs, recovery state, or
  tracked files.
- **FR-023**: An explicit ingress pin MUST beat detection; a machine-local project override
  MUST beat a committed project pin.
- **FR-024**: A missing or unusable pin MUST be reported and MUST NOT silently choose a
  different ingress.
- **FR-025**: Status MUST report effective ingress and pin source, support tier, endpoint
  ownership, promised protocols/capabilities, route ownership/drift, clean-URL health, and
  per-port fallback reason.
- **FR-026**: Adoption failure MUST NOT block otherwise successful instance provisioning or
  access through the per-port URL.
- **FR-027**: Destroy and uninstall MUST remove every unchanged owned route reachable in an
  available incumbent and MUST preserve all foreign configuration.
- **FR-028**: Unavailable, drifted, or rejected cleanup MUST return incomplete cleanup,
  identify the residual, and retain minimum non-secret retry state.
- **FR-029**: The system MUST never attribute a confirmed port conflict to Docker being
  unavailable.
- **FR-030**: A user MUST be able to list the products this build can detect/adopt, their
  tiers, required capabilities, consent, and credential prerequisites.
- **FR-031**: Incumbent adoption MUST be opt-in. With no explicit selection, the system MUST
  use the default Sandbox Caddy ingress even when an adoptable incumbent is detected and
  proven.
- **FR-032**: A user MUST be able to select the ingress mode during setup and switch it on
  demand afterwards, at project and machine-local scope, without destroying or reprovisioning
  the instance.
- **FR-033**: The existing Sandbox Caddy clean-URL path, including the privileged bootstrap
  it needs to serve `http(s)://<hostname>` without a port, MUST keep working unchanged while
  this feature ships. Disabling, bypassing, or removing it requires recorded live parity of
  the replacement plus explicit human approval.
- **FR-034**: When the default ingress cannot take a required endpoint because a foreign
  listener owns it, the system MUST report the owner, MUST NOT steal the endpoint, and MUST
  offer the opt-in adoption or per-port fallback rather than silently degrading.

### Key Entities

- **Ingress Observation**: Product evidence, process identity where observable, endpoint
  address/port/protocol, bind scope, support tier, capabilities, and health.
- **Ingress Selection**: Effective product, required protocols, pin value/source,
  acceptable listener addresses, consent/credential readiness, and selection reason.
- **Route Record**: Hostname, backend endpoint, ingress identity, promised protocols,
  ownership mark, last applied state, and current drift/health.
- **Incumbent Consent**: Machine/incumbent identity, accepted or declined state, and
  explicit reconsideration status.
- **Cleanup Recovery**: Residual route identity, expected prior state, incumbent reference,
  failure reason, and retry status without secrets.
- **Support Declaration**: Product/platform tier, documented control prerequisite,
  capabilities, and required live-proof status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of bind-scope fixtures—including exact loopback, dedicated loopback,
  wildcard IPv4/IPv6, Sandbox-owned, and split-protocol cases—produce the correct conflict
  classification without changing listeners.
- **SC-002**: Every product advertised as adoptable passes a live add → request → update →
  request → remove lifecycle while all sampled pre-existing routes remain healthy.
- **SC-003**: Foreign route and drift scenarios preserve foreign/changed configuration
  byte-for-byte in 100% of tests.
- **SC-004**: All validation, reload, and post-apply failure scenarios restore prior
  incumbent state and retain the working per-port instance URL.
- **SC-005**: Non-interactive consent/credential scenarios complete within normal command
  bounds with zero prompts and zero incumbent mutations.
- **SC-006**: Add, update, remove, detection, and cleanup pass twice consecutively with no
  duplicate route and the same final owned state.
- **SC-007**: Status identifies effective ingress, support tier, endpoint owner, pin source,
  capabilities, route health/drift, and fallback reason in every tested state.
- **SC-008**: Confirmed port-conflict tests produce zero messages blaming unavailable
  Docker.
- **SC-009**: Cleanup removes 100% of unchanged reachable owned routes and reports 100% of
  unavailable/drifted residuals without claiming complete removal.
- **SC-010**: Existing persisted hostnames continue to serve through the selected ingress;
  new unpinned names supplied by B also serve without changing the C backend identity.
- **SC-011**: On a host with free required endpoints and zero adoptable incumbent adapters,
  100% of new and existing instances still serve their clean URL through Sandbox Caddy, with
  no result that reports only a per-port fallback.
- **SC-012**: Switching between default and adopted ingress, in both directions, preserves
  the instance hostname, data, and per-port URL and requires no reprovisioning.

## Assumptions

- B chooses or preserves the hostname and verifies resolution before A activates it.
- C exposes a backend endpoint or runtime requirement without registering the hostname.
- Products lacking a stable documented control surface remain detect-only even if private
  automation appears technically possible.
- One authoritative ingress per hostname is an intentional safety boundary.
- The per-port URL remains available whenever instance provisioning itself succeeds.
