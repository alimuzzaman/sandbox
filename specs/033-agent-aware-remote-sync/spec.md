# Feature Specification: Agent-Aware Remote Development Sync

**Feature Branch**: `033-agent-aware-remote-sync`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "Resume the committed agent-aware incremental sync draft for remote development workspaces and make it ready for formal specification."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Keep a Disposable Workspace Current (Priority: P1)

A developer opts a disposable remote development workspace into live
synchronization so supported local edits reach the selected workspace without a
full redeploy or service restart.

**Why this priority**: This is the primary edit-test benefit and addresses the
highest-ranked hosted development feedback.

**Independent Test**: In a disposable workspace, enable live mode, edit a
supported source file, and verify that one accepted remote generation contains
the edit within the healthy-profile freshness target.

**Acceptance Scenarios**:

1. **Given** a registered healthy disposable workspace with synchronization off,
   **When** the developer starts live mode and edits a supported file,
   **Then** the edit is accepted as one remote source generation and status shows
   the accepted generation and any later pending changes.
2. **Given** the remote is unavailable after live mode is enabled, **When** a
   supported edit occurs, **Then** the local edit remains intact, status records
   an actionable pending failure, and no false current state is reported.

### User Story 2 - Choose Deliberate Synchronization Boundaries (Priority: P1)

A developer uses checkpoint mode or a one-time request to decide exactly when
local source is sent to a disposable remote workspace, while the existing
deploy-only behavior remains unchanged when synchronization is off.

**Why this priority**: Explicit boundaries are required for compatibility,
reviewable changes, and workflows that should not continuously transfer edits.

**Independent Test**: Run one explicit checkpoint, confirm its accepted
generation, edit again without requesting a checkpoint, and verify that the
second edit is not transferred. Repeat with synchronization off and verify that
ordinary edits and commits cause no automatic transfer.

**Acceptance Scenarios**:

1. **Given** checkpoint mode with a healthy selected workspace, **When** the
   developer requests a checkpoint, **Then** the latest supported source is
   accepted as one generation before success is returned.
2. **Given** checkpoint or off mode, **When** a local edit or commit occurs,
   **Then** no automatic transfer is started and the commit itself is never
   blocked, amended, created, or pushed by synchronization.

### User Story 3 - Share One Source Relationship Safely (Priority: P1)

Multiple agents or sessions using the same canonical local worktree can
participate in one ordered synchronization relationship, while a different
worktree cannot silently overwrite its remote workspace.

**Why this priority**: Shared agent workflows otherwise create duplicate
transfers, unclear ownership, and cross-worktree data loss.

**Independent Test**: Start two sessions against one canonical worktree and
workspace, issue overlapping synchronization requests, and verify one ordered
generation stream. Then use a different canonical worktree and verify rejection
before remote file mutation.

**Acceptance Scenarios**:

1. **Given** two participants resolved to the same project identity and durable
   workspace ID, **When** both request synchronization close together, **Then**
   duplicate source is coalesced and only one accepted generation is recorded.
2. **Given** a workspace owned by one canonical worktree, **When** a different
   canonical worktree requests synchronization, **Then** the request is rejected
   before file transfer with a redacted actionable ownership conflict.
3. **Given** a symlinked path or relocated worktree that preserves the durable
   project identity, **When** it participates in the relationship, **Then** it is
   treated as the same owner; a fresh clone remains a different owner until an
   explicit lifecycle adoption.

### User Story 4 - Run Jobs Against Stable Source (Priority: P1)

A remote job runs against one accepted source generation even when local edits
continue, and a new job waits for the newest pending generation instead of
silently using stale source.

**Why this priority**: Jobs must not observe a mixture of pre-edit and mid-edit
files, and stale execution results are difficult to trust.

**Independent Test**: Start a job on generation A, create pending generation B,
request another job, and verify that the second job waits for B. Verify that
parallel-safe jobs share only an accepted generation and that later edits remain
pending until its active jobs release it.

**Acceptance Scenarios**:

1. **Given** active job A on generation A and pending generation B, **When** a
   new job is requested, **Then** it waits for B and never reports A as its
   accepted generation.
2. **Given** two parallel-safe jobs on generation A, **When** generation B is
   created, **Then** both jobs continue to report A and B is not accepted for the
   workspace until both jobs release A.
3. **Given** a synchronized job with a managed-source read-only projection,
   **When** it attempts to write managed source, **Then** the write is rejected
   with no managed-source mutation. A source-mutating request may run only in an
   explicitly isolated copy, whose writes remain output and cannot alter the
   accepted generation or a parallel-safe peer.

### User Story 5 - Recover and Inspect Synchronization Safely (Priority: P2)

A developer can understand and recover from interruption, conflicts, excluded
files, credential findings, and remote divergence without losing local work or
exposing protected data.

**Why this priority**: Recovery and redacted status determine whether an
automatic transfer is trustworthy in daily use.

**Independent Test**: Interrupt a transfer, restart the selected mode, and verify
that the same pending request can be reconciled without duplicate acceptance.
Exercise a credential finding, a file changing during capture, an out-of-band
remote edit, and an unsupported deletion, and verify each bounded result.

**Acceptance Scenarios**:

1. **Given** a lost acceptance response, **When** the developer requests status
   or restarts synchronization, **Then** the original replay-safe request
   identity reconciles without creating a second accepted generation.
2. **Given** a file changes during capture, **When** synchronization retries up to
   its bounded limit, **Then** it either accepts one coherent generation or fails
   without exposing mixed content as current.
3. **Given** a credential-like name, value, key material, or local environment
   file is found in tracked, modified, untracked, or explicitly included input,
   **When** synchronization validates the generation, **Then** the entire
   generation is rejected before remote mutation; it is not silently narrowed.
4. **Given** an out-of-band remote source edit, **When** synchronization is
   requested, **Then** it reports divergence and requires explicit resolution
   rather than adopting or overwriting the edit.

### Edge Cases

- A stop request during capture or transfer completes only if the captured state
  validates as one coherent generation; otherwise it remains unaccepted and no
  new transfer starts.
- Ordinary runtime, database, upload, cache, log, unsafe-path, and other
  non-credential exclusions are omitted without granting deletion authority.
- A credential finding is distinct from an ordinary exclusion and rejects the
  complete generation before any remote mutation.
- A supported local deletion removes only an entry previously managed by the
  same relationship; unknown remote entries and unrelated runtime state remain.
- Repeated start, ensure, checkpoint, or identical launch requests are
  idempotent and do not create duplicate accepted generations.
- A remote health or source-mount failure prevents acceptance and leaves the
  pending source visible.
- A synchronized job that needs source mutation must request an isolated copy or
  be rejected before launch; shared source remains read-only.
- A larger transfer, slower link, higher packet loss, or busier remote reports
  progress or delay rather than claiming the healthy-profile target.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST restrict synchronization to an explicitly selected
  registered disposable remote development workspace and MUST validate remote
  health, source mount, and workspace ownership before transferring files.
- **FR-002**: The system MUST provide off, live, checkpoint, and one-time modes.
  Off MUST preserve existing deploy-only behavior; checkpoint and off MUST not
  start automatic transfers.
- **FR-003**: In live mode, a successful local commit MUST act as a high-priority
  synchronization signal without blocking, amending, creating, or pushing the
  commit. Remote unavailability MUST never make the commit fail.
- **FR-004**: Ownership MUST use resolved project identity plus durable workspace
  ID. Symlinked paths resolving to the same identity MAY participate; fresh
  clones and unresolved relocations MUST require explicit lifecycle adoption.
- **FR-005**: The system MUST capture a stable local view and MUST retry or fail
  without acceptance when files change during capture. It MUST serialize
  concurrent synchronization and job-launch requests at the relationship
  boundary.
- **FR-006**: Each accepted source state MUST have a stable generation identity
  and MUST be accepted atomically as one coherent source state. Replaying one
  request identity MUST NOT create a second accepted generation.
- **FR-007**: A remote job MUST pin and report its accepted generation. A new job
  MUST wait for the newest pending eligible generation rather than joining an
  older active generation. Parallel-safe jobs MAY share a generation only after
  it is accepted.
- **FR-008**: A synchronized job MUST see managed source read-only. A shared job
  write MUST fail without managed-source mutation; source-mutating requests MUST
  use an explicitly isolated copy or be rejected before launch. Isolated writes
  MUST remain in the job-artifact/output boundary.
- **FR-009**: The system MUST apply ordinary exclusions to runtime state,
  databases, uploads, caches, logs, unsafe paths, and other non-source content.
  Credential screening MUST examine tracked, modified, untracked, and explicitly
  included files. Any credential finding MUST reject the entire generation before
  remote mutation.
- **FR-010**: Deletions MUST be limited to source entries proven to have been
  previously managed by the same relationship. Unknown remote source and
  unrelated runtime state MUST fail closed.
- **FR-011**: Remote divergence MUST never be silently adopted or overwritten.
  An out-of-band managed-source edit MUST be surfaced and require explicit
  resolution before another synchronization can mutate that area.
- **FR-012**: Stop, interruption, remote failure, and lost acknowledgment MUST
  leave accepted and pending generations distinguishable and MUST support bounded
  reconciliation with the original replay-safe request identity.
- **FR-013**: Status MUST expose equivalent bounded and redacted CLI and MCP
  fields for mode, lifecycle, target, ownership, runtime health, mount state,
  participants when available, latest commit, accepted/pending generations,
  active job generation, change counts, and actionable errors.
- **FR-014**: Public output and persisted non-secret metadata MUST exclude
  credentials, source contents, protected values, raw sensitive paths,
  environment values, and process arguments.
- **FR-015**: Synchronization MUST grant no new reset, destroy, takeover, cleanup,
  instance-replacement, or production authority. Existing lifecycle safety gates
  MUST remain in force.
- **FR-016**: The system MUST document that explicit apply may reset synced but
  uncommitted source to the committed revision, and MUST preserve existing
  explicit deploy and remote-job compatibility when synchronization is off.

### Key Entities

- **Synchronization relationship**: The shared ownership record connecting a
  resolved project identity, named remote, durable workspace ID, mode, and
  participating sessions.
- **Source generation**: One complete accepted local source state with a stable
  identity, optional commit identity, lifecycle state, and aggregate counts.
- **Pending synchronization**: A requested or detected source state not yet
  accepted because it is queued, transferring, retrying, conflicted, refused, or
  waiting for an active job to release its generation.
- **Pinned job**: A remote job associated with exactly one accepted source
  generation and its release state.
- **Divergence record**: A bounded redacted record that a remote managed-source
  entry changed outside the accepted local generation and needs explicit
  resolution.
- **Participant**: A CLI or agent session contributing to or inspecting one
  relationship without becoming an independent source owner.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Under the healthy profile—eligible checkout no larger than 512 MiB,
  one generation no larger than 10 MiB or 100 paths, round-trip latency no more
  than 100 ms, sustained transfer throughput at least 5 MiB/s, packet loss no
  more than 1%, remote CPU below 70%, and at least 20% free workspace storage—a
  supported edit reaches durable remote generation acceptance within 10 seconds.
  Timing starts when the supported trigger is accepted and ends at the remote
  durable acceptance acknowledgment; preflight, credential screening, stable
  capture, transfer, and validation are included.
- **SC-002**: In live mode, at least 95% of supported healthy-profile edit
  triggers reach accepted or explicitly pending status within 10 seconds, with
  no false-current result for a failed transfer.
- **SC-003**: Duplicate concurrent requests for identical source produce one
  accepted generation and one ordered status history, with zero duplicate
  acceptance records in the disposable acceptance run.
- **SC-004**: Every remote job started through a synchronized relationship reports
  exactly the source generation it executed, and no job starts against an
  unaccepted or mixed generation.
- **SC-005**: Credential findings cause zero remote source mutations in negative
  acceptance tests across tracked, modified, untracked, and explicitly included
  inputs, while ordinary non-credential exclusions remain omitted.
- **SC-006**: A synchronized shared-job write causes zero managed-source changes;
  an isolated-copy write produces output retrievable through the existing artifact
  surface and causes zero change to the accepted generation or a parallel-safe
  peer's view.
- **SC-007**: After interruption or a lost response, one bounded reconciliation
  restores an accurate accepted/pending state without duplicate generation
  acceptance in at least 10 consecutive disposable recovery runs.
- **SC-008**: CLI and MCP status responses agree on all target, ownership,
  lifecycle, generation, conflict, partial-failure, and redaction fields for the
  same relationship in the parity acceptance run.
- **SC-009**: With synchronization off, existing deploy-before-job acceptance
  scenarios continue to pass and no ordinary edit or commit starts a transfer.

## Assumptions

- Users explicitly select a registered disposable remote workspace and have the
  existing permissions required to use it.
- The canonical local worktree remains the source authority; remote-first editing
  and automatic merge are out of scope.
- Existing remote deployment, durable job, artifact, registry, and lifecycle
  capabilities remain available and retain their safety gates.
- The healthy-profile target is a bounded product outcome, not a guarantee for
  larger source sets, slow links, unavailable hosts, or overloaded remotes.
- Credential detection is fail-closed even when the source file is tracked; a
  detected credential is never treated as an ordinary exclusion.
- Explicit apply may reset synced uncommitted work to the committed revision, and
  this behavior is documented before synchronization is enabled.
- Production and permanent instances are not eligible in the first release.
