# Feature Specification: Generic Remote Deploy

**Feature Branch**: `029-generic-remote-deploy`
**Created**: 2026-07-18
**Status**: Draft
**Input**: User description: "find out further improvement for sandbox. also make sure non wp can create local instance and do remote deploy hassle free like wordpress instance (local/remote)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a Non-WordPress Project Locally (Priority: P1)

A developer with an explicitly configured non-WordPress web project starts a local
instance through the same familiar lifecycle used for WordPress projects and receives
a reachable URL plus useful status and diagnostics.

**Why this priority**: Local development is the prerequisite for every later remote
workflow and must not require framework-specific tooling or hidden discovery.

**Independent Test**: A configured representative web project can be started twice,
probed, inspected, and stopped/restarted without changing its source or declared
persistent data.

**Acceptance Scenarios**:

1. **Given** an explicit non-WordPress project declaration, **When** the developer
   ensures a local instance, **Then** Sandbox returns its reachable URL, declared
   service, health state, and supported operations.
2. **Given** a healthy local instance, **When** the developer ensures it again,
   **Then** Sandbox reuses it without creating duplicate application state.
3. **Given** a generic project lacking required deployment information, **When** the
   developer requests an instance or deploy, **Then** Sandbox reports the missing
   information and does not infer commands from repository files.

---

### User Story 2 - Deploy a Non-WordPress Project to a Registered Remote (Priority: P1)

A developer deploys a configured non-WordPress project to a provisioned remote with
the same one-command path as a WordPress project: transfer the current working tree,
ensure the remote instance, optionally expose it at a declared hostname, and receive
the resulting URL.

**Why this priority**: The present remote path rejects the already-supported generic
runtime, forcing developers into manual remote Compose, routing, and refresh steps.

**Independent Test**: A generic fixture deploy uses the standard remote workflow,
creates or refreshes the remote instance, passes its health check, and returns a
public URL when exposure is requested.

**Acceptance Scenarios**:

1. **Given** a provisioned remote and a valid generic project, **When** the developer
   deploys with remote ensure, **Then** the remote working tree and generic instance
   match the current local project state.
2. **Given** a healthy generic remote instance and a declared public hostname,
   **When** the developer requests exposure, **Then** its declared service is routed
   to HTTPS and the command returns that URL.
3. **Given** a subsequent deployment with changed or deleted uncommitted files,
   **When** deployment runs, **Then** the remote reflects only the latest local state
   and retains no stale transferred files.

---

### User Story 3 - Receive Consistent, Safe Deployment Guidance (Priority: P2)

A developer receives clear, kind-neutral errors and documentation when a project is
not ready for remote deployment, without Sandbox copying secrets, opening undeclared
routes, or treating arbitrary repository content as a deployment contract.

**Why this priority**: A convenient deploy command must not weaken the explicit
configuration and confirmation boundaries that make shared remote hosts safe.

**Independent Test**: Invalid, WordPress, and generic project declarations exercise
the same command surface and produce relevant capability or contract messages before
any remote mutation.

**Acceptance Scenarios**:

1. **Given** a generic project without a declared public service or health check,
   **When** remote deployment is requested, **Then** it fails locally with actionable
   guidance before contacting the remote.
2. **Given** a generic project, **When** exposure is requested without a hostname,
   **Then** Sandbox derives only the existing safe default or asks for a declared
   hostname according to current remote-hosting policy.
3. **Given** a deploy error, **When** the result is returned in JSON or human output,
   **Then** it has the same stable result shape and does not disclose SSH targets or
   secrets.

---

### User Story 4 - Use a Runtime-Relevant MCP Catalog (Priority: P1)

A developer or coding agent can start a project-scoped MCP server and sees only tools
that are useful to that project's runtime, rather than having to filter a catalog full
of operations that can never apply.

**Why this priority**: Capability errors protect execution, but withholding irrelevant
tools prevents bad tool selection and makes non-WordPress use feel first-class.

**Independent Test**: The declared WordPress and Compose project profiles have no
runtime-exclusive group in common, while both retain their shared lifecycle, network,
and remote deploy path.

**Acceptance Scenarios**:

1. **Given** an MCP server scoped to a Compose project, **When** its catalog is
   initialized, **Then** WordPress database, filesystem, REST, and plugin tools are
   absent.
2. **Given** an MCP server scoped to a WordPress project, **When** its catalog is
   initialized, **Then** generic container-execution and generic-service-log tools are
   absent.
3. **Given** an existing unscoped MCP registration or an explicit group override,
   **When** it starts, **Then** its established catalog behavior remains available.

### Edge Cases

- A generic project declares a public service whose mapped port differs from the
  WordPress port conventions.
- The remote instance is already healthy and a repeated deploy changes only source
  files; the refresh is idempotent and preserves project-owned volumes.
- A health probe fails after remote transfer; the deploy reports failure and does not
  claim a reachable public URL.
- WordPress deployment behavior, including plugin activation and URL updates, remains
  unchanged.
- Public routing is requested for a hostname already claimed by another remote route;
  Sandbox rejects the conflict rather than replacing it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST support explicitly declared non-WordPress web projects
  through the same local instance lifecycle and kind-neutral status contract as
  WordPress projects.
- **FR-002**: Sandbox MUST allow an explicitly configured generic project to use the
  existing registered-remote deployment command without requiring WordPress-only
  capabilities.
- **FR-003**: A generic remote deployment MUST transfer committed and current local
  working-tree state using the existing replace-not-stack behavior.
- **FR-004**: When remote ensure is requested, Sandbox MUST create or reconcile the
  declared generic remote instance and verify its declared health condition before
  reporting it ready.
- **FR-005**: When public exposure is requested, Sandbox MUST route HTTPS traffic to
  the declared generic public service and return the resulting URL without applying
  WordPress-specific activation or URL-update behavior.
- **FR-006**: Sandbox MUST validate generic remote-deployment prerequisites locally
  before remote mutation and provide actionable errors for missing or incompatible
  declarations.
- **FR-007**: Sandbox MUST preserve WordPress remote-deployment behavior, result
  fields, activation, and public URL behavior.
- **FR-008**: Sandbox MUST keep secrets out of project transfer metadata, registry
  records, user-visible output, and generated documentation.
- **FR-009**: Sandbox MUST document a single local-to-remote workflow for generic
  projects, including configuration prerequisites, deploy, exposure, and common
  failure recovery.
- **FR-010**: Sandbox MUST offer a project-scoped MCP startup mode that selects a
  runtime-relevant tool catalog before MCP registration.
- **FR-011**: A scoped Compose catalog MUST exclude WordPress-only groups, and a
  scoped WordPress catalog MUST exclude generic-runtime-only groups, while both retain
  their shared lifecycle, network, and remote deploy operations.
- **FR-012**: Existing unscoped MCP registrations and explicit catalog allowlists MUST
  remain backward compatible.

### Key Entities *(include if feature involves data)*

- **Generic deployment contract**: The explicit project-owned declaration that names
  the service, reachable port, health condition, and deployment-safe public behavior.
- **Remote instance**: The remote-owned realization of one project and optional label,
  including its kind, service endpoint, health status, and optional public URL.
- **Deployment result**: The stable report of transfer, instance reconciliation,
  optional exposure, and errors returned by the CLI and agent integration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer can start a representative configured non-WordPress project
  locally and obtain a healthy URL in one lifecycle command, with a repeat run creating
  no duplicate instance.
- **SC-002**: A developer can deploy a representative configured non-WordPress
  project to a provisioned remote and obtain a healthy remote instance in one deploy
  command, with no manual remote Compose or routing commands.
- **SC-003**: When exposure is requested for a valid declared hostname, the returned
  HTTPS URL passes the project health check after deployment.
- **SC-004**: All current WordPress remote-deployment acceptance coverage continues to
  pass unchanged, while generic deployment adds equivalent transfer, ensure, exposure,
  failure, and repeat-deploy coverage.
- **SC-005**: Invalid generic deployment declarations fail before a remote connection
  is initiated and identify the missing requirement in the first error message.
- **SC-006**: Project-scoped Compose and WordPress MCP catalog tests demonstrate that
  neither catalog exposes the other runtime's exclusive tool group.

## Assumptions

- Generic projects remain explicit Compose-based web projects; Sandbox does not infer
  arbitrary deployment commands or service definitions.
- The existing registered remote and its control-plane provisioning are reused; this
  feature does not create a new remote hosting model.
- Public DNS and certificate policy remain governed by the current managed-hosting
  workflow and retain its confirmation requirements.
- Project-owned persistent volumes are preserved during normal generic refreshes;
  destructive cleanup remains separately confirmed.
