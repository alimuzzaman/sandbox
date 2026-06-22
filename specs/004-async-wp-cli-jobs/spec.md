# Feature Specification: Async / Background WP-CLI Jobs

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Steal from Novamira #2 — WP-CLI runs synchronously today and
long migrations/imports time out or block the agent; add background jobs with a job id and
incremental log polling."

## Context

Both the `sb wp` CLI and the `wp_cli` MCP tool run synchronously: the caller blocks
until the command exits, and the MCP tool hard-caps its wait. Long operations —
media regeneration, large search-replace, bulk imports, plugin/DB migrations —
either time out or wedge the agent. This feature adds fire-and-forget background
jobs: a command starts, returns a job identifier immediately, runs detached, and the
agent polls for incremental output, completion status, and (if needed) cancels it.

Implementation detail (detached container exec vs host nohup, the PID self-report,
log-slice mechanics) is deferred to `plan.md`.

## Clarifications

### Session 2026-06-22

- Q: Include background-job cancellation in v1? → A: Yes — support cancel/kill. The launched job self-reports its process id so a later call can terminate it; killing a finished/unknown job is a safe no-op.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Long command doesn't block the agent (Priority: P1)

An agent starts a multi-minute WP-CLI command and immediately gets a job identifier
back, without blocking or timing out; the command keeps running.

**Why this priority**: This is the entire reason for the feature — unblocking the
agent on long operations.

**Independent Test**: Start a known-long command in async mode and confirm the call
returns a job identifier near-instantly while the command continues running.

**Acceptance Scenarios**:

1. **Given** a running instance, **When** the agent starts a long command in async
   mode, **Then** it receives a job identifier within ~1 second and the command keeps
   running after the call returns.
2. **When** the agent polls the job, **Then** it reports "running" with output so far,
   then "completed" with a final exit code once finished.
3. **Given** a finished job, **When** polled again, **Then** the status and exit code
   persist (re-readable) until the job is reaped.

### User Story 2 — Incremental output polling (Priority: P2)

The agent fetches only new output each poll, so a large log isn't re-sent every call.

**Why this priority**: Keeps polling cheap for big logs; valuable but secondary to
the core unblock.

**Independent Test**: Poll a job with a byte offset and confirm only the new slice is
returned with a truncation indicator.

**Acceptance Scenarios**:

1. **Given** a running job, **When** the agent polls with an offset, **Then** it gets
   the output slice from that offset plus how many bytes were read and whether more
   remains, and can advance the offset accordingly.

### User Story 3 — Cancel a running job (Priority: P1)

The agent (or developer) stops a long-running job it no longer needs.

**Why this priority**: Without cancellation a runaway/slow job can only be waited out;
explicitly requested for v1.

**Independent Test**: Start a long job, cancel it, and confirm the process is gone and
the job reports a cancelled status.

**Acceptance Scenarios**:

1. **Given** a running job, **When** the agent cancels it, **Then** the process is
   terminated and the job records a cancelled outcome.
2. **Given** a finished or unknown job, **When** cancel is called, **Then** it is a
   no-op with a clear result (no error).

### User Story 4 — CLI parity (Priority: P2)

A developer drives the same async lifecycle from the CLI.

**Why this priority**: Keeps the CLI and MCP surfaces at parity; convenience, not
core capability.

**Independent Test**: Start an async job, follow it, list jobs, and kill one — all
from the CLI.

**Acceptance Scenarios**:

1. **Given** the CLI, **When** the developer starts a job async, **Then** they get a
   job identifier; they can follow it, list active/recent jobs, and kill one.

### User Story 5 — Works on every server driver (Priority: P1)

Async jobs behave identically whether the instance is container-backed or
host-served (herd).

**Why this priority**: A capability that only works on some drivers is a trap;
parity is required.

**Independent Test**: Run the same async job on a container-backed and a host-served
instance and confirm both poll to completion.

**Acceptance Scenarios**:

1. **Given** either driver, **When** an async job runs, **Then** start, poll,
   completion, and cancel all work the same way.

### Edge Cases

- Malformed/forged job identifiers MUST be rejected before any filesystem access
  (no path traversal).
- A job whose process dies without recording completion → polling reflects this
  rather than reporting "running" forever (reaped by age).
- Old job artifacts are pruned automatically so they don't accumulate.
- Async mode does not widen what commands may run — it is only an execution mode for
  the same `wp` surface.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The WP-CLI surface MUST support an async mode that starts a command,
  returns a job identifier immediately, and never blocks beyond process spawn.
- **FR-002**: The system MUST provide a job-status query returning state
  (running/completed/not-found), exit code when finished, and the captured output.
- **FR-003**: Output retrieval MUST support an offset + limit so callers fetch only
  new output, reporting bytes read and whether more remains.
- **FR-004**: Job identifiers MUST be validated against a strict format before any
  filesystem access.
- **FR-005**: The system MUST support cancelling a running job; cancelling a
  finished/unknown job MUST be a safe no-op.
- **FR-006**: Completion MUST be durably recorded (state survives re-query) until the
  job is reaped.
- **FR-007**: Job artifacts MUST be reapable on demand and auto-pruned by age.
- **FR-008**: Async MUST be only an execution mode for the existing `wp` surface — it
  MUST NOT broaden which commands are allowed, and MUST use the same instance
  resolution/handshake as the synchronous path.
- **FR-009**: Async jobs MUST work identically on container-backed and host-served
  (herd) instances.
- **FR-010**: The CLI MUST offer parity: start async, follow, list, and kill jobs.

### Key Entities

- **Job**: a single background WP-CLI run, identified by a validated token, with
  output, a terminal status + exit code, and a process handle for cancellation.
- **Job artifacts**: the host-visible per-job records (output, status, process id)
  that encode the running→completed/cancelled state machine.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Starting a multi-minute command in async mode returns control to the
  agent in under ~2 seconds.
- **SC-002**: An agent can poll a job to completion and read its full output and exit
  code without ever blocking on the command's duration.
- **SC-003**: A cancelled job's process is gone and its status reflects cancellation
  100% of the time in test.
- **SC-004**: The same async job completes successfully on both a container-backed
  and a host-served instance.
- **SC-005**: No job artifact persists beyond the configured retention window
  (default ~24h).

## Assumptions

- State is file-based (no database/registry entry); presence/absence of the status
  record is the completion signal.
- Polling, not server push, is the delivery model for output.
- There is no concurrency cap or queue in v1 — jobs start immediately and the OS
  schedules them.
- Job artifacts are gitignored runtime state.
