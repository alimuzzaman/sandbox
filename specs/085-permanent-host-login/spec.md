# Feature Specification: Permanent hosted WordPress login

**Feature Branch**: `feat/host-autologin-permanent-links`
**Created**: 2026-11-12
**Status**: Specified
**Input**: Reviewed [PRD](prd.md), explicitly requested opt-in permanent links with existing behavior preserved.

## User Scenarios & Testing

### User Story 1 - Existing login stays compatible (Priority: P1)

An existing operator issues a hosted link without selecting permanent mode.

**Independent Test**: The unchanged command returns the same URL format and response fields, respects configured or explicit lifetime, authenticates once, then refuses reuse.

**Acceptance Scenarios**:
1. Given an ordinary deployed target, requesting a link retains the configured expiry and single-use behavior.
2. Local development login rendering and other login providers remain unchanged.

### User Story 2 - Choose permanent access (Priority: P1)

An operator explicitly requests and confirms a permanent WordPress link.

**Independent Test**: Use the link, log out, reuse it, and repeat beyond the ordinary expiry; each valid attempt authenticates the configured user.

**Acceptance Scenarios**:
1. Permanent selection overrides the manifest default lifetime and creates unlimited reusable access.
2. The key never appears in initial or subsequent HTTP request targets. Preview GET/HEAD requests do not log in or consume access.
3. Authentication still requires the valid key and a legitimate same-origin browser submission; wrong keys and foreign/null origins fail.

### User Story 3 - Retain operator control (Priority: P2)

An operator replaces or explicitly removes the installed helper.

**Independent Test**: A prior link fails after replacement; removing the installed helper removes link authentication. Existing WordPress sessions are not claimed to be revoked.

### Edge Cases

- Explicit finite lifetime plus permanent selection: refuse before installation/state mutation.
- Unsupported generic provider/share mode or unrelated host action: refuse permanent selection without changing its current state.
- Unconfirmed issuance, missing target, missing integration, unavailable configured user, malformed key: no authenticated session.
- Removing the manifest declaration alone is not a supported revocation action.

## Requirements

### Functional Requirements

- **FR-001**: Existing default local and hosted login behavior and hosted JSON fields MUST remain compatible.
- **FR-002**: Permanent access MUST require explicit per-issuance selection and confirmation.
- **FR-003**: Permanent links MUST have no expiry/use limit; normal WordPress session lifetime MUST remain unchanged.
- **FR-004**: Explicit lifetime conflicts and unsupported providers/actions MUST fail before mutation; manifest default lifetime MUST not disable permanent mode.
- **FR-005**: The permanent credential MUST stay out of HTTP request targets, logs, retained results and host state; only deliberate foreground issuance may disclose it.
- **FR-006**: GET/HEAD previews, invalid credentials, foreign/null-origin requests and missing users MUST not authenticate.
- **FR-007**: Replacement MUST invalidate the prior link. Explicit helper removal MUST prevent link authentication.
- **FR-008**: Configured target, service, route, user and existing ownership controls MUST be honored.

### Key Entities

- Login policy: ordinary expiring/single-use or explicitly permanent/reusable.
- Installed credential: one current hash for the configured target/user; no plaintext key in retained state.

## Success Criteria

- **SC-001**: All existing targeted login regressions pass without changing expectations for the default path.
- **SC-002**: Two sessions separated by logout can authenticate using one permanent link, including beyond the normal expiry.
- **SC-003**: All invalid/conflicting/unsupported requests are refused without changing active credentials or creating sessions.
- **SC-004**: No permanent key appears in captured request targets or retained non-issuance output.

## Clarifications

Session 2026-11-12: user already confirmed unlimited/no-expiry behavior and preservation of existing defaults. Reviewed PRD resolves finite-default versus explicit lifetime and replacement/removal semantics. No additional user question is needed.
