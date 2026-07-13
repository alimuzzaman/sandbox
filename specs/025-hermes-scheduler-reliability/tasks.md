# Tasks: Reliable Hermes Scheduled Work

**Input**: Design documents from `specs/025-hermes-scheduler-reliability/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: Required by FR-018 and SC-008. Add behavior tests before each implementation slice and record the failing and passing evidence.

## Phase 1: Setup and Evidence Baseline

**Purpose**: Preserve the observed failure state and establish the feature skeleton without mutating the remote.

- [X] T001 Record the sanitized local and remote baseline, current five-job inventory, gateway ownership conflict, request rejection, and dirty worktrees in `specs/025-hermes-scheduler-reliability/implementation-evidence.md`
- [X] T002 [P] Add the desired catalog and committed-script directory skeleton at `sandbox/hermes/cron-catalog.json` and `sandbox/hermes/cron_scripts/`
- [X] T003 [P] Add scheduler reliability test fixtures for nominal success, false success, gateway conflict, and dirty worktrees in `tests/fixtures/hermes/`

---

## Phase 2: Foundational Scheduler Domain

**Purpose**: Implement deterministic models and validation shared by every user story.

- [X] T004 Add failing tests for desired/observed cron entities, execution classification, route separation, catalog fingerprints, and safe target validation in `tests/test_hermes.py`
- [X] T005 Implement desired cron entries, observed evidence, verified outcomes, gateway ownership state, and worktree evidence in `sandbox/hermes/scheduler.py`
- [X] T006 Implement strict catalog loading and validation in `sandbox/hermes/scheduler.py`, including duplicate names, unsafe paths, missing scripts, unsupported profiles, and model identifiers containing effort suffixes
- [X] T007 Define the reviewed desired inventory in `sandbox/hermes/cron-catalog.json`, omitting obsolete paused work, separating monitor scripts from agent work, and including one Terra/Medium `sandbox-approved-spec-task` worker that executes at most one explicitly selected unchecked Spec-Kit task in an isolated worktree without commit/push authority while Luna remains read-only
- [X] T008 [P] Implement non-self-mutating TODO monitoring with truthful exits in `sandbox/hermes/cron_scripts/todo_md_monitor.py`
- [X] T009 [P] Implement truthful quota requeue inspection with nonzero operational failures in `sandbox/hermes/cron_scripts/codex_quota_requeue.py`
- [X] T010 [P] Implement truthful Lenzora dispatch with bounded timeout and nonzero operational failures in `sandbox/hermes/cron_scripts/lenzora_kanban_dispatch.py`
- [X] T011 Run the foundational test slice and record exact results in `specs/025-hermes-scheduler-reliability/implementation-evidence.md`

**Checkpoint**: The desired state and all scheduler decisions are deterministic and independently testable.

---

## Phase 3: User Story 1 — Truthful Scheduled-Work Health (Priority: P1)

**Goal**: Report what Hermes actually did, including false-green failures and ownership conflicts.

**Independent Test**: A fixture with nominal success plus a newer provider rejection is degraded and identifies the contradiction without exposing prompts or secrets.

- [X] T012 [P] [US1] Add failing health, redaction, bounded-evidence, and false-success precedence tests in `tests/test_hermes.py`
- [X] T013 [US1] Implement bounded cron/run/request evidence collection and false-success evaluation in `sandbox/core/_hermes.py` and `sandbox/hermes/scheduler.py`
- [X] T014 [US1] Implement aggregate gateway, scheduler, cron, and worktree health in `sandbox/core/_hermes.py`
- [X] T015 [P] [US1] Add `hermes health` CLI arguments and JSON contract tests in `sandbox/cli.py`, `sandbox/commands/hermes.py`, and `tests/test_cli.py`
- [X] T016 [P] [US1] Add `hermes_health` MCP parity and tests in `mcp/wp-server/tools/hermes.py` and `tests/test_mcp.py`
- [X] T017 [US1] Run the US1 test slice and verify all returned evidence is bounded and secret-safe

**Checkpoint**: Health distinguishes idle monitors, real work, provider rejection, false success, and gateway conflict.

---

## Phase 4: User Story 2 — Reconcile a Known Cron Catalog (Priority: P1)

**Goal**: Preview and then converge any inventory onto the committed desired catalog.

**Independent Test**: A drifted five-job fixture previews exact replacement, confirmed apply produces one copy of every desired entry, and a second apply is a no-op.

- [X] T018 [P] [US2] Add failing catalog rendering, exact-plan, confirmation, partial-failure, and idempotency tests in `tests/test_hermes.py`
- [X] T019 [US2] Implement deterministic reconciliation planning and catalog fingerprint comparison in `sandbox/hermes/scheduler.py`
- [X] T020 [US2] Implement remote script installation, protected inventory backup, remove-all/create-all apply, route verification, and partial-result reporting in `sandbox/core/_hermes.py`
- [X] T021 [P] [US2] Add cron catalog/reconcile CLI contracts and tests in `sandbox/cli.py`, `sandbox/commands/hermes.py`, and `tests/test_cli.py`
- [X] T022 [P] [US2] Add cron catalog/reconcile MCP parity and tests in `mcp/wp-server/tools/hermes.py` and `tests/test_mcp.py`
- [X] T023 [US2] Run the US2 test slice and prove preview is side-effect free and repeat apply is converged

**Checkpoint**: Cron state is fully reproducible from committed Sandbox configuration.

---

## Phase 5: User Story 3 — Prove a Coding Job Can Work (Priority: P1)

**Goal**: Wait for evidence-backed completion and never confuse launch with success.

**Independent Test**: Verified execution detects run-marker transition, terminal evidence, timeout, no-work, and metadata/error disagreement.

- [X] T024 [P] [US3] Add failing verified-run transition, timeout, no-work, provider-rejection, and contradiction tests in `tests/test_hermes.py`
- [X] T025 [US3] Implement bounded trigger/poll/evidence verification in `sandbox/core/_hermes.py`
- [X] T026 [P] [US3] Add verified-run CLI and MCP contracts with timeout and confirmation in `sandbox/cli.py`, `sandbox/commands/hermes.py`, `mcp/wp-server/tools/hermes.py`, `tests/test_cli.py`, and `tests/test_mcp.py`
- [X] T027 [US3] Run the US3 test slice and prove an asynchronous trigger acknowledgement alone cannot yield success

**Checkpoint**: A scheduled agent run has an inspectable terminal result or an actionable failure.

---

## Phase 6: User Story 4 — Keep One Gateway Owner (Priority: P2)

**Goal**: Make `hermes-gateway-sandbox.service` the stable sole scheduler owner.

**Independent Test**: A manual process plus restarting legacy unit previews the required actions; confirmed convergence leaves one stable managed owner.

- [X] T028 [P] [US4] Add failing gateway conflict, preview, confirmation, idempotency, and stability-window tests in `tests/test_hermes.py`
- [X] T029 [US4] Implement gateway ownership discovery and deterministic convergence in `sandbox/core/_hermes.py`, stopping/disabling legacy ownership before starting the Sandbox unit
- [X] T030 [P] [US4] Add gateway convergence CLI and MCP parity with tests in `sandbox/cli.py`, `sandbox/commands/hermes.py`, `mcp/wp-server/tools/hermes.py`, `tests/test_cli.py`, and `tests/test_mcp.py`
- [X] T031 [US4] Update `scripts/install-remote.sh` and the existing Hermes update/restore integration in `sandbox/core/_hermes.py` so fresh setup, update, and recovery install the same managed gateway ownership and committed cron-script contract without implicitly applying destructive reconciliation

**Checkpoint**: Repeated health checks show one owner and no restart-counter growth.

---

## Phase 7: User Story 5 — Preserve Agent Work Before Cleanup (Priority: P2)

**Goal**: Inventory and preserve every dirty Hermes worktree without force-committing invalid work.

**Independent Test**: Dirty, detached, clean, and invalid worktrees are classified; destructive cleanup remains blocked until each dirty tree is shipped or explicitly retained.

- [X] T032 [P] [US5] Add failing managed-repository/worktree inventory, redaction, and cleanup-block tests in `tests/test_hermes.py`
- [X] T033 [US5] Implement bounded repository/worktree inventory and preservation disposition in `sandbox/core/_hermes.py`
- [X] T034 [P] [US5] Add worktree-list CLI and MCP parity with tests in `sandbox/cli.py`, `sandbox/commands/hermes.py`, `mcp/wp-server/tools/hermes.py`, `tests/test_cli.py`, and `tests/test_mcp.py`
- [X] T035 [US5] Review the remote Sandbox, recovery, Lenzora, and smoke worktrees against their task scopes and repository instructions; ship only validated changes and record retained work in `specs/025-hermes-scheduler-reliability/implementation-evidence.md`

**Checkpoint**: Every observed dirty worktree has a reviewable disposition and none is silently deleted.

---

## Phase 8: Integration, Documentation, and Live Convergence

**Purpose**: Complete fresh-server parity, verify locally, then perform the explicitly authorized remote replacement.

- [X] T036 [P] Update the operator runbook, failure semantics, desired-job rationale, recovery, and fresh-server commands in `docs/hermes-agent.md`
- [X] T037 [P] Update Sandbox setup/restore documentation and command help for catalog and gateway convergence in relevant `README.md`, `AGENTS.md`, and command help files
- [X] T038 Run focused scheduler, CLI, and MCP tests; then run the full test suite and `./sb selftest`, recording commands and results in `specs/025-hermes-scheduler-reliability/implementation-evidence.md`
- [X] T039 Perform a fresh independent review for correctness, security/redaction, destructive-action gates, CLI/MCP parity, and spec/task completeness; resolve every material finding
- [X] T040 Commit and push validated Sandbox changes to the current explicit branch, preserving unrelated local files
- [X] T041 Synchronize the remote Sandbox checkout through the Sandbox-managed update path and verify its commit matches the pushed branch
- [X] T042 Run read-only remote health, worktree inventory, gateway convergence preview, and cron reconciliation preview; retain sanitized evidence
- [X] T043 Run confirmed gateway convergence and verify one stable scheduler owner over the required observation window
- [X] T044 Back up the remote cron inventory, remove every existing job, recreate the complete reviewed catalog, and report exact partial state if any step fails
- [X] T045 Trigger the harmless acceptance job with verified execution, confirm evidence-backed terminal behavior, and rerun reconciliation to prove zero drift
- [ ] T046 Complete `implementation-evidence.md`, mark implemented tasks, rerun Spec-Kit analysis/convergence, and report residual risks and learning delta

---

## Phase 9: User Story 6 — Reuse Secure Remote Connections (Priority: P2)

**Goal**: Reuse authenticated transport setup for sequential Sandbox remote operations without combining command authority or replaying mutations.

**Independent Test**: Three harmless live checks reuse one endpoint-hashed connection after the first call, retain independent outcomes, and direct mode remains available when local control state cannot be prepared.

- [X] T047 [P] [US6] Add SSH, SCP, and Git transport tests for owner-only control state, endpoint isolation, custom ports, pre-launch fallback, and no replay in `tests/test_remote.py`
- [X] T048 [US6] Implement bounded opportunistic connection reuse for shared SSH and SCP operations in `sandbox/core/_remote.py`
- [X] T049 [US6] Apply the same transport policy to direct VPS deploy pushes in `sandbox/core/_remote.py` without affecting unrelated Git remotes
- [X] T050 [P] [US6] Document transport lifetime, batching boundaries, security behavior, and live timing evidence in `docs/remote-hosting.md`, `docs/hermes-agent.md`, and `specs/025-hermes-scheduler-reliability/implementation-evidence.md`

---

## Dependencies and Execution Order

- Phase 1 preserves the baseline before any mutation.
- Phase 2 blocks all user stories.
- US1 health is required before live reconciliation so failures remain visible.
- US2 reconciliation and US3 verified execution are independent after the foundation, but both must pass before live replacement.
- US4 convergence must complete before cron acceptance because the gateway owns scheduler ticks.
- US5 inventory must complete before any worktree cleanup or shipping decision.
- Phase 8 begins only after all local behavior and contract tests pass.

## Parallel Opportunities

- T002 and T003 can proceed independently.
- T008–T010 touch independent committed scripts.
- Within each story, pure/CLI/MCP tests marked `[P]` can be prepared independently, but one owner integrates shared files.
- Documentation T036–T037 can proceed after contracts stabilize.

## Implementation Strategy

1. Build and test the pure scheduler policy first.
2. Deliver truthful read-only health before adding mutations.
3. Add preview/apply and verified-run controls behind explicit confirmation.
4. Add gateway and worktree safeguards.
5. Verify every local contract, review remote work, then perform the authorized remote convergence once through the new Sandbox commands.
