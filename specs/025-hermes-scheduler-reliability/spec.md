# Feature Specification: Reliable Hermes Scheduled Work

**Feature Branch**: `codex/hermes-public-access`

**Created**: 2026-07-13

**Status**: Approved for implementation

**Input**: User description: "Deeply review why Hermes scheduled work does nothing, solve every discovered scheduler and gateway problem, recreate all cron jobs through repeatable Sandbox tools, preserve and ship valid Hermes work, and ensure Sandbox prevents recurrence."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See Truthful Scheduled-Work Health (Priority: P1)

An operator can inspect Hermes and immediately distinguish an idle monitor, a queued coding job, a successful coding run, a provider rejection, and a gateway ownership conflict.

**Why this priority**: The current system reports rejected agent requests as successful, which hides the primary failure and makes every later operational decision unreliable.

**Independent Test**: Present a scheduler state containing a successful script run, a rejected agent request, and conflicting gateway owners; the health report identifies each condition and returns a degraded overall result.

**Acceptance Scenarios**:

1. **Given** a scheduled agent request is rejected before doing work, **When** the operator checks scheduler health, **Then** the run is reported as failed with a sanitized actionable reason rather than successful.
2. **Given** a frequent scheduled entry is a monitor-only script, **When** the operator checks its status, **Then** it is classified as monitoring or dispatch work rather than implementation work.
3. **Given** more than one process claims gateway ownership, **When** health is checked, **Then** the conflict and the expected owner are reported.

---

### User Story 2 - Reconcile a Known Cron Catalog (Priority: P1)

An operator can replace an unknown or drifted Hermes cron inventory with the complete Sandbox-owned catalog in one confirmed, repeatable operation.

**Why this priority**: Ad hoc jobs accumulated with inconsistent routes, schedules, and execution modes; deleting and manually recreating them would reproduce the same drift.

**Independent Test**: Starting from duplicate, obsolete, paused, and malformed jobs, a confirmed reconciliation leaves exactly one enabled copy of every desired job, no unmanaged jobs, and no invalid model route.

**Acceptance Scenarios**:

1. **Given** any existing Hermes cron inventory, **When** the operator previews reconciliation, **Then** the report lists removals, creations, retained prerequisites, routes, and schedules without changing the remote.
2. **Given** the operator confirms reconciliation, **When** it completes, **Then** every old job has been removed and every desired job has been recreated exactly once.
3. **Given** reconciliation is run again, **When** the catalog already matches, **Then** it makes no changes and reports convergence.
4. **Given** creation of any desired job fails, **When** reconciliation stops, **Then** the failure is explicit and the resulting partial inventory is reported for recovery.

---

### User Story 3 - Prove a Coding Job Can Work (Priority: P1)

An operator can trigger a scheduled coding job and wait for bounded evidence that the agent request was accepted, the run completed truthfully, and repository changes or a no-work result are inspectable.

**Why this priority**: A "triggered" acknowledgement only proves that a subprocess was launched; it does not prove that a model accepted the request or that any task ran.

**Independent Test**: Trigger one harmless acceptance job with a supported route and verify a changed run timestamp, terminal status, sanitized output, and repository evidence within a fixed timeout.

**Acceptance Scenarios**:

1. **Given** a valid coding job, **When** the operator triggers verified execution, **Then** the command waits for a terminal run result and reports success only after evidence is available.
2. **Given** the provider rejects the model or reasoning configuration, **When** verified execution completes, **Then** it reports failure even if upstream metadata says success.
3. **Given** the agent finds no approved work, **When** the run completes, **Then** the result states no work and does not fabricate a successful implementation.

---

### User Story 4 - Keep One Gateway Owner (Priority: P2)

An operator can converge Hermes onto the Sandbox-managed gateway lifecycle without a manual process and a legacy service continuously fighting each other.

**Why this priority**: Competing gateway owners create restart storms, obscure scheduler ownership, and leave old in-memory behavior active after configuration changes.

**Independent Test**: Starting with a manual gateway and a restarting legacy service, a confirmed convergence leaves one active Sandbox-managed gateway, no conflicting process, and a stable service state across repeated checks.

**Acceptance Scenarios**:

1. **Given** a manual gateway process and a legacy service conflict, **When** the operator previews convergence, **Then** it reports what will stop, disable, install, and start.
2. **Given** convergence is confirmed, **When** it completes, **Then** only the Sandbox-managed gateway owns the scheduler.
3. **Given** convergence is run on an already healthy gateway, **When** it completes, **Then** it is idempotent and does not interrupt unrelated services.

---

### User Story 5 - Preserve Agent Work Before Cleanup (Priority: P2)

An operator can inventory every managed repository and worktree, classify dirty changes, and preserve valid changes before cron or worktree cleanup.

**Why this priority**: Hermes produced changes in detached and task worktrees; deleting jobs or worktrees without reviewing them risks losing useful work.

**Independent Test**: An inventory containing clean, dirty, detached, unrelated, and invalid worktrees reports each state and refuses destructive cleanup while unpreserved changes remain.

**Acceptance Scenarios**:

1. **Given** dirty Hermes worktrees, **When** reconciliation or cleanup is requested, **Then** the operator receives a complete repository, branch, commit, and changed-file inventory.
2. **Given** changes fail validation or exceed their task scope, **When** reviewed, **Then** they are retained and reported rather than automatically committed.
3. **Given** changes pass their repository checks and current approval permits shipping, **When** preserved, **Then** they are committed and pushed to an explicit branch before cleanup.

---

### User Story 6 - Reuse Secure Remote Connections (Priority: P2)

An operator can run several independent Sandbox checks against the same server without paying the full connection and authentication setup cost for every command.

**Why this priority**: Scheduler diagnosis and convergence require many short remote observations. Repeating secure-session setup makes routine checks slow and encourages unsafe ad hoc command bundling.

**Independent Test**: Run three harmless remote checks in sequence; later checks reuse the established secure connection, retain independent exit/timeout evidence, and automatically recover when the reusable connection is absent or stale.

**Acceptance Scenarios**:

1. **Given** a successful remote check established a reusable connection, **When** another Sandbox command targets the same endpoint shortly afterward, **Then** it reuses that connection without changing authentication or host-verification policy.
2. **Given** the reusable connection is unavailable or stale, **When** a command runs, **Then** the command falls back to a fresh secure connection and retains truthful failure evidence.
3. **Given** several observations are one atomic diagnostic operation, **When** Sandbox gathers them, **Then** it may batch them into one bounded remote operation while preserving per-observation status.

### Edge Cases

- The job metadata says success while the newest request dump or run artifact records a provider/client failure.
- A model identifier embeds an effort suffix, or model and reasoning fields are individually valid but the effective provider request is rejected.
- A monitor script exits successfully because there was no work to queue.
- The desired work directory is missing, detached, dirty, or points at an outdated commit.
- The gateway PID file names a stale process, a manual gateway is already running, or a legacy service loops on restart.
- Reconciliation is interrupted after removals but before all creations finish.
- A cron script or prompt contains a secret-like value; reports and committed catalog data must never expose it.
- The upstream Hermes version changes job storage or output behavior.
- A reusable remote connection is stale, its control endpoint is inaccessible, or two configured remotes use the same host with different users or ports.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST provide one read-only Hermes health operation that reports gateway ownership, scheduler availability, cron classifications, routing validity, latest terminal outcomes, false-success evidence, and dirty managed worktrees.
- **FR-002**: Health MUST classify agent jobs separately from monitor-only or dispatcher-only scripts.
- **FR-003**: Health MUST treat a sanitized provider/client error newer than or associated with a nominally successful run as a failure and flag the metadata disagreement.
- **FR-004**: Health MUST never expose stored prompts, credentials, tokens, authorization headers, or unbounded agent output.
- **FR-005**: Sandbox MUST maintain a complete, version-controlled desired cron catalog containing stable logical names, schedules, execution mode, route profile, work target, and non-secret task intent.
- **FR-006**: Catalog validation MUST reject duplicate names, invalid schedules, unsafe work targets, missing scripts, unsupported route profiles, model identifiers containing effort suffixes, and agent jobs without a validated route.
- **FR-007**: Sandbox MUST provide a side-effect-free reconciliation preview before any cron removal or creation.
- **FR-008**: Confirmed reconciliation MUST remove every existing cron job and recreate the entire desired catalog through validated Sandbox controls.
- **FR-009**: Reconciliation MUST report partial progress and recovery guidance when any removal, creation, routing, or verification step fails.
- **FR-010**: Reconciliation MUST be idempotent after convergence and MUST not create duplicate jobs.
- **FR-011**: Sandbox MUST provide verified cron execution that waits for a bounded terminal result rather than treating process launch as completion.
- **FR-012**: Verified execution MUST cross-check upstream metadata with bounded run and request-error evidence before reporting success.
- **FR-013**: Sandbox MUST provide gateway convergence with preview and confirmation, establishing the Sandbox-managed service as the sole owner and disabling or stopping conflicting legacy/manual owners.
- **FR-014**: Gateway convergence MUST verify a stable active state, scheduler presence, and absence of a restart storm after the ownership change.
- **FR-015**: Setup, restore, update, and fresh-server installation paths MUST install the same gateway ownership and cron-catalog behavior.
- **FR-016**: Sandbox MUST inventory all managed repositories and their worktrees before destructive cron/worktree cleanup and MUST block cleanup while dirty changes are unpreserved.
- **FR-017**: Worktree preservation MUST keep unrelated or failing changes available for human review and MUST never force-commit them as successful work.
- **FR-018**: CLI, MCP, documentation, tests, and remote bootstrap behavior MUST expose the same health, reconciliation, verification, and ownership contracts.
- **FR-019**: All external mutations MUST require explicit confirmation and use the configured Sandbox remote and secret-handling paths.
- **FR-020**: The implementation MUST retain a bounded task trace with commands, checks, remote evidence, outcome, and residual risks without recording secrets.
- **FR-021**: Sandbox MUST opportunistically reuse authenticated remote connections for repeated operations against the same user, host, and port without weakening authentication or host verification.
- **FR-022**: Reusable remote connection state MUST be isolated per endpoint, owner-only, bounded in idle lifetime, contain no credentials or readable connection target, and fall back safely when unavailable.
- **FR-023**: Connection reuse and any batched diagnostic operation MUST preserve each command's timeout, exit status, bounded output, redaction, and confirmation contract.

### Key Entities

- **Desired Cron Entry**: Stable logical job definition with schedule, execution classification, route profile, work target, and non-secret intent.
- **Observed Cron Entry**: Sanitized remote scheduler metadata and bounded terminal evidence for one installed job.
- **Cron Reconciliation Plan**: Ordered removals, creations, validations, and verification steps with no side effects until confirmed.
- **Gateway Ownership State**: Expected service owner, observed services/processes, restart behavior, and conflict classification.
- **Worktree Evidence**: Repository, worktree, branch/detached state, commit, dirty paths, and validation disposition.
- **Verified Run Result**: Trigger time, observed run transition, terminal status, sanitized failure or outcome, and repository evidence.
- **Remote Connection Lease**: Short-lived, endpoint-isolated client state that allows later commands to reuse an authenticated secure transport without storing credentials.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In automated and remote acceptance checks, 100% of provider/client rejections are reported as failures and none are reported solely as successful triggers.
- **SC-002**: A confirmed reconciliation from the current five-job inventory produces exactly the desired catalog with zero duplicate names and zero invalid routes.
- **SC-003**: Re-running reconciliation on the converged catalog produces zero removals and zero creations.
- **SC-004**: Gateway convergence leaves exactly one scheduler-owning gateway process and no service restart growth during a two-minute observation window.
- **SC-005**: A verified acceptance cron reaches a terminal, evidence-backed outcome within its configured timeout, or returns a specific actionable failure.
- **SC-006**: Every dirty Hermes worktree is either shipped on an explicit branch or retained with a documented reason; none is silently deleted.
- **SC-007**: A fresh-server setup can reproduce gateway ownership and the complete cron catalog using only documented Sandbox commands.
- **SC-008**: Focused scheduler tests, full Sandbox tests, CLI/MCP contract checks, and remote health checks all pass with no secret values in output or committed files.
- **SC-009**: In a three-command live acceptance sequence, at least two later commands reuse one established secure connection, all three retain independent results, and stale reusable state recovers without manual cleanup.

## Assumptions

- The configured remote remains the operator-approved Hermes server and the existing authentication material remains valid.
- Upstream Hermes continues to store cron metadata and bounded run artifacts under its documented operator state directory, but Sandbox treats those details as an adapter boundary.
- The desired catalog includes monitor/dispatcher jobs only when they support an explicitly defined workflow; frequency alone is not evidence of useful work.
- Valid repository changes are shipped only after repository-specific instructions and tests pass; invalid or unrelated changes are preserved rather than discarded.
- The current user instruction authorizes this task's cron replacement, gateway convergence, commits, pushes, and remote synchronization, while unrelated production deployment remains out of scope.
