# Feature Specification: Cross-Platform Open-Source Secret Manager

**Feature Branch**: `latest`

**Created**: 2026-10-13

**Status**: Draft

**Input**: Approved product requirements in `specs/067-keyring-secret-manager/prd.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Use One Registered Secret Across Controllers (Priority: P1)

A developer can use the same authorized project secret from Linux and macOS
through the existing Sandbox secret broker without copying the value into either
project checkout or between controllers.

**Why this priority**: This is the core user value: a shared source of truth that
preserves the broker's least-disclosure behavior on both supported platforms.

**Independent Test**: Register one synthetic provider item and one fixed use
profile, give each supported controller its own credential for the same
project-scoped identity, and show that the fixed child receives the same value on
both controllers while all retained evidence remains secret-free.

**Acceptance Scenarios**:

1. **Given** Linux and macOS can reach the registered private provider endpoint
   and each has its own valid local controller credential, **When** the user runs
   the same fixed use profile on both controllers, **Then** both executions
   resolve the exact registered item and deliver it only to the bounded child.
2. **Given** the registered provider item has a known synthetic value, **When**
   either controller completes a use operation, **Then** that value appears in
   none of the project files, command arguments, parent environment, routine
   output, audit records, logs, or retained job evidence.
3. **Given** the Linux controller credential is revoked while the macOS
   credential remains valid, **When** both controllers request the same profile,
   **Then** Linux receives a bounded authorization denial and macOS continues to
   succeed without copying or replacing its credential.

---

### User Story 2 - Preserve Exact Scope and Caller Boundaries (Priority: P1)

A project can use only its registered provider project and exact item selector,
while automation callers remain unable to broaden access, browse the provider,
or change registrations.

**Why this priority**: Centralizing secrets increases blast radius unless the
provider project boundary and Sandbox's exact-selector policy both remain
enforceable and testable.

**Independent Test**: Configure two provider projects and deliberately attempt
cross-project access, request-time selector overrides, listing, search, reveal,
registration changes, and caller-selected execution through both CLI and MCP
surfaces; verify that only the authorized exact-item operation succeeds.

**Acceptance Scenarios**:

1. **Given** Project A and Project B have distinct read-only provider identities,
   **When** Project A names an item in Project B, **Then** the provider denies the
   request and Sandbox returns only a secret-free scoped denial.
2. **Given** a registered environment, path, and key, **When** a caller supplies
   a different selector without changing the authoritative registration,
   **Then** Sandbox refuses the request without search, wildcard lookup,
   neighbouring-item substitution, or collection listing.
3. **Given** an MCP or unattended caller with an authorized fixed use profile,
   **When** it attempts to list, search, reveal, write, delete, migrate, change a
   registration, administer identities, or choose an arbitrary child command,
   **Then** every attempt is refused even if the underlying provider credential
   could perform a broader action.
4. **Given** a same-user human can edit authoritative project configuration or
   access the provider separately, **When** that user changes the registration or
   bypasses Sandbox, **Then** the documentation identifies this as inside the
   user's authority and outside the broker's isolation guarantee.

---

### User Story 3 - Fail Closed with Actionable, Secret-Free Outcomes (Priority: P2)

A developer or operator can distinguish configuration, credential, transport,
authorization, capacity, and provider failures without exposing secret material
or silently falling back to a weaker source.

**Why this priority**: Cross-machine secret use is operationally useful only when
failures are bounded, diagnosable, and do not create an unsafe recovery path.

**Independent Test**: Exercise each defined negative state using synthetic
credentials and responses, then verify a stable outcome category, an actionable
remedy, bounded completion, no value fetch during status checks, and no plaintext
fallback.

**Acceptance Scenarios**:

1. **Given** the local credential store is missing, locked, unsupported, or
   unavailable to the current session, **When** a provider-backed operation
   begins, **Then** it fails closed with a distinct remedy and does not read a
   fallback file, start a credential-store service, or request a password through
   an exposed channel.
2. **Given** private-network reachability, name resolution, endpoint identity, or
   provider availability fails, **When** a request begins, **Then** it terminates
   within its declared bound without switching to a public endpoint or replaying
   an operation whose outcome is uncertain.
3. **Given** a controller credential is revoked, a provider session has expired,
   or project permission is removed, **When** a new operation begins, **Then**
   Sandbox uses only the approved bounded authentication path or returns a stable
   denial and never reuses expired authority.
4. **Given** the community deployment cannot provide another distinct project
   identity or controller credential, **When** registration is attempted,
   **Then** registration fails before value retrieval without sharing credentials,
   weakening isolation, or silently requiring a paid feature.
5. **Given** an operator requests capability or status information, **When** the
   inspection completes, **Then** it reports only approved non-secret state and
   does not fetch a secret value.

---

### User Story 4 - Adopt Without Disrupting Existing Secret Sources (Priority: P3)

A project can opt into the provider-backed source while existing registered file
sources continue to behave exactly as before.

**Why this priority**: Adoption must remain reversible and cannot disable proven
secret workflows before parity is established.

**Independent Test**: Run the existing file-source conformance scenarios before
and after registering a provider-backed source and verify identical outcomes for
all pre-existing source types.

**Acceptance Scenarios**:

1. **Given** a project uses an existing registered file source, **When** provider
   support is available but not selected, **Then** every existing inspection,
   validation, and fixed-use behavior remains unchanged.
2. **Given** a provider-backed source is registered, **When** registration
   completes, **Then** no service deployment, public exposure, value migration,
   provider write, deletion, rotation, daemon installation, or source-file read
   occurs as a side effect.
3. **Given** the selected community distribution lacks a required built-in
   capability, **When** compatibility is assessed, **Then** the provider-backed
   source is reported as unsupported and existing sources remain available.

### Edge Cases

- The endpoint is reachable over the private network but presents an unexpected
  or unverifiable endpoint identity.
- The provider returns multiple matches, no match, a moved item, malformed data,
  excessive data, or a response after the operation bound expires.
- A provider session expires between authorization and exact-item retrieval.
- A credential is revoked on one controller while another controller is already
  using its distinct credential for the same project identity.
- An item name, project name, environment, path, label, or query attribute is
  configured with text that resembles a secret value.
- The provider identity has broader rights than the registered source requires.
- The local credential store prompts interactively during an unattended request.
- The community distribution changes its licensing, identity capacity, built-in
  roles, or supported authentication behavior after registration.
- A same-user human edits the authoritative registration while an operation is
  starting.
- Audit persistence fails before secret processing begins.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST support an opt-in provider-backed source through the
  existing secret-broker interface without creating a separate general-purpose
  secret command surface.
- **FR-002**: A provider-backed registration MUST identify exactly one provider
  project and one environment, path, and item key using non-secret metadata.
- **FR-003**: The registration schema MUST provide no field for secret values,
  bootstrap credentials, or provider sessions; MUST reject credential-, token-,
  and value-bearing fields; and MUST document that a same-user operator remains
  responsible for not disguising secret material as an allowed metadata value.
- **FR-004**: Each isolated Sandbox project MUST use a distinct provider project
  identity with provider-enforced read-only access to that provider project.
- **FR-005**: Environment, path, and item-key restrictions MUST be enforced as
  exact Sandbox selection policy and MUST NOT be represented as provider-enforced
  authorization when the selected community distribution does not enforce them.
- **FR-006**: Each participating controller MUST use its own independently
  revocable bootstrap credential for the project's provider identity.
- **FR-007**: Bootstrap credentials MUST remain in the controller's approved
  operating-system credential store and MUST NOT be copied or synchronized
  between controllers by Sandbox.
- **FR-008**: Provider session authority MUST be bounded to approved operations,
  MUST expire according to provider policy, and MUST NOT be reused beyond its
  validated lifetime or persisted in project configuration or Sandbox policy
  state.
- **FR-009**: A use operation MUST retrieve only the exact registered item and
  MUST NOT perform provider collection listing, wildcard lookup, broad search,
  neighbouring-item substitution, or bulk export.
- **FR-010**: A resolved value MUST be delivered only to the registered bounded
  child through the existing minimal-environment use boundary.
- **FR-011**: Secret values, bootstrap credentials, and provider session
  credentials MUST NOT appear in project files, command arguments, parent
  environments, routine output, logs, audit records, provider response dumps, or
  retained job evidence.
- **FR-012**: Audit intent MUST be durable before provider authentication or
  secret processing begins, and every outcome MUST remain secret-free.
- **FR-013**: Capability and status inspection MUST NOT retrieve a secret value
  and MAY report only approved non-secret source class, reachability,
  authorization, lifetime, supported-operation, and remedy information.
- **FR-014**: MCP and unattended callers MUST require explicit source-mode and
  fixed-use-profile authority for the exact registered item.
- **FR-015**: MCP and unattended callers MUST NOT create, modify, override, or
  delete a source registration and MUST NOT invoke reveal, listing, search,
  provider write, deletion, migration, identity administration, rotation, or a
  caller-selected child command.
- **FR-016**: Sandbox MUST treat authoritative configuration changes made within
  the same user's existing authority as registration changes and MUST document
  that it does not isolate that user from provider or configuration authority the
  user independently controls.
- **FR-017**: Provider-backed operations MUST fail closed with distinct,
  secret-free outcomes for invalid configuration, missing or locked local
  credential storage, unavailable user session, revoked credential, expired
  session, denied project scope, unavailable private network, endpoint trust
  failure, unavailable provider, missing or ambiguous item, malformed response,
  excessive response, timeout, and capacity exhaustion.
- **FR-018**: Failure handling MUST NOT fall back to plaintext sources, public
  endpoints, ambient environment credentials, shared controller credentials, or
  unreviewed credential-store services.
- **FR-019**: An uncertain provider operation MUST NOT be automatically retried
  in a way that can duplicate or broaden the operation.
- **FR-020**: Release compatibility evidence and the live acceptance gate MUST
  verify that the selected community self-hosted distribution supplies the
  required project identity and built-in read-only role without enterprise-only
  code or paid fine-grained access controls; local registration MUST NOT claim to
  infer this from an unauthenticated status response.
- **FR-021**: Registration of another isolated project MUST require an
  operator-provisioned distinct provider identity before value retrieval, and
  Sandbox MUST NOT create, substitute, or recommend a shared identity to evade
  provider capacity. Provider-side uniqueness and role scope are live acceptance
  evidence because provider administration is outside this feature.
- **FR-022**: Addition of another controller MUST require an independently
  revocable controller credential in that controller's approved local store
  before value retrieval, and Sandbox MUST NOT copy, substitute, or recommend a
  shared bootstrap credential. Provider-side credential capacity is an operator
  prerequisite verified by live acceptance.
- **FR-023**: The operator MUST supply a Tailscale-private endpoint and Sandbox
  MUST require verified HTTPS, disable redirects and public/alternate endpoint
  fallback, and document that private routing is a deployment precondition rather
  than authority granted by network reachability.
- **FR-024**: Source registration MUST NOT deploy, upgrade, expose, back up,
  restore, or otherwise administer the provider service.
- **FR-025**: Source registration and use MUST NOT automatically read existing
  secret files for migration, migrate values, write provider values, rotate or
  revoke existing credentials, or delete an existing source.
- **FR-026**: Existing registered file sources and their safety boundaries MUST
  remain supported and unchanged when provider support is present or enabled.
- **FR-027**: User-facing documentation MUST distinguish provider project
  authorization, Sandbox exact-selector policy, same-user authority, private
  network reachability, and operator-owned service recovery responsibilities.
- **FR-028**: Linux support MUST fail closed when an approved user-session
  credential store is unavailable; Sandbox MUST NOT create or manage a login
  session, credential-store daemon, or unattended unlock secret.
- **FR-029**: macOS and Linux MAY differ in prompt behavior, but both MUST satisfy
  the same non-disclosure, exact-selection, independent-revocation, and bounded
  failure outcomes.

### Key Entities

- **Provider-Backed Source**: An opt-in registered source that names one provider
  project and one exact item selector, its permitted broker modes, and only
  non-secret endpoint and scope metadata.
- **Project Identity**: The distinct read-only provider identity assigned to one
  Sandbox project; it defines the provider-enforced project boundary.
- **Controller Credential**: The independently revocable bootstrap authority held
  by one Linux or macOS controller for a project identity; its value is never
  project configuration.
- **Provider Session**: Temporary authority derived from a valid controller
  credential for a bounded approved operation.
- **Fixed Use Profile**: The pre-registered exact item and bounded child behavior
  authorized for interactive or explicitly approved unattended use.
- **Resolution Outcome**: A stable, secret-free success or refusal category with
  approved evidence and an actionable remedy.
- **Provider Capability Record**: Versioned, secret-free acceptance evidence that
  records the tested community release, built-in role, project-identity
  separation, controller credential separation, private route, and observation
  time. Local status is availability evidence only and cannot create this record.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a two-controller acceptance run, 100% of supported Linux and
  macOS controllers resolve the same authorized synthetic item through the same
  fixed use profile without copying a credential or secret value between them.
- **SC-002**: Across the defined output and persistence surfaces, automated scans
  find zero occurrences of the synthetic secret value, bootstrap credentials,
  or provider session credentials after successful and failed operations.
- **SC-003**: 100% of cross-project requests, request-time selector overrides,
  unregistered-item requests, listing, wildcard, search, reveal, write, delete,
  migration, identity-administration, registration-mutation, and arbitrary-child
  attempts are refused through unattended and MCP surfaces.
- **SC-004**: Every negative state enumerated by FR-017 produces its assigned
  stable, secret-free outcome and remedy, with zero plaintext or public-endpoint
  fallbacks.
- **SC-005**: Revoking either controller's bootstrap credential causes 100% of
  subsequent requests from that controller to fail while the other controller's
  independently authorized requests continue to succeed.
- **SC-006**: Capability and status inspection performs zero secret-value reads
  in both healthy and failing provider states.
- **SC-007**: Registration performs zero service deployments, public exposures,
  migrations, provider writes, rotations, deletions, daemon installations, or
  reads of existing secret files.
- **SC-008**: 100% of existing registered file-source conformance scenarios retain
  their pre-feature outcomes after provider-backed support is enabled.
- **SC-009**: Every focused automated acceptance case completes within 5 seconds;
  externally dependent acceptance operations terminate within their declared
  finite bound and never treat empty or malformed evidence as success.
- **SC-010**: A new project or controller whose required identity or credential
  capacity is unavailable is refused before any value retrieval in 100% of
  capacity-boundary tests, with no identity or credential sharing.

## Assumptions

- The operator supplies and maintains a compatible community self-hosted
  Infisical service, its authenticated private endpoint, encryption material,
  data store, backups, administrator access, upgrades, and disaster recovery.
- Tailscale provides private reachability between authorized controllers and the
  provider endpoint but does not provide provider identity, Sandbox
  authorization, endpoint identity, backup, or availability.
- Each participating desktop already has an approved operating-system credential
  store available to its user session.
- The selected community distribution continues to provide project identities
  and a built-in project-level read-only role without enterprise-only code.
- One provider identity represents one isolated Sandbox project's permission set;
  each controller authenticates to that identity with a distinct revocable
  credential.
- Existing source registration and fixed-use-profile authority remain the source
  of truth for broker behavior.
- Provider deployment, credential migration, provider-side writes, rotation,
  deletion, service backup, service recovery, Windows support, and managed
  headless credential-store unlock are outside this feature.
