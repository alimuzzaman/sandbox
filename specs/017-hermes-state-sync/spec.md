# Feature Specification: Hermes State Sync

**Feature Branch**: `017-hermes-state-sync`

**Created**: 2026-07-11

**Status**: Ready for planning

**Input**: User description: "Update Hermes setup tools so Hermes/Sandbox automatically update its sanitized harness and memory state to a private GitHub repository for rebuilds."

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

### User Story 1 - Rebuildable State (Priority: P1)

As the operator of a remote Hermes installation, I want setup to restore the latest safe harness and memory state from my private state repository so a lost server can be rebuilt without losing operating context.

**Why this priority**: Recovery after remote loss is the primary reason for the repository.

**Independent Test**: Seed a private state repository, run setup on a clean remote, and verify the expected non-secret profile and memory files are restored while provider login remains required.

**Acceptance Scenarios**:

1. **Given** a reachable private state repository, **When** Hermes setup runs, **Then** the latest validated state revision is restored before the agent starts.
2. **Given** no repository credentials on a clean remote, **When** setup completes, **Then** setup reports that operator authentication is still required and does not fabricate credentials.

---

### User Story 2 - Publish State Changes (Priority: P2)

As the operator, I want completed harness and memory changes to be published back to the private repository so future rebuilds contain the latest state.

**Why this priority**: A restore source is useful only if it remains current.

**Independent Test**: Modify an allowed state file, run the sync command, and verify one new commit containing only the allowed files.

**Acceptance Scenarios**:

1. **Given** a configured state repository and changed allowed files, **When** sync runs, **Then** it commits and pushes a sanitized state snapshot.
2. **Given** no allowed changes, **When** sync runs, **Then** it exits successfully without creating an empty commit.

---

### User Story 3 - Secret-Safe Boundaries (Priority: P3)

As the operator, I want state synchronization to reject credentials and runtime data so the backup repository cannot become a secret store.

**Why this priority**: Hermes uses OAuth and Git credentials that must remain in approved remote secret stores.

**Independent Test**: Place credential-like files and token-shaped content in the remote Hermes home, run sync, and verify they are excluded or cause a safe refusal before push.

**Acceptance Scenarios**:

1. **Given** an auth/session/database/log path, **When** sync runs, **Then** it is excluded and the result identifies the exclusion without revealing contents.
2. **Given** a secret-like value in an allowed text file, **When** sync runs, **Then** the push is refused and no remote commit is created.

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- What happens when the repository is unreachable? Setup must fail closed before replacing local state and sync must leave the working tree unchanged.
- What happens when the repository has conflicting changes? The tool must refuse automatic merge and report the conflicting revision.
- What happens when the remote has no memory files? Setup succeeds with an empty memory set.
- What happens when a state file is a symlink or path traversal target? It is excluded and reported.

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: System MUST support a configured private GitHub state repository per remote.
- **FR-002**: Setup MUST restore only a documented allowlist of non-secret Hermes harness, profile, memory, and Sandbox metadata files.
- **FR-003**: Setup MUST preserve provider authentication, Git credentials, cookies, private keys, sessions, checkpoints, databases, logs, worktrees, and runtime binaries on the remote only.
- **FR-004**: System MUST provide an explicit sync operation that commits and pushes changed allowed files with a stable manifest and source revision.
- **FR-005**: System MUST provide an explicit restore operation that validates the repository revision and refuses conflicts or unsafe paths before mutation.
- **FR-006**: System MUST scan allowed text content for credential-like values and refuse publication when detected.
- **FR-007**: System MUST use atomic staging and replacement so a failed restore leaves the existing remote state intact.
- **FR-008**: System MUST report repository revision, changed paths, exclusions, and authentication-required status without printing secrets.
- **FR-009**: Setup MUST remain usable when the state repository is not configured, preserving current behavior with a clear status.

*Example of marking unclear requirements:*

- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified - email/password, SSO, OAuth?]
- **FR-007**: System MUST retain user data for [NEEDS CLARIFICATION: retention period not specified]

### Key Entities *(include if feature involves data)*

- **State Repository**: A private Git repository identified by owner/name or URL and associated with one configured remote.
- **State Manifest**: A versioned document listing the allowed files, excluded classes, source remote, and snapshot revision.
- **Sanitized Snapshot**: An immutable set of validated non-secret harness and memory files used for restore.

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: A clean remote can restore the latest snapshot and reach configured Hermes state without restoring any provider credential.
- **SC-002**: A no-change sync creates zero new commits; a changed allowed file creates exactly one commit.
- **SC-003**: Test fixtures containing credential, session, database, log, and binary paths produce zero published copies of those paths.
- **SC-004**: Existing Hermes setup and operation remain backward compatible when no state repository is configured.

## Assumptions

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right assumptions based on reasonable defaults
  chosen when the feature description did not specify certain details.
-->

- The operator owns the private repository and has GitHub Contents read/write access without organization access.
- Provider OAuth and Git credentials remain operator-managed and must be reauthenticated after a loss.
- Default synchronization is explicit and setup-triggered; unattended cron scheduling is out of scope for v1.
- The existing named-remote SSH abstraction and GitHub CLI authentication are reused.
