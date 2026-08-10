# Feature Specification: Managed Hosting with Cloudflare DNS and TLS

**Feature Branch**: `015-managed-hosting-cloudflare`

**Created**: 2026-07-10

**Status**: Implemented for offline validation, guarded planning, and mocked rollback.
Live Cloudflare/Lenzora apply remains deliberately pending explicit approval.

**Input**: User description: "Add managed Compose hosting, Cloudflare DNS and Origin CA TLS, domain aliases, WordPress multisite, and production/development site configurations without changing live infrastructure."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Validate a hosted project (Priority: P1)

A developer can place a hosting manifest in a project and validate its domains,
aliases, Compose service, deployment policy, and Cloudflare intent before any
credentials or remote infrastructure are used.

**Why this priority**: It makes the configuration safe to review and provides value
without changing production infrastructure.

**Independent Test**: Run the validation command against a static site, a WordPress
multisite, and each Lenzora environment without network credentials.

**Acceptance Scenarios**:

1. **Given** a valid manifest, **When** a developer validates it, **Then** the command
   reports the normalized host and routing configuration.
2. **Given** duplicate, cyclic, or invalid wildcard aliases, **When** a developer
   validates the manifest, **Then** the command fails with an actionable explanation.

---

### User Story 2 - Review and apply a protected hosting change (Priority: P2)

A developer can see the required remote runtime, DNS, TLS, and Caddy changes before
applying them, and can only apply a reviewed change with an explicit confirmation.

**Why this priority**: DNS and certificates are production-sensitive and must be
reviewable and reversible.

**Independent Test**: Mock the Cloudflare and remote interfaces, inspect the generated
plan, and verify that an apply operation is rejected without confirmation.

**Acceptance Scenarios**:

1. **Given** a remote with an origin address and a Cloudflare token, **When** a developer
   requests a plan, **Then** it lists only the declared DNS and routing changes.
2. **Given** no explicit confirmation, **When** a developer requests an apply operation,
   **Then** no remote, DNS, or certificate mutation occurs.

---

### User Story 3 - Describe personal, WordPress, and application hosting (Priority: P3)

A developer can maintain site-specific manifests that describe one static personal
site across three domains, an IDN WordPress multisite with redirects, and isolated
Lenzora production/development environments.

**Why this priority**: These configurations prove the generic hosting contract against
the real services it is intended to support.

**Independent Test**: Validate each committed manifest and render its expected aliases,
redirects, and runtime isolation.

**Acceptance Scenarios**:

1. **Given** the personal-site manifest, **When** it is rendered, **Then** all three
   apex domains serve the same application and every `www` host redirects to its apex.
2. **Given** the WordPress manifest, **When** it is rendered, **Then** the IDN apex and
   wildcard route to the network while the `asb.bd` and `www` aliases redirect.
3. **Given** the Lenzora manifests, **When** they are rendered, **Then** production and
   development have distinct runtime names, data volumes, and deployment policies.

### User Story 4 - Persist an origin Basic Auth gate (Priority: P2)

A developer can declare a shared Basic Auth gate for a hosted environment so every
future apply regenerates the origin proxy configuration with the same protection,
without committing the password or its hash.

**Independent Test**: Validate a manifest with a Basic Auth secret reference, inspect
the read-only plan, apply it with mocked remote hashing, and verify the generated Caddy
configuration contains only the username and generated hash.

**Acceptance Scenarios**:

1. **Given** a manifest with `basic_auth.username` and `basic_auth.password_secret`,
   **When** it is validated, **Then** the username and secret reference are accepted
   while the password remains absent from the manifest and plan output.
2. **Given** a configured Basic Auth secret, **When** the host is applied, **Then** the
   password is streamed to remote Caddy hashing, the generated hash is written to the
   managed Caddy fragment, and Caddy is validated before reload.
3. **Given** a Basic Auth environment with a missing secret, **When** a plan or apply is
   requested, **Then** the command reports the missing secret before remote mutation.

### Edge Cases

- A zone-wide TLS-mode change with unmanaged proxied records requires an additional
  explicit acknowledgement.
- Cloudflare API failures, invalid token permissions, Caddy validation failures, and
  failed health checks leave the prior DNS and routing state unchanged.
- Existing records outside the manifest, including mail records, are never deleted.
- Unicode hostnames, wildcard hostnames, and direct `www` aliases normalize consistently.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST load and validate project-local hosting manifests without
  requiring Cloudflare credentials or a remote connection.
- **FR-002**: The system MUST support one primary hostname and aliases that either serve
  the same application or issue path- and query-preserving permanent redirects.
- **FR-003**: The system MUST normalize internationalized hostnames before using them for
  DNS, certificates, routing, or persisted state.
- **FR-004**: The system MUST produce a read-only deployment plan before a hosting change.
- **FR-005**: The system MUST require an explicit confirmation flag before changing a
  remote runtime, Caddy configuration, certificate, or DNS record.
- **FR-006**: The system MUST retain Cloudflare tokens and private certificate keys outside
  version control and MUST NOT print those values after storage.
- **FR-007**: The system MUST preserve unrelated DNS records and restore managed routing
  and DNS state when a verified apply fails.
- **FR-008**: The system MUST support isolated Compose environments with distinct project
  names, loopback ports, persistent volumes, and deployment policies.
- **FR-009**: The system MUST provide manifests for the personal static site, the IDN
  WordPress multisite, and Lenzora production/development environments.
- **FR-010**: A hosted environment MAY declare an origin Basic Auth username and a
  secret-store password reference; the manifest MUST NOT contain a plaintext password
  or generated password hash.
- **FR-011**: When Basic Auth is declared, `host apply` MUST stream the password to the
  remote Caddy hash command without placing it in argv, logs, generated Compose
  environment files, or persisted host state.
- **FR-012**: The generated managed Caddy route MUST use the supported `basicauth`
  directive, include the declared username and generated hash, and validate before
  reload; a failed validation MUST leave the previous route active.

### Key Entities

- **Hosting manifest**: The versioned project description of a runtime, deployment policy,
  hostnames, aliases, and Cloudflare intent.
- **Hosted environment**: One isolated deployment of a manifest environment on a selected
  remote host.
- **Host route**: A normalized hostname, its service or redirect behavior, and certificate
  coverage.
- **Cloudflare zone change**: The scoped DNS and TLS settings required for declared hosts.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All three project manifests validate without credentials or live changes.
- **SC-002**: Invalid alias, wildcard, and deployment-policy configurations are rejected
  before any remote action is attempted.
- **SC-003**: A reviewed plan identifies every managed hostname, redirect, certificate
  name, and DNS record while omitting unrelated records.
- **SC-004**: A failed apply restores the prior managed DNS and routing state in automated
  tests.
- **SC-005**: A subsequent confirmed apply of a Basic Auth environment preserves the
  authentication gate, and anonymous edge requests receive `401` while valid
  credentials reach the application.

## Assumptions

- Existing DNS, containers, and Cloudflare settings remain unchanged until a developer
  explicitly runs a separately approved apply command.
- A future Cloudflare token has access to all declared zones and only the required DNS,
  certificate, and zone-setting permissions.
- The existing remote remains an explicit command argument; manifests never embed an
  origin IP address or remote name.
- The new WordPress repository begins as a blank multisite network; no existing data is
  migrated in this feature.
