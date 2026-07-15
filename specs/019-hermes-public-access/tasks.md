# Tasks: Hermes Public Dashboard Access

**Input**: Design documents from `/specs/019-hermes-public-access/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/cli.md`, and `quickstart.md`

**Tests**: Required. The feature controls a privileged network boundary; every
Cloudflare/SSH/service mutation path needs a mocked failure and rollback test before
implementation.

**Organization**: Tasks are grouped by user story. `[P]` tasks modify different files
and can proceed in parallel after their listed prerequisites.

## Phase 1: Setup

**Purpose**: Establish feature documentation and the shared test surface.

- [x] T001 Add test placeholders and shared fake HTTP/SSH helpers in `tests/test_cloudflare_access.py`, `tests/test_cloudflare_tunnel.py`, and `tests/test_hermes.py`.
- [x] T002 [P] Add the public-access operator and recovery sections to `docs/hermes-agent.md`.
- [x] T003 [P] Confirm the current public-exposure architecture link in `specs/016-remote-hermes-agent/plan.md` and `docs/hermes-public-access-prd.md`.

---

## Phase 2: Foundational Security and State

**Purpose**: Create all shared validation, API, state, and result boundaries before any
user-story behavior.

- [x] T004 Add account-level Cloudflare Access application/policy read-validation client in `sandbox/core/_cloudflare_access.py`.
- [x] T005 [P] Add named Tunnel read-validation, token, ingress validation, and user-service rendering client in `sandbox/core/_cloudflare_tunnel.py`.
- [x] T006 Add public-exposure hostname, local configuration references, secret-reference, policy-shape, attach-only ownership, and non-secret state helpers in `sandbox/core/_hermes.py`.
- [x] T007 Add focused foundational unit tests for token redaction, exact hostname/target checks, broad-policy rejection, and state validation in `tests/test_cloudflare_access.py`, `tests/test_cloudflare_tunnel.py`, and `tests/test_hermes.py`.
- [x] T008 Extend the dashboard CLI parser/dispatch with `exposure-status`, `expose`, `unexpose`, and Basic Auth options in `sandbox/cli.py` and `sandbox/commands/hermes.py`.
- [x] T009 Add parser and confirmation-before-remote-access tests in `tests/test_cli.py`.

**Checkpoint**: All invalid, broad, stale, missing-secret, and unconfirmed requests fail
before Cloudflare or SSH operations.

---

## Phase 3: User Story 1 - Plan Protected Public Access (Priority: P1) 🎯 MVP

**Goal**: Produce a deterministic, sanitized, read-only exposure plan with conflict and
rollback information.

**Independent Test**: Mocked Access/Tunnel/remote state returns a stable plan; snapshots
prove no write API or SSH command is issued.

- [x] T010 [P] [US1] Write read-only plan, ownership-conflict, and stale-V2-gate tests in `tests/test_hermes.py`.
- [x] T011 [P] [US1] Write Access application/policy lookup and policy-shape tests in `tests/test_cloudflare_access.py`.
- [x] T012 [P] [US1] Write Tunnel lookup/ingress target tests in `tests/test_cloudflare_tunnel.py`.
- [x] T013 [US1] Implement deterministic public exposure desired-state and conflict planning in `sandbox/core/_hermes.py`.
- [x] T014 [US1] Implement sanitized `dashboard exposure-status` and `dashboard expose --plan` result paths in `sandbox/core/_hermes.py` and `sandbox/commands/hermes.py`.

**Checkpoint**: An operator can inspect `hermes.asb.bd` readiness without mutation.

---

## Phase 4: User Story 2 - Publish an Authenticated Dashboard (Priority: P2)

**Goal**: Confirmed apply owns only the exact Access/Tunnel/DNS/proxy route and leaves
Hermes loopback-only.

**Independent Test**: Mocked apply produces the loopback Caddy fragment and connector
service, rejects anonymous health, and rolls back every injected failure.

- [x] T015 [P] [US2] Write Caddy loopback fragment, connector service, and no-public-listener rendering tests in `tests/test_hermes.py` and `tests/test_cloudflare_tunnel.py`.
- [x] T016 [P] [US2] Write confirmed pre-created Access/Tunnel/DNS validation, connector lifecycle, and reverse-rollback tests in `tests/test_hermes.py`, `tests/test_cloudflare_access.py`, and `tests/test_cloudflare_tunnel.py`.
- [x] T017 [US2] Implement integration-owned remote Caddy fragment install/restore and connector token-file/service lifecycle in `sandbox/core/_hermes.py` and `sandbox/core/_cloudflare_tunnel.py`.
- [x] T018 [US2] Implement explicit-confirm Access/Tunnel/DNS validation, health probes, rollback record persistence, and local expose apply in `sandbox/core/_hermes.py` and `sandbox/core/_cloudflare_access.py`.
- [x] T019 [US2] Extend dashboard diagnostics with distinct dashboard, proxy, Access, tunnel, DNS, and recovery health in `sandbox/core/_hermes.py`.

**Checkpoint**: Confirmed publication is fail-closed, target-limited, and rollback-safe in
mocked tests; no live public route is created by the test suite.

---

## Phase 5: User Story 3 - Add or Rotate a Secondary Access Secret (Priority: P3)

**Goal**: Opt-in Basic Auth is hash-only and can rotate independently.

**Independent Test**: Tests verify disabled/default behavior, verifier-only Caddy
content, rotation, removal, and no tunnel/Access recreation.

- [x] T020 [P] [US3] Write Basic Auth disabled, enabled, rotation, removal, and redaction tests in `tests/test_hermes.py` and `tests/test_cli.py`.
- [x] T021 [US3] Implement secret-reference lookup, remote Argon2id verifier generation, and atomic Basic Auth fragment lifecycle in `sandbox/core/_hermes.py`.
- [x] T022 [US3] Implement protected `dashboard basic-auth set|remove` dispatch and stable errors in `sandbox/cli.py` and `sandbox/commands/hermes.py`.

**Checkpoint**: The secondary credential never appears in state, arguments, logs, or
results and does not substitute for the primary identity policy.

---

## Phase 6: User Story 4 - Recover Safely from Exposure Failure (Priority: P4)

**Goal**: Operators can unexpose, diagnose degradation, and retain SSH-only recovery.

**Independent Test**: Every fault-injected apply/unexpose stage preserves the documented
SSH-forwarded path and leaves no anonymous public endpoint.

- [x] T023 [P] [US4] Add unexpose, degraded-status, rollback-failure, and SSH-recovery tests in `tests/test_hermes.py`.
- [x] T024 [US4] Implement confirmed local unexpose and emergency containment ordering without deleting external Cloudflare resources in `sandbox/core/_hermes.py`.
- [x] T025 [US4] Add public-access status/unexpose result-envelope and no-MCP-mutation tests in `tests/test_cli.py` and `tests/test_mcp.py`.

**Checkpoint**: Removing or recovering public access never destroys Hermes CLI/gateway,
repositories, backups, or loopback/SSH dashboard access.

---

## Phase 7: Polish and Validation

**Purpose**: Finalize documentation, analyze artifacts, and run required checks.

- [x] T026 Update `docs/hermes-agent.md` and `README.md` with plan/review/apply, MFA, optional Basic Auth, SSH fallback, and emergency containment guidance.
- [x] T027 Run focused and full unit suites plus `git diff --check`; record sanitized evidence in `specs/019-hermes-public-access/quickstart.md`.
- [X] T028 Execute the separately approved live remote/edge acceptance only after explicit Cloudflare/VPS authorization; otherwise record it as pending in `specs/019-hermes-public-access/quickstart.md`.

## Dependencies & Execution Order

- Phase 1 starts immediately.
- Phase 2 blocks every user story.
- US1 is the MVP and blocks US2 because apply consumes its desired-state/ownership model.
- US2 blocks US3 and US4 because they operate the owned route.
- US3 and US4 can proceed in parallel after US2 if they do not overlap `_hermes.py`.
- Phase 7 follows all implemented stories. T028 is external and remains pending without
  separate approval; it is not required to run local implementation tests.

## Parallel Opportunities

- T002/T003 may run in parallel with T001.
- T004/T005 and their direct test tasks can run in parallel before T006.
- T010/T011/T012 can run in parallel before T013.
- T015/T016 can run in parallel before T017/T018.
- T020 and T023 can run in parallel after US2.

## Implementation Strategy

1. Complete Phase 2 and verify every unsafe request is rejected before external access.
2. Deliver US1 as a read-only planning MVP.
3. Add US2 only with complete mocked rollback coverage; do not make a live route.
4. Add optional Basic Auth and recovery operations.
5. Complete documentation and local verification. Defer T028 until the operator gives
   explicit, current Cloudflare/VPS authorization.
