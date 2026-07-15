# Feature Specification: Hermes Authorization Controls

**Feature Branch**: `codex/hermes-public-access`

**Created**: 2026-07-15

**Status**: Approved for implementation

**Input**: User description: "Add tools to review and authorize Hermes blocked work with immutable scoped approvals, audit evidence, expiration, and scheduler integration."

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Review a Pending Authorization (Priority: P1)

An operator can see every pending Hermes authorization request and inspect one request's exact job, requested scope, replay origin, rationale, state, timestamps, and sanitized evidence before deciding.

**Why this priority**: Hermes must not hide why it stopped or ask an operator to approve unstructured model output.

**Independent Test**: Create a pending request and verify that list and show return its complete non-secret review record without remote cron mutation.

**Acceptance Scenarios**:

1. **Given** a pending request, **When** the operator lists authorizations, **Then** the result includes a bounded summary and no secret-like content.
2. **Given** a request ID, **When** the operator shows it, **Then** the result contains the immutable request fields and audit history.

---

### User Story 2 - Authorize Exactly One Request (Priority: P1)

An operator can explicitly approve one existing pending request after reviewing its scope and exact deployed replay origin.

**Why this priority**: An authenticated operator session is not blanket permission for unattended agent work.

**Independent Test**: Approve a request with confirmation and verify that its state, audit history, and scheduled job prompt contain exactly the approved scope and origin.

**Acceptance Scenarios**:

1. **Given** a pending request, **When** the operator approves it with confirmation, **Then** it becomes approved and the matching catalog-managed job receives a sanitized authorization context.
2. **Given** a missing, expired, or non-pending request, **When** approval is attempted, **Then** the operation fails without changing state or the scheduler.

---

### User Story 3 - Request Bounded Authorization (Priority: P2)

An operator or trusted control-plane workflow can create a structured request for a catalog-managed scheduled job when a human decision is needed.

**Why this priority**: Review and approval are only useful if the decision data is explicit, immutable, and safe to display.

**Independent Test**: Create a request with a valid job, scope, origin, and rationale; verify immutable identity and rejection of unsafe or malformed fields.

**Acceptance Scenarios**:

1. **Given** a catalog-managed job, **When** a valid request is submitted, **Then** it is stored as pending with a stable fingerprint and audit event.
2. **Given** a request containing a credential, invalid origin, unknown job, or unsafe scope, **When** it is submitted, **Then** it is rejected before any remote state is written.

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- A request expires before approval.
- An approved request is replaced by a newer pending request for the same job.
- The scheduled job is absent or is not catalog-managed when approval is attempted.
- A concurrent approval races with another approval or request creation.
- Request rationale or remote output contains secret-like material.

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: System MUST list and show structured authorization requests through `sb hermes` and MCP controls.
- **FR-002**: System MUST allow requests only for catalog-managed scheduled jobs and store an immutable request fingerprint derived from the reviewed fields.
- **FR-003**: System MUST require a valid HTTPS replay origin without credentials, a bounded slug scope, and rationale free of credential-like material.
- **FR-004**: System MUST require an explicit confirmation for approval and allow approval only from the pending state before expiry.
- **FR-005**: System MUST append bounded, secret-screened audit events for request and approval lifecycle actions.
- **FR-006**: System MUST update only the matching scheduled job with its trusted catalog prompt plus the approved request context; it MUST NOT create, remove, or otherwise reconfigure jobs.
- **FR-007**: System MUST reject missing, stale, expired, already-approved, or mismatched requests without changing remote state or scheduler state.

### Key Entities *(include if feature involves data)*

- **Authorization Request**: Immutable operator-review record for one named catalog job, containing scope, canonical replay origin, rationale, fingerprint, creation/expiry timestamps, and lifecycle status.
- **Authorization Audit Event**: Bounded record of request creation, approval, expiry, or supersession linked to a request ID.
- **Approval Context**: Sanitized immutable request fields delivered to exactly one matching scheduled job through its managed prompt.

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: An operator can list and inspect any stored request in one command with only sanitized, structured fields.
- **SC-002**: A confirmed approval updates the corresponding scheduled-job prompt and remote authorization state atomically from the operator’s perspective.
- **SC-003**: Every invalid lifecycle transition is rejected without writing state or changing a cron job.
- **SC-004**: Focused automated tests cover request validation, fingerprinting, expiry, state transitions, prompt delivery, and CLI/MCP forwarding.

## Assumptions

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right assumptions based on reasonable defaults
  chosen when the feature description did not specify certain details.
-->

- The existing configured Sandbox remote is the trusted operator control plane; no multi-user identity system is introduced.
- Authorization expires after 24 hours by default; an optional shorter expiry is accepted.
- The exact replay origin is an HTTPS origin, not a URL containing a path, query, fragment, or credentials.
- Hermes cron supports in-place prompt editing, and only catalog-managed jobs are eligible for authorization context delivery.
