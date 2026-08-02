# Feature Specification: TLD and DNS Adoption

**Feature Branch**: `latest`

**Created**: 2026-08-01

**Status**: Draft

**Input**: Ready product requirements from `specs/038-tld-dns-adoption/prd.md`

## Clarifications

### Session 2026-08-02

- Q: Which resolution path is the product default once this feature ships? → A: Sandbox's own
  DNS bootstrap for its own suffix — the same mechanism that serves the current Docker/Caddy
  clean URLs — on every supported platform and for every runtime.
- Q: Is that default gated by resolver adapter support/live-proof tiers? → A: No. Proof tiers
  gate adoption of a host-owned resolver only; the default path stays available when no
  adapter is proven.
- Q: How does a user get incumbent-resolver adoption instead? → A: Explicit opt-in,
  selectable during setup and switchable on demand afterwards, per project or per machine.
- Q: Does this feature replace the legacy DNS path? → A: No. It is an additional selectable
  strategy. Removal of the existing path requires live parity plus explicit human approval
  per constitution principle VI.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resolve Through the Active Host Manager (Priority: P1)

A developer enables a clean local hostname on a machine whose name resolution is already
managed by the operating system or another development tool. Sandbox identifies that
owner, uses a supported scoped integration, and proves the hostname reaches the ingress
selected for the instance without replacing the machine's resolver configuration.

**Why this priority**: A clean hostname is unusable until resolution works, and resolver
takeover can disrupt every network operation on the host.

**Independent Test**: On each advertised resolver environment, create one instance,
enable its clean hostname, resolve it, make a request through the selected ingress, and
compare unrelated resolution and resolver ownership before and after.

**Acceptance Scenarios**:

1. **Given** a host managed by a supported routed resolver, **When** clean naming is
   enabled, **Then** only the Sandbox-owned namespace is routed to a scoped answering
   authority and the hostname resolves to an address accepted by the selected ingress.
2. **Given** a host whose manager directly supports scoped local records, **When** clean
   naming is enabled, **Then** Sandbox adds only its owned rule through that manager and
   starts no competing resolver.
3. **Given** a manager with no supported extension point, **When** clean naming is
   requested, **Then** the instance remains usable at its per-port URL and no resolver
   setting changes.

---

### User Story 2 - Preserve Safe Project Identity (Priority: P1)

A developer receives a standards-safe default hostname for a new project while existing
and explicitly configured project identities remain stable.

**Why this priority**: WordPress persists absolute URLs, so an implicit rename can corrupt
application behavior even if DNS itself works.

**Independent Test**: Create a new unpinned project, re-ensure an existing `.tst` project,
validate a new `.local` request, and apply an incompatible explicit hostname.

**Acceptance Scenarios**:

1. **Given** a new project with no persisted or explicit local hostname, **When** clean
   naming is enabled, **Then** it receives a `.test` hostname.
2. **Given** an existing project with a persisted `.tst` hostname, **When** it is ensured
   or applied, **Then** its hostname remains byte-for-byte unchanged.
3. **Given** a request for a new `.local` hostname, **When** configuration is validated,
   **Then** it is rejected before mutation with a `.test` alternative.
4. **Given** an explicit hostname incompatible with the selected resolver or ingress,
   **When** ensure runs, **Then** the identity is preserved and Sandbox uses the per-port
   URL rather than silently renaming the project.

---

### User Story 3 - Diagnose, Reconcile, and Clean Up Safely (Priority: P2)

A developer can see who owns name resolution, what answer is active, whether Sandbox owns
the rule, and what remains when the resolver changes, disappears, or drifts.

**Why this priority**: Host networking changes over time, and stale markers are not proof
that a hostname currently works or can be safely removed.

**Independent Test**: Change the active resolver, alter an owned rule externally, stop the
answering authority, then run status, destroy, and cleanup twice.

**Acceptance Scenarios**:

1. **Given** the active resolver differs from the recorded resolver, **When** status runs,
   **Then** it reports the current owner, actual answer, expected address, health, and
   recovery action without mutating either resolver.
2. **Given** a Sandbox-marked rule changed outside Sandbox, **When** ensure or destroy
   runs, **Then** the rule is left untouched and an explicit reconciliation result is
   returned.
3. **Given** an unchanged owned rule, **When** the instance is destroyed, **Then** only
   that rule is removed and repeated destroy is safe.
4. **Given** cleanup cannot reach the prior resolver, **When** destroy or uninstall runs,
   **Then** cleanup is reported incomplete and a non-secret retry record is retained.

---

### User Story 4 - Support Wildcard-Dependent Sites Without Overreach (Priority: P2)

A developer running subdomain multisite can use arbitrary subdomains only when the active
resolution path safely supports the required wildcard namespace.

**Why this priority**: Exact-name success can hide a broken multisite configuration, while
an overly broad wildcard can shadow unrelated names.

**Independent Test**: Enable subdomain multisite, resolve a previously unseen subdomain,
then remove one and the last consumer of the wildcard namespace.

**Acceptance Scenarios**:

1. **Given** a wildcard-capable path and a declared local suffix, **When** subdomain
   multisite is enabled, **Then** an arbitrary new subdomain resolves without another
   per-name mutation.
2. **Given** an exact-name-only path, **When** subdomain multisite is requested, **Then**
   Sandbox reports the capability gap and does not claim the network is ready.
3. **Given** a foreign or publicly delegated namespace, **When** wildcard registration is
   considered, **Then** Sandbox refuses to create a local wildcard override.

### Edge Cases

- The project and machine-local override pin different resolver strategies; the
  machine-local override wins and its source is reported.
- A non-interactive caller reaches first-use consent or privilege; it receives a pending-
  consent result without a prompt or mutation.
- The selected ingress changes listener address after resolution was installed; status
  reports the mismatch before A activates or advertises the route.
- A routed resolver is available but the scoped answering endpoint collides with a foreign
  listener; Sandbox does not steal it and uses a safe fallback.
- Multiple projects share one owned wildcard zone; removing one project preserves the zone
  until the last unchanged owner is removed.
- Cached answers survive a rule update; health remains pending/unhealthy until a fresh
  lookup returns the expected address.
- An explicit public FQDN resolves externally to an accepted local ingress address;
  Sandbox consumes that answer but creates no local override.
- The system changes network interface or VPN state and transient routing-domain state is
  lost; status measures current behavior rather than trusting persisted intent.
- An adoptable resolver is present and proven but no adoption was selected; the default
  Sandbox-owned strategy is used and adoption is offered, not applied.
- No resolver adapter has recorded live proof on this platform; the default strategy still
  resolves the hostname and only adoption is reported unavailable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST detect the active local name-resolution owner, mode, and
  current answer without changing host state.
- **FR-002**: The system MUST distinguish supported incumbent integration, Sandbox-managed
  fallback, exact-name fallback, detect-only, externally resolved, and unavailable states.
- **FR-003**: Spec A MUST provide acceptable listener addresses and hostname capabilities
  before this feature installs resolution.
- **FR-004**: This feature MUST choose or preserve the hostname, resolve it to one address
  accepted by A, and return verified naming state before A activates the hostname route.
- **FR-005**: A routed-resolver integration MUST have a scoped Sandbox-owned answering
  authority that serves only declared Sandbox names or zones.
- **FR-006**: The scoped authority MUST NOT become the machine's general upstream resolver
  or forward unrelated queries.
- **FR-007**: The scoped authority MUST use a collision-free local endpoint, expose health
  and ownership, start idempotently, and stop after its final owned zone is removed.
- **FR-008**: A direct-record resolver integration MUST use only its documented scoped
  extension and reload path and MUST NOT switch the host's global resolver mode.
- **FR-009**: The system MUST NOT replace a resolver-managed configuration file or steal a
  port from a foreign listener.
- **FR-010**: New projects without a persisted or explicit hostname MUST use `.test`.
- **FR-011**: Existing persisted hostnames, including `.tst`, MUST remain unchanged during
  ordinary ensure, apply, status, and upgrade.
- **FR-012**: New `.local` hostnames MUST be rejected before mutation.
- **FR-013**: An explicit incompatible hostname MUST be preserved and MUST fall back to the
  per-port URL unless the user separately confirms a migration.
- **FR-014**: The system MUST NOT create local wildcard or shadowing rules for publicly
  delegated names; it may only consume and verify their external answers.
- **FR-015**: Exact-name rules MUST be preferred unless a declared feature, such as
  subdomain multisite, requires a wildcard.
- **FR-016**: A wildcard MUST be limited to the declared local suffix and MUST NOT shadow a
  foreign or broader namespace.
- **FR-017**: Before writing, the system MUST detect foreign exact-name, wildcard, endpoint,
  and ownership collisions and refuse to overwrite them.
- **FR-018**: Every created rule or zone MUST be attributable to Sandbox and its instance
  or shared suffix owners without relying on a name alone.
- **FR-019**: The system MUST modify or remove an owned rule only when observed state still
  matches the last state written by Sandbox.
- **FR-020**: Drifted, unavailable, or rejected cleanup MUST return an incomplete result and
  retain the minimum non-secret recovery state required for retry.
- **FR-021**: First mutation of a user-owned resolver MUST require recorded interactive
  consent; non-interactive callers MUST NOT prompt or mutate.
- **FR-022**: A remembered decline MUST suppress repeated offers until an explicit user
  action reconsiders it.
- **FR-023**: An explicit resolver pin MUST beat detection; a machine-local project
  override MUST beat the committed project pin.
- **FR-024**: Status MUST report requested hostname, effective pin and source, resolver
  owner, integration tier, actual answer, expected listener address, ownership, health,
  fallback URL, and actionable reason for any failure.
- **FR-025**: DNS adoption failure MUST NOT block successful instance provisioning or its
  per-port URL.
- **FR-026**: Add, update, status, removal, and cleanup operations MUST be safe to repeat.
- **FR-027**: Every advertised resolver environment MUST be proven by a live fresh lookup
  followed by a request through the selected ingress.
- **FR-028**: Unrelated local, internet, search-domain, and VPN resolution MUST remain
  unchanged across adoption and cleanup.
- **FR-029**: Sandbox's own scoped resolution bootstrap MUST be the default strategy on every
  supported platform and for every runtime, and MUST NOT depend on any incumbent resolver
  adapter reaching an adoptable support tier.
- **FR-030**: Adoption of a host-owned resolver MUST be opt-in. With no explicit selection,
  the system MUST use the default Sandbox-owned strategy even when an adoptable incumbent is
  detected and proven.
- **FR-031**: A user MUST be able to select the resolution strategy during setup and switch it
  on demand afterwards, at project and machine-local scope, without destroying or
  reprovisioning the instance and without changing the persisted hostname.
- **FR-032**: The existing clean-URL resolution path, including the privileged bootstrap it
  requires, MUST keep working unchanged while this feature ships. Disabling, bypassing, or
  removing it requires recorded live parity of the replacement plus explicit human approval.
- **FR-033**: The system MUST report a per-port fallback only when both the default strategy
  and any selected adoption strategy are genuinely unavailable, and MUST name which
  precondition failed.

### Key Entities

- **Hostname Intent**: The persisted or selected hostname, suffix class, source, explicit
  versus default status, required wildcard capability, and migration status.
- **Resolver Observation**: Current owner, mode, endpoint, active answer, applicable
  extension point, support tier, and health evidence.
- **Resolution Binding**: One exact name or local zone, its target ingress address,
  strategy, owners, last applied state, and observed drift.
- **Answering Authority**: The scoped local answer service, collision-safe endpoint,
  declared zones, health, and owner references.
- **Consent Record**: Resolver identity, machine scope, decision, and reconsideration state
  without credentials.
- **Cleanup Record**: Residual owned state, prior resolver identity, expected value, and
  retry status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of advertised resolver environments complete a fresh name lookup and
  HTTP request through the selected ingress on a live host before release.
- **SC-002**: In resolver adoption and cleanup tests, 100% of unrelated sampled DNS answers
  and resolver ownership markers remain unchanged.
- **SC-003**: A new unpinned project receives `.test`, while 100% of existing persisted
  hostname fixtures retain their exact identity through ensure and apply.
- **SC-004**: All non-interactive first-use cases return within the command's normal bound
  with zero prompts and zero resolver mutations.
- **SC-005**: Add, update, remove, and cleanup pass two consecutive executions with the
  same final owned state and no duplicate rules or authorities.
- **SC-006**: All foreign-collision and drift scenarios preserve the foreign/changed state
  byte-for-byte and return an actionable fallback or reconciliation result.
- **SC-007**: A wildcard-capable path resolves an unseen subdomain in one existing zone;
  exact-only paths have a 0% false-ready rate for subdomain multisite.
- **SC-008**: Status identifies the active resolver owner, actual/expected address,
  effective pin source, health, and fallback reason in every tested healthy and unhealthy
  state.
- **SC-009**: Public-FQDN scenarios create zero local override records.
- **SC-010**: Cleanup removes 100% of unchanged reachable owned records and truthfully
  reports 100% of unreachable or drifted residuals rather than claiming completion.
- **SC-011**: On a host with zero adoptable resolver adapters, 100% of new and existing
  instances still resolve their clean hostname through the default Sandbox-owned strategy,
  with no result that reports only a per-port fallback.
- **SC-012**: Switching between default and adopted resolution, in both directions, preserves
  the persisted hostname byte-for-byte and requires no reprovisioning.

## Assumptions

- Local clean hostnames serve the current machine; public and remote DNS remain governed
  by existing hosting features.
- Spec A can provide one or more local listener addresses before hostname activation.
- Supported resolver managers expose a stable scoped extension point or can be left
  untouched in favor of the default Sandbox-owned strategy, with the per-port URL used only
  when that default is also unavailable.
- A publicly delegated name is externally managed and is never synthesized locally by
  this feature.
- Existing `.tst` identities remain supported indefinitely unless the user invokes a
  separate confirmed migration.
