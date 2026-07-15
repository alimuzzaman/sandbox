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

## Dependencies and Execution Order

- T001 before T002–T011.
- T002 before all user stories.
- T003–T005 deliver read-only review independently.
- T006–T008 require T002 and use the validated state model.
- T009–T011 require T002 and may follow T006 because approval relies on request creation.
- T012–T013 follow all implementation tasks.
