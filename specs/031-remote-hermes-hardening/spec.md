# Feature Specification: Remote and Hermes Operations Hardening

**Feature Branch**: `031-remote-hermes-hardening`

**Created**: 2026-07-18

**Status**: Implemented

**Input**: User description: "Implement the Remote and Hermes Operations Hardening PRD: secure remote MCP service ownership and secret handling, truthful remote and Hermes health, transactional cron reconciliation with safe rollback, and terminal result classification. Keep all remote mutations explicitly confirmation-gated."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Safely operate a remote MCP service (Priority: P1)

An operator can inspect, plan, install, start, stop, and recover the remote MCP
control plane as a Sandbox-owned service without exposing its credential or affecting
another process on the remote host.

**Why this priority**: The control plane must be safe before it can be trusted to
operate remote instances or Hermes.

**Independent Test**: A disposable remote-service fixture can prove that status and
the migration plan do not write state, a confirmed install creates one owned service,
and stopping it leaves an unrelated HTTP fixture running.

**Acceptance Scenarios**:

1. **Given** a registered remote with a legacy detached MCP process, **When** an
   operator requests a migration plan, **Then** the response identifies the legacy
   state and intended recovery service without changing the remote.
2. **Given** an approved, eligible migration plan, **When** an operator explicitly
   confirms it, **Then** the remote gains one owned, reboot-recoverable MCP service
   whose credential is not visible in commands, service text, output, or metadata.
3. **Given** the owned MCP service and an unrelated streamable-HTTP process,
   **When** the operator stops the selected remote service, **Then** only the owned
   service stops and the unrelated process remains alive.

---

### User Story 2 - See truthful operations health (Priority: P1)

An operator can use one read-only health result to distinguish an operational remote,
a recoverability gap, a scheduler/catalog problem, and managed-worktree hygiene
problems without exposing credentials or prompt contents.

**Why this priority**: Accurate diagnosis prevents unsafe repair attempts and makes
the current remote state actionable.

**Independent Test**: Sanitized fixtures independently simulate disabled recovery,
missing scheduler, catalog drift, dirty worktrees, and an operational service, each
yielding its own stable reason code.

**Acceptance Scenarios**:

1. **Given** a service that is active but not enabled for post-reboot recovery,
   **When** health is requested, **Then** it is degraded with a distinct recovery
   reason rather than reported healthy.
2. **Given** a healthy gateway but an unavailable scheduler, **When** health is
   requested, **Then** the response reports the scheduler as unavailable while
   preserving the gateway evidence.
3. **Given** legacy or dirty managed state, **When** health is requested, **Then** it
   reports the separate catalog and worktree reasons with bounded, redacted evidence.

---

### User Story 3 - Reconcile Hermes cron without losing the previous state (Priority: P2)

An operator can understand legacy cron drift, review an exact replacement plan, and,
when separately approved, run a protected reconciliation that either verifies the
desired catalog or restores the previous scheduler inventory.

**Why this priority**: The scheduler currently has legacy, fingerprintless state;
the safe path must not make autonomous work active by accident.

**Independent Test**: A fake scheduler proves the no-write legacy plan, successful
exact convergence, and recovery after an injected post-removal failure.

**Acceptance Scenarios**:

1. **Given** fingerprintless legacy jobs, **When** reconciliation is requested
   without confirmation, **Then** it returns a non-mutating blocked result that
   explains why replacement approval is needed.
2. **Given** an approved exact-replacement request, **When** preflight and
   postcondition checks pass, **Then** the catalog has exactly the desired controlled
   entries and no duplicates.
3. **Given** a failure after old jobs are removed, **When** reconciliation handles the
   failure, **Then** it restores and verifies the prior inventory or reports a
   specific rollback failure with bounded evidence.

---

### User Story 4 - Classify terminal agent results correctly (Priority: P2)

An operator can tell the difference between a provider/work failure and a documented,
successful terminal result that an upstream wrapper incorrectly labels as an error.

**Why this priority**: The observed `COMPLETED_SPEC_TASK` result must not be mistaken
for a failed task or provider outage.

**Independent Test**: Sanitized job-output fixtures cover documented terminal markers,
provider rejection, malformed output, and a missing terminal transition.

**Acceptance Scenarios**:

1. **Given** a documented terminal success marker followed by a wrapper error,
   **When** verification evaluates the run, **Then** it reports a successful terminal
   result with a protocol-classification note.
2. **Given** provider authentication or rate-limit evidence, **When** a nominal marker
   is also present, **Then** verification reports the provider failure.
3. **Given** malformed or incomplete terminal evidence, **When** verification runs,
   **Then** it does not infer success.

### Edge Cases

- The remote does not support the chosen recovery-service capability or its user
  manager is unavailable: planning reports an actionable, non-mutating incompatibility.
- Ownership facts do not match the selected remote: lifecycle commands refuse the
  mutation and do not use process-name scanning as a fallback.
- Credential material is missing, overly permissive, or mismatched: the service is
  not started and diagnostic output remains redacted.
- A reconciliation snapshot cannot be saved, a scheduler query times out, or restore
  fails: the operation stops or reports bounded recovery evidence without triggering
  a job.
- A terminal marker appears with an explicit provider/client rejection or missing
  transition: the failure wins and is reported distinctly.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST model the remote MCP server as an owned service with
  a unique, non-secret ownership record for the selected remote.
- **FR-002**: The system MUST keep a remote MCP bearer credential out of command
  arguments, process lists, service definitions, managed metadata, result envelopes,
  and documentation examples.
- **FR-003**: The system MUST reject a remote service configuration that would bind a
  protected control-plane listener to a wildcard or public address.
- **FR-004**: The system MUST require explicit confirmation before installing,
  migrating, restarting, stopping, creating credentials for, or otherwise changing a
  remote MCP service.
- **FR-005**: The system MUST stop or restart only the service proven to be owned by
  the selected remote; ambiguous ownership MUST return a non-mutating error.
- **FR-006**: The system MUST expose read-only remote service status including
  installation, enablement, activity, listener, ownership, and recovery readiness,
  with all sensitive values redacted.
- **FR-007**: The system MUST report remote MCP recovery, gateway ownership, user
  recovery support, scheduler availability, cron catalog state, terminal-result
  classification, stale sessions, and dirty managed worktrees as separate health
  facts and stable reason codes.
- **FR-008**: Health MUST be degraded whenever required recovery or scheduler evidence
  is absent, failed, stale, or unknown; unknown evidence MUST NOT be treated as healthy.
- **FR-009**: The system MUST preserve the existing fail-closed behavior for legacy or
  fingerprintless cron state and return an exact, read-only replacement plan.
- **FR-010**: A confirmed force replacement MUST preflight dependencies, retain a
  protected snapshot of the prior cron inventory, verify exact desired convergence,
  and restore the prior inventory when a post-removal step fails.
- **FR-011**: The system MUST report whether cron reconciliation completed, rolled
  back, or could not roll back, along with bounded, redacted evidence.
- **FR-012**: The system MUST classify only the documented terminal-result grammar as
  successful after a valid transition; provider/client rejection, malformed state,
  or missing transition MUST remain failures.
- **FR-013**: The system MUST keep existing local MCP and compatible remote CLI
  behavior available during the migration period, while retiring unsafe process-wide
  stop behavior for migrated services.
- **FR-014**: The system MUST provide user-facing guidance for all new planning,
  confirmation, health, rollback, and recovery states.

### Key Entities *(include if feature involves data)*

- **Remote Service Record**: Non-secret identity, expected listener, ownership marker,
  and recovery facts for one selected remote service.
- **Service Ownership Proof**: Evidence that an observed process belongs to the
  selected Sandbox-owned service and no other remote.
- **Component Health Fact**: A time-bounded status, reason code, and redacted evidence
  for one remote or Hermes dependency.
- **Cron Reconciliation Transaction**: An exact desired plan, protected prior-state
  snapshot, changes performed, postconditions, and rollback outcome.
- **Terminal Result Classification**: The observed terminal marker, transition and
  provider evidence, and resulting success or failure decision.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In all automated remote-service fixtures, 100% of lifecycle mutations
  target only the selected owned service; the unrelated fixture survives every stop test.
- **SC-002**: Automated credential-exposure scans find zero bearer values in command
  arguments, rendered service definitions, managed metadata, or returned output.
- **SC-003**: Health tests produce a distinct documented reason code for each of the
  five conditions: recovery disabled, remote service unavailable, scheduler
  unavailable, catalog drift, and dirty managed worktree.
- **SC-004**: In reconciliation tests, every injected failure after scheduler removal
  restores the exact original inventory or returns an explicit `rollback_failed` state.
- **SC-005**: A converged catalog has exactly the desired controlled jobs, no duplicate
  names, and produces zero creates or removals when reconciled again.
- **SC-006**: Terminal-result tests recognize all approved grammar fixtures while 100%
  of provider rejection, malformed output, and missing-transition fixtures remain failures.
- **SC-007**: The focused test suite, full applicable test suite, CLI contracts, and
  documentation checks pass without secret material in captured output or committed files.
- **SC-008**: On an explicitly approved disposable remote, a reboot-recovery acceptance
  check shows the owned MCP service return without interactive login and no public
  listener exposure.

## Assumptions

- The first implementation uses the host's supported user-service environment file
  mechanism with owner-only permissions; a system credential mechanism may replace it
  later only with equivalent secrecy and test coverage.
- The acceptance remote is disposable and is not automatically converged; a live
  migration, linger enablement, scheduler reconciliation, or job trigger still needs
  separate current operator approval.
- The documented terminal-result grammar initially consists of `COMPLETED_SPEC_TASK`,
  `COMPLETED_TODO_TASK`, `NO_BACKLOG_WORK`, and `REVIEW_REQUIRED` and is versioned in
  one testable policy location.
- Existing remote metadata and local stdio MCP workflows remain compatible unless a
  protected migration has completed.
- Any live acceptance that changes a remote uses a disposable host, explicit
  confirmation, and separately captured before/after evidence.
