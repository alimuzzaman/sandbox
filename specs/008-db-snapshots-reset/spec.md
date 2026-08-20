# Feature Specification: DB-Only Snapshots & Reset-to-Fresh-Install

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Add a wp reset / db-reset option to return WordPress to its
initial state after install; add DB-only snapshots since most of the time resetting the DB
is enough; also expose this in the dashboard mu-plugin."

## Context

The snapshot/restore system captures the database and the uploads archive together.
For most dev/test loops the database is the state worth saving and rolling back;
uploads rarely change and the archive is the slow, large part. This feature adds (1)
DB-only snapshots and (2) a fast "reset to fresh install" — an automatically captured
post-install baseline that `reset` restores to return WordPress to its initial
installed state (admin, default content, activated plugins) without recreating the
instance. Both also surface in the dashboard snapshot mu-plugin. This extends the
existing snapshot/restore feature.

Implementation detail (the baseline storage location, bridge routes, CLI/MCP names)
is deferred to `plan.md`.

## Clarifications

### Session 2026-06-22

- Q: What does "reset" restore to? → A: The **post-provision baseline** (admin, default content, activated plugins), **not** an empty database. Captured automatically as a reserved DB-only snapshot **after plugin/theme wiring + seed import complete** (in the `ensure_instance`/onboard flow — NOT inside `cmd_install`, which runs before wiring/seed). [analysis F1]
- Q: Snapshot scope for this feature? → A: **DB-only** is the focus and the default for the baseline and reset. Full snapshots (DB + uploads) remain available; DB-only is a new mode, not a replacement.
- Q: Where is it surfaced? → A: CLI + the agent tool surface **and** the dashboard snapshot mu-plugin (a DB-only toggle + a "Reset to fresh install" action).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — DB-only snapshot (Priority: P1)

A dev saves just the database before a risky change, skipping the uploads archive.

**Why this priority**: The common case; faster snapshots that capture the state that
actually matters.

**Independent Test**: Take a DB-only snapshot and confirm it captures the database,
omits the uploads archive, and restores correctly.

**Acceptance Scenarios**:

1. **Given** a running instance, **When** the dev takes a DB-only snapshot, **Then**
   it captures the database, omits the uploads archive, records its mode, and finishes
   faster than a full snapshot.
2. **Given** a DB-only snapshot, **When** restored, **Then** the database is replaced
   point-in-time and uploads are left untouched (no error about missing uploads).
3. **When** snapshots are listed, **Then** each shows its mode (DB-only / full).

### User Story 2 — Reset to fresh install (Priority: P1)

A dev or agent has dirtied the database and wants the clean post-install state back in
seconds, without recreating the instance.

**Why this priority**: The headline ask — a fast "give me a clean DB" that keeps the
instance, uploads, and ports.

**Independent Test**: Dirty the database, run reset, and confirm the site is back to
its initial installed state.

**Acceptance Scenarios**:

1. **Given** an instance installed by the Sandbox, **Then** a reserved DB-only
   baseline was captured automatically at install time.
2. **Given** a dirtied database, **When** the dev/agent runs reset, **Then** the
   database is restored to the baseline (uploads left as-is) and the site is back to
   its initial installed state.
3. **Given** no baseline exists (older instance), **When** reset is run, **Then** it
   explains how to create one rather than failing opaquely.
4. **When** the dev re-baselines, **Then** the baseline is re-captured from the current
   database (e.g. after intentionally changing the activated plugin set).

### User Story 3 — From the WordPress dashboard (Priority: P2)

A dev takes a DB-only snapshot and resets to fresh install from the in-dashboard Tools
screen.

**Why this priority**: Convenience parity with the CLI/agent surface; not the core
capability.

**Independent Test**: Use the dashboard DB-only toggle and "Reset to fresh install"
button and confirm both complete via the existing out-of-band flow.

**Acceptance Scenarios**:

1. **Given** the dashboard snapshot screen, **When** the dev captures with "DB only"
   checked or clicks "Reset to fresh install", **Then** both run through the existing
   authenticated out-of-band flow and report completion.

### User Story 4 — Reset is destructive, so it is gated (Priority: P1)

Reset drops the current database, so it requires explicit confirmation, and the
baseline is protected.

**Why this priority**: Prevents accidental data loss; non-negotiable for a destructive
op.

**Independent Test**: Confirm reset prompts/requires confirmation and that the reserved
baseline can't be overwritten or deleted by ordinary snapshot operations.

**Acceptance Scenarios**:

1. **Given** reset, **When** invoked without explicit confirmation, **Then** it
   prompts (CLI) or requires a confirm flag (agent surface) before dropping the DB.
2. **Given** the reserved baseline, **When** ordinary snapshot/delete operations run,
   **Then** they cannot overwrite or delete it (only an explicit re-baseline replaces
   it).

### Edge Cases

- Restoring a DB-only snapshot must not delete or alter existing uploads.
- A config change that alters the activated plugin set makes the baseline stale → the
  re-baseline path refreshes it.
- Host-served (herd) instances: snapshots/reset remain unsupported in v1 (consistent
  with the existing snapshot feature), emitting the existing unsupported notice.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Snapshot capture MUST support a DB-only mode that skips the uploads
  archive and records the snapshot's mode (DB-only / full).
- **FR-002**: Restore MUST correctly restore a DB-only snapshot (point-in-time DB
  replacement) without deleting or altering existing uploads.
- **FR-003**: Snapshot listings MUST report each snapshot's mode.
- **FR-004**: The system MUST automatically capture a reserved DB-only **baseline**
  representing the post-provision state, hooked **after** plugin/theme wiring and seed
  import in the `ensure_instance`/onboard flow (not inside `cmd_install`, which runs
  before those). [analysis F1]
- **FR-005**: The system MUST provide a **reset** that restores the baseline, and a
  **re-baseline** that re-captures it from the current database.
- **FR-006**: Reset MUST be gated as destructive (CLI confirmation / agent confirm
  flag), and the reserved baseline MUST be protected from ordinary overwrite/delete.
- **FR-007**: When no baseline exists, reset MUST give actionable guidance rather than
  failing opaquely.
- **FR-008**: The dashboard snapshot mu-plugin MUST offer a DB-only capture toggle and
  a "Reset to fresh install" action, both via the existing authenticated out-of-band
  flow with completion polling.
- **FR-009**: DB-only snapshots and reset MUST be available on the CLI and the agent
  tool surface in addition to the dashboard.
- **FR-010**: Host-served (herd) instances MAY remain unsupported in v1, emitting the
  existing unsupported notice (consistent with the current snapshot feature).

### Key Entities

- **Snapshot**: a saved instance state with a mode (DB-only or full) and metadata.
- **Baseline**: the reserved, protected DB-only snapshot captured at install,
  representing the initial installed state; one per instance.
- **Reset**: the operation that restores the baseline; re-baseline replaces it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A DB-only snapshot omits the uploads archive and restores without error.
- **SC-002**: After dirtying the database, reset returns the site to its post-install
  state in seconds, with the instance, uploads, and ports intact.
- **SC-003**: DB-only capture is meaningfully faster than a full snapshot on an
  instance with non-trivial uploads.
- **SC-004**: The reserved baseline cannot be overwritten or deleted by ordinary
  snapshot operations.
- **SC-005**: The dashboard DB-only capture and "Reset to fresh install" both complete
  and report status through the existing flow.

## Assumptions

- This extends the existing snapshot/restore feature; restore already resets the DB
  before import and tolerates a missing uploads archive, so DB-only restore needs no
  restore-side behavior change.
- The baseline is captured after install seeds content and activates plugins, so
  "initial state" matches what was provisioned; one baseline per instance. (No FR change
  under multi-instance-per-root: baselines and snapshot stores are keyed by instance
  *name*, which stays globally unique when a project root owns several labelled
  instances — each gets its own independent baseline. See `docs/multi-instance-spec.md` §7.)
- Full (DB + uploads) snapshots remain available; DB-only is additive.
- Host-served (herd) snapshot support is out of scope for v1 (tracked with the
  existing snapshot feature's limitation).

## Convergence amendment — 2026-08-13 (27-feedback restore safety)

Feedback `adde58a6` exposed a contract gap around named snapshot restore. The
existing baseline/reset protections remain; this amendment makes every
destructive restore confirmation boundary explicit.

### Normative requirements

- **FR-010**: Restoring any named snapshot MUST be treated as destructive because
  it drops/replaces current database state. A non-interactive CLI invocation MUST
  supply explicit `--yes` (or the established equivalent confirmation field);
  an interactive invocation MUST default-deny on missing, cancelled, or invalid
  confirmation.
- **FR-011**: MCP and bridge/dashboard restore callers MUST carry an explicit
  boolean confirmation plus their existing nonce/capability checks. A plan ID,
  snapshot name, prior prompt, or caller identity MUST NOT imply confirmation.
- **FR-012**: Confirmation MUST be validated before any database reset, import,
  uploads extraction, or other restore-side effect. Refusal returns a stable
  `confirmation_required`/`confirmation_failed` error and leaves the instance
  unchanged.
- **FR-013**: A confirmed restore records the target snapshot, instance identity,
  confirmation path, and bounded outcome; cancellation or a preflight failure
  preserves the existing state and never reports success.

### Acceptance evidence required before closing this amendment

The matrix MUST include noninteractive refusal-before-mutation, interactive
cancel, confirmed CLI, confirmed MCP, and confirmed dashboard/bridge paths. It
must assert database/filesystem providers were not called on refusal and must
retain safe evidence for feedback `adde58a6`.
