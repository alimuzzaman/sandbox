# Tasks: Hermes Authorization Controls

**Input**: Design documents from `/specs/027-hermes-authorizations/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli-mcp.md

## Phase 1: Foundation

- [X] T001 Add failing authorization lifecycle, validation, audit, and prompt-delivery tests in `tests/test_hermes.py`.
- [X] T002 Add state migration and bounded authorization collection helpers in `sandbox/core/_hermes.py`.

## Phase 2: User Story 1 — Review a Pending Authorization (P1)

**Goal**: Operators can list and show sanitized authorization records.

**Independent Test**: A test fixture with pending records returns bounded summaries and full request/audit detail without scheduler mutation.

- [X] T003 [US1] Implement read-only `authorization_list` and `authorization_show` facade operations in `sandbox/core/_hermes.py`.
- [X] T004 [US1] Add `sb hermes authorization list|show` parsing and dispatch in `sandbox/commands/hermes.py`.
- [X] T005 [US1] Add `hermes_authorization_list` and `hermes_authorization_show` MCP wrappers in `mcp/wp-server/tools/hermes.py`.

## Phase 3: User Story 2 — Authorize Exactly One Request (P1)

**Goal**: A confirmed approval updates only its matching catalog-managed job with trusted context.

**Independent Test**: A pending fixture is approved once; state/audit change and one bounded `cron edit --prompt` call is asserted.

- [X] T006 [US2] Implement validated approval transition, expiry enforcement, audit append, and catalog-prompt rendering in `sandbox/core/_hermes.py`.
- [X] T007 [US2] Add `sb hermes authorization approve` confirmation handling in `sandbox/commands/hermes.py`.
- [X] T008 [US2] Add `hermes_authorization_approve` MCP wrapper in `mcp/wp-server/tools/hermes.py`.

## Phase 4: User Story 3 — Request Bounded Authorization (P2)

**Goal**: Operators can submit immutable, reviewable requests for a managed job.

**Independent Test**: Valid requests persist; malformed jobs, origins, scopes, and credential-like rationales fail before writes.

- [X] T009 [US3] Implement request validators, fingerprinting, supersession, and request creation in `sandbox/core/_hermes.py`.
- [X] T010 [US3] Add `sb hermes authorization request` input validation and dispatch in `sandbox/commands/hermes.py`.
- [X] T011 [US3] Add `hermes_authorization_request` MCP wrapper in `mcp/wp-server/tools/hermes.py`.

## Phase 5: Documentation and Verification

- [X] T012 Document request, review, approval, expiry, and scheduler behavior in `docs/hermes-agent.md`.
- [X] T013 Run focused tests and `./sb hermes authorization list --remote scaleway-sandbox --json`; record sanitized evidence in `specs/027-hermes-authorizations/quickstart.md`.
- [X] T014 Add `authorization sync` to convert terminal `REVIEW_REQUIRED` outputs into review-only drafts and verify it against the active Lenzora job.

## Phase 6: Convergence

- [X] T015 Replace unavailable Hermes output synchronization with a catalog-templated local companion that an eligible cron can use to create only a pending authorization request (FR-002, FR-003, FR-005; partial).
- [X] T016 Make the dashboard review-and-approve only: remove manual request and output-sync controls, make lifecycle state and expiry clear, and require the dialog to restate the exact pending scope, origin, and job (US1/AC1, US2/AC1; partial).
- [X] T017 Configure the Lenzora catalog job to invoke the bounded companion only for its configured authorization template; deploy, reconcile, and verify that it creates a pending request without approval or production access (FR-004, FR-006, FR-007; missing).
- [X] T018 Verify the repaired Hermes upstream remote can fetch the signed update history without updating the installed checkout, and record the operator workflow (plan: live proof; missing).

## Phase 7: Convergence

- [X] T019 Enforce approved authorization expiry by delivering an expiry-bearing prompt and running a bounded revoker that restores the catalog prompt (FR-004, FR-006, FR-007; partial).

## Phase 8: Convergence

- [X] T020 Ensure approval replacement leaves at most one active approved request per catalog job (FR-005, FR-007; partial).

## Dependencies and Execution Order

- T001 before T002–T011.
- T002 before all user stories.
- T003–T005 deliver read-only review independently.
- T006–T008 require T002 and use the validated state model.
- T009–T011 require T002 and may follow T006 because approval relies on request creation.
- T012–T013 follow all implementation tasks.

## Phase 9: Convergence

- [ ] T021 [US2] Perform the missing live Lenzora catalog-companion deployment, reconciliation, and pending-request acceptance check from T017 with explicit operator authorization; verify no approval, production access, or scheduler reconfiguration occurs (FR-004, FR-006, FR-007; missing).
- [ ] T022 [P] Verify the repaired Hermes upstream remote can fetch signed update history without changing the installed checkout, and record the bounded operator procedure (plan: live proof; missing).
- [ ] T023 Add a concurrency regression and guarded state-transition mechanism proving competing authorization approvals cannot lose audit/state updates or leave a mismatched cron prompt (FR-007; partial).
- [ ] T024 Resolve the remaining Spec-Kit placeholder sections in `spec.md` through the canonical specify/clarify workflows, including measurable edge-case acceptance criteria and assumptions (spec quality; partial).
- [ ] T025 Refresh `quickstart.md` with current focused/full test counts and clearly separate fixture evidence from unperformed live acceptance (SC-004; partial).
