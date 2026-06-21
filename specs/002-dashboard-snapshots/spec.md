# Feature Specification: Snapshot & Restore from the WordPress Dashboard

**Feature Branch**: `002-dashboard-snapshots`

**Created**: 2026-06-21

**Status**: Draft

**Input**: User description: "Snapshot and restore from the WordPress admin dashboard via a sandbox mu-plugin, so users can capture and roll back instance state without the CLI"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Take a snapshot from wp-admin (Priority: P1)

A developer working in the WordPress admin of a sandbox instance opens a Sandbox
Snapshots screen, optionally names a snapshot, clicks "Take snapshot," and the current
DB + uploads are captured — equivalent to running `sb snapshot <name>` on the host.

**Why this priority**: Capturing state before a risky action is the most common reason to
snapshot; doing it without leaving the browser is the core value of this feature.

**Independent Test**: In wp-admin of a Docker-backed instance, create a snapshot named
`t1`; confirm it then appears both in the dashboard list AND in `sb snapshots` for that
instance, with a `db.sql` and `uploads.tgz`.

**Acceptance Scenarios**:

1. **Given** I am an admin on a sandbox instance, **When** I take a snapshot named `t1`,
   **Then** a snapshot `t1` is created under that instance's snapshot store and listed in
   both the dashboard and the CLI.
2. **Given** a snapshot named `t1` already exists, **When** I take another named `t1`,
   **Then** I am warned and must explicitly confirm overwrite (parity with CLI `--force`).
3. **Given** I leave the name blank, **When** I take a snapshot, **Then** a valid default
   name is generated (matching the CLI's `[A-Za-z0-9._-]+` rule).

---

### User Story 2 - Restore a snapshot from wp-admin (Priority: P1)

A developer selects a previously captured snapshot and restores it from the dashboard,
replacing the current DB + uploads with that point-in-time state — equivalent to `sb
restore <name>` — after an explicit confirmation.

**Why this priority**: Rollback is the other half of the value; without restore, capturing
is only half useful.

**Independent Test**: Take `t1`, change site state (e.g. delete a post / toggle a plugin),
restore `t1`, confirm the site returns to the captured state.

**Acceptance Scenarios**:

1. **Given** snapshot `t1` exists, **When** I restore it after confirming, **Then** the DB
   is replaced as a true point-in-time state (tables created after the snapshot are gone,
   matching CLI restore semantics) and uploads are restored.
2. **Given** a restore is requested, **When** I have not confirmed, **Then** nothing is
   changed (restore is destructive and requires explicit confirmation).
3. **Given** a restore is in progress, **When** the DB is being reset/reimported, **Then**
   the admin is not left with a corrupted half-restored state (the operation is atomic
   enough that a failure leaves a recoverable instance, and the outcome is reported).

---

### User Story 3 - See and manage snapshots in one place (Priority: P2)

A developer sees the list of snapshots for the current instance (name, size, created/meta)
in the dashboard, and can delete ones they no longer need.

**Why this priority**: Listing/deleting rounds out the workflow but isn't required to
deliver the core capture/rollback value.

**Independent Test**: With several snapshots present, the dashboard lists them matching `sb
snapshots`; deleting one removes it from both views.

**Acceptance Scenarios**:

1. **Given** snapshots exist, **When** I open the screen, **Then** I see each snapshot's
   name, size, and metadata, consistent with `sb snapshots`.
2. **Given** a snapshot, **When** I delete it after confirming, **Then** it is removed from
   the store and both views.

---

### Edge Cases

- **Host/container boundary**: the WP request runs inside the container/host PHP, but
  snapshots are a host-level operation (DB export via the wpcli service, uploads tar,
  storage under the host snapshot store outside the WP tree). The mechanism by which the
  dashboard triggers the host operation is the central open question (see below).
- **Restore-while-serving**: a restore resets the very DB serving the request — the design
  MUST avoid the request killing its own DB connection mid-restore and reporting a false
  failure.
- **Herd (host) instances**: the CLI currently does NOT support snapshots on herd
  instances. The dashboard MUST reflect that (disable/explain) rather than appear to work.
- **Permissions**: a non-admin or a request without a valid nonce MUST be rejected.
- **Non-sandbox environments**: the mu-plugin MUST NOT load/act outside a sandbox instance
  (it must never ship in or affect a real site).
- **Large uploads / long operations**: capture/restore may exceed a normal request budget;
  the design MUST handle long-running operations without a silent timeout.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a WordPress admin screen (prefixed, e.g. under Tools)
  to take, list, restore, and delete snapshots for the current instance.
- **FR-002**: Taking a snapshot from the dashboard MUST produce a snapshot identical in
  format and location to a CLI snapshot, so the two are mutually visible and interchangeable.
- **FR-003**: Restoring from the dashboard MUST replicate CLI restore semantics (full
  point-in-time DB replacement incl. drop-all-tables, plus uploads restore).
- **FR-004**: Every state-changing action (take/restore/delete) MUST enforce BOTH a
  capability check (admin-level, e.g. `manage_options`/`install_plugins`) AND a nonce.
- **FR-005**: Restore and delete MUST require explicit user confirmation before executing.
- **FR-006**: The mu-plugin MUST be sandbox-only: it MUST be installed/loaded by the
  sandbox provisioning (like the existing mail/ssl/autologin mu-plugins) and MUST NOT
  activate or have effect outside a sandbox instance.
- **FR-007**: The system MUST report success/failure and surface errors to the admin user
  (no silent failures), including the not-supported case on herd instances.
- **FR-008**: All input (snapshot name) MUST be validated/sanitized to the CLI's allowed
  character set; all output MUST be escaped.
- **FR-009**: The feature MUST identify the current instance correctly so it acts on the
  right snapshot store (consistent with the per-project model from spec 001).
- **FR-010**: The host-bridge mechanism MUST NOT expose a way to execute arbitrary host
  commands from the browser; only the defined snapshot/restore/list/delete operations for
  the current instance are permitted.

### Key Entities

- **Snapshot**: a named point-in-time capture of one instance (DB dump + uploads archive +
  metadata), stored in that instance's host-side snapshot store.
- **Snapshot mu-plugin**: a prefixed sandbox-only must-use plugin that renders the admin UI
  and initiates snapshot operations for the current instance.
- **Host bridge**: the trusted channel by which the in-WordPress UI causes the host-level
  snapshot/restore to run (mechanism TBD — see Open Questions).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer can capture a snapshot from wp-admin and see it in `sb snapshots`
  (and vice versa) with no CLI step required.
- **SC-002**: A developer can roll an instance back to a captured state entirely from
  wp-admin, with the resulting state matching a CLI restore of the same snapshot.
- **SC-003**: 100% of state-changing dashboard actions are rejected without a valid nonce
  and admin capability.
- **SC-004**: The mu-plugin has zero effect outside a sandbox instance (never loads/acts on
  a non-sandbox site).
- **SC-005**: On herd instances, the dashboard clearly communicates the unsupported state
  rather than failing opaquely.

## Assumptions

- Snapshots remain host-side and local-only (consistent with the existing CLI design and
  the gitignored snapshot store); this feature adds a UI + trigger, not a new storage model.
- The sandbox already installs mu-plugins into every instance during provisioning
  (mail/ssl/autologin), so an additional sandbox mu-plugin fits the existing mechanism.
- Docker-backed instances are the primary target for v1; herd support follows whenever CLI
  snapshot support for herd lands.
- This feature depends on the per-project instance model (spec 001) for correctly
  identifying the current instance.

## Open Questions (for `/speckit-clarify`)

1. **Host-bridge mechanism**: how does the in-WordPress UI trigger the host-level
   snapshot/restore? Candidate approaches to decide between: (a) a small localhost-bound
   host agent/daemon that runs `sb snapshot/restore`; (b) a request-file dropped in a
   bind-mounted control dir that a host watcher executes; (c) exposing the operations
   through the existing MCP server / a REST endpoint the mu-plugin calls; (d) an
   in-container approximation writing to a bind-mounted path picked up host-side. This is
   the load-bearing architectural decision and gates the plan.
2. **Restore-while-serving**: run the restore out-of-band (host) so the serving request
   doesn't sever its own DB, vs. a guarded in-request flow — confirm the approach.
3. **Scope of v1**: take + restore + list only, or also delete and rename? Include herd or
   defer?
