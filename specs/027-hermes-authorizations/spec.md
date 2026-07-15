# Feature Specification: Hermes Authorization Controls

**Feature Branch**: `codex/hermes-public-access`

**Created**: 2026-07-15

**Status**: Approved for implementation

**Input**: User description: "Add tools to review and authorize Hermes blocked work with immutable scoped approvals, audit evidence, expiration, and scheduler integration."

## Clarifications

### Session 2026-07-16

- Q: How should consistency between authorization state and the scheduler prompt be guaranteed? → A: Commit the authorization state with a compare-and-swap before prompt delivery; on prompt failure, restore the prior state with a second compare-and-swap, and reject competing writers before prompt mutation.

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

No additional user stories are required; review, approval, and request creation cover the
supported operator and trusted-workflow journeys.

### Edge Cases

- A request expires before approval; listing/show reports it as expired, approval is rejected,
  and the expiry audit event is recorded at most once.
- An approved request is replaced by a newer pending request for the same job; the older request
  becomes superseded and no more than one approved request remains active for that job.
- The scheduled job is absent, duplicated, disabled, or not catalog-managed when approval is
  attempted; approval is rejected before state or scheduler mutation.
- A concurrent approval or request creation races with another writer; exactly one state CAS may
  succeed, a losing approval returns `state_conflict` before prompt mutation, and audit records
  from the winning transition are retained.
- Prompt delivery fails after state commit; a second CAS restores the prior authorization state,
  and failure is reported as retryable rather than leaving an approved state without its context.
- Request rationale or remote output contains secret-like material; the request is rejected or
  the output is withheld/redacted before it reaches operator-facing output.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST list and show structured authorization requests through `sb hermes` and MCP controls.
- **FR-002**: System MUST allow requests only for catalog-managed scheduled jobs and store an immutable request fingerprint derived from the reviewed fields.
- **FR-003**: System MUST require a valid HTTPS replay origin without credentials, a bounded slug scope, and rationale free of credential-like material.
- **FR-004**: System MUST require an explicit confirmation for approval and allow approval only from the pending state before expiry.
- **FR-005**: System MUST append bounded, secret-screened audit events for request and approval lifecycle actions, retaining no more than 200 audit events and no more than 100 stored requests.
- **FR-006**: System MUST update only the matching scheduled job with its trusted catalog prompt plus the approved request context; it MUST NOT create, remove, or otherwise reconfigure jobs.
- **FR-007**: System MUST reject missing, stale, expired, already-approved, or mismatched requests without changing remote state or scheduler state.

### Key Entities *(include if feature involves data)*

- **Authorization Request**: Immutable operator-review record for one named catalog job, containing scope, canonical replay origin, rationale, fingerprint, creation/expiry timestamps, and lifecycle status.
- **Authorization Audit Event**: Bounded record of request creation, approval, expiry, or supersession linked to a request ID.
- **Approval Context**: Sanitized immutable request fields delivered to exactly one matching scheduled job through its managed prompt.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In every verified list/show run, an operator can inspect a stored request in one command with only sanitized, structured fields and no prompt body or credential-like value.
- **SC-002**: In every verified approval race, the winning state transition is CAS-committed before prompt delivery; a losing writer performs zero prompt mutations, and a prompt-delivery failure restores the prior state with a second CAS.
- **SC-003**: In every verified invalid lifecycle transition, the operation returns a stable error and performs zero state writes and zero cron mutations.
- **SC-004**: The focused automated suite covers request validation, fingerprinting, expiry, state transitions, concurrency conflict, prompt rollback, and CLI/MCP forwarding; the repository suite remains green.

## Assumptions

- The existing configured Sandbox remote is the trusted operator control plane; no multi-user identity system is introduced.
- Authorization expires after 24 hours by default; an optional shorter expiry is accepted.
- The exact replay origin is an HTTPS origin, not a URL containing a path, query, fragment, or credentials.
- Hermes cron supports in-place prompt editing, and only catalog-managed jobs are eligible for authorization context delivery.
- Scheduler state and authorization state are separate remote resources; consistency is therefore defined as CAS-guarded state-first commit with compensating rollback, not an unavailable distributed transaction.
- Live deployment and acceptance of the Lenzora companion remain separately protected operational work and are not implied by fixture or read-only evidence.
