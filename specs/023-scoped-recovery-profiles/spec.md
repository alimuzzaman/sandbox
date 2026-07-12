# Feature Specification: Scoped Recovery Profiles

**Feature Branch**: `codex/hermes-public-access`

**Created**: 2026-07-13

**Status**: Draft

**Input**: User description: "Replace broad WordPress/container snapshots with reusable Sandbox-owned recovery profiles for Hermes/Sandbox/Cloudflare state and selected production applications; encrypt archives to Google Drive, support full and partial filesystem capture, schedule safely, and prove restoration from scratch."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Declare Valuable State (Priority: P1)

As the server owner, I can describe what is valuable for each managed system and
what is reproducible, so backups never capture transient data merely because it
exists on disk.

**Why this priority**: Correct scope prevents wasted storage, inconsistent snapshots,
and false confidence before any archive is created.

**Independent Test**: Validate the profile catalog and inspect a read-only plan
showing every included source, exclusion, capture mode, rationale, and restore target
without reading secret values or mutating the server.

**Acceptance Scenarios**:

1. **Given** a disposable development WordPress instance, **When** a plan is generated, **Then** its database, uploads, containers, images, caches, sockets, and logs are excluded.
2. **Given** a production application with valuable database state, **When** its profile is planned, **Then** the database is captured consistently while Git-owned code is referenced by commit.
3. **Given** a production site with valuable non-Git files, **When** its profile is planned, **Then** those files are included with explicit full or partial filesystem semantics.

---

### User Story 2 - Create Verified Encrypted Recovery Sets (Priority: P1)

As the server owner, I can run one Sandbox operation that captures selected profiles,
verifies integrity, encrypts the recovery set, and publishes it to Google Drive.

**Why this priority**: A scoped plan is useful only when it produces a restorable,
confidential artifact through a repeatable supported operation.

**Independent Test**: Create a fixture set, verify local and remote hashes, decrypt
it in a disposable directory, and prove excluded sentinels and secret values are
absent from output and manifests.

**Acceptance Scenarios**:

1. **Given** valid profiles and a passphrase reference, **When** capture runs, **Then** each artifact uses its consistency method, is hashed, encrypted, uploaded archive-first and manifest-last, and reported without secrets.
2. **Given** any artifact failure, **When** capture runs, **Then** no complete manifest is published and existing sets remain unchanged.
3. **Given** unavailable Drive storage, **When** capture runs, **Then** the verified encrypted local artifact remains available for retry and a bounded failure is reported.

---

### User Story 3 - Restore Safely From Scratch (Priority: P1)

As the server owner, I can plan a full or selected-profile restore onto a fresh
server, review what will be replaced, and apply it only after explicit confirmation.

**Why this priority**: Backups are not valuable until restoration is proven and
destructive ordering is controlled.

**Independent Test**: Restore fixtures into a disposable fresh-server root, verify
expected files/data, and prove plan mode performs zero writes.

**Acceptance Scenarios**:

1. **Given** a recovery-set identifier, **When** restore planning runs, **Then** it verifies metadata and reports ordered actions without changing services or files.
2. **Given** explicit confirmation and a valid pre-restore checkpoint, **When** apply runs, **Then** affected services are quiesced only when needed, state is restored, verified, and resumed.
3. **Given** verification failure, **When** apply runs, **Then** it stops, preserves evidence, and uses the checkpoint to return affected state to its prior condition.

---

### User Story 4 - Retain and Schedule Without Collisions (Priority: P2)

As the server owner, I can schedule profile-aware recovery and retention through
Sandbox while preventing overlap with existing Hermes jobs or another backup.

**Why this priority**: Automation is valuable after capture and restore are proven,
but must not create concurrency or deletion risks.

**Independent Test**: Use a fake clock and lock to verify skipped overlap, failure
retry state, retention candidates, and no deletion before a new verified set exists.

**Acceptance Scenarios**:

1. **Given** an existing run or insufficient resources, **When** the schedule fires, **Then** it skips safely and records the reason.
2. **Given** a successful verified run, **When** retention is evaluated, **Then** it proposes only superseded sets outside the configured count/age floor.
3. **Given** old incompatible Drive backups, **When** prune is requested, **Then** deletion requires a reviewed list and confirmation after a new verified set exists.

---

### User Story 5 - Rebuild the Control Plane (Priority: P2)

As the server owner, I can bootstrap a fresh server and restore Sandbox/Hermes,
Cloudflare declarations, repository state, approved credentials, and valuable
production data without restoring disposable development data or containers.

**Why this priority**: This proves the repository contains the reusable recovery
mechanism rather than relying on server history.

**Independent Test**: Run a fresh-server drill using only a clean Sandbox checkout,
approved secrets, and one recovery-set identifier.

**Acceptance Scenarios**:

1. **Given** a clean supported host, **When** bootstrap and restore complete, **Then** declared control-plane configuration is reconstructed without secrets in repository files or manifests.
2. **Given** unpublished critical Git changes, **When** capture plans them, **Then** they are a dedicated encrypted Git artifact; committed code comes from its remote.

### Edge Cases

- A source is absent, unreadable, changes during capture, or resolves through a symlink outside its allowed root.
- A database dump is empty or fails format-aware validation despite a zero exit code.
- A tree mixes Git-owned code, generated files, uploads, and runtime credentials.
- Encryption succeeds but upload, publication, or remote verification fails.
- A schedule fires during deployment, migration, another recovery run, or resource pressure.
- Drive contains legacy archives encrypted with a forgotten passphrase.
- A restore targets a different user, root, service name, or application version.
- A passphrase or provider token is missing, empty, printed, or supplied as a command argument.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a committed, versioned Sandbox recovery-profile catalog.
- **FR-002**: Every profile MUST declare identifier, scope, source type, allowed roots, capture mode, consistency method, exclusions, sensitivity, restore target, verification, retention class, and dependencies.
- **FR-003**: The system MUST support control-plane, database, Git-state, full-filesystem, and partial-filesystem artifacts without backing up containers or images.
- **FR-004**: Development WordPress databases and uploads MUST be excluded unless explicitly classified as valuable production state.
- **FR-005**: Initial profiles MUST cover Sandbox/Hermes/Cloudflare state; `lenzora.app` production PostgreSQL database and persistent application storage; `alimuzzaman.me` Git provenance plus any non-Git persistent paths discovered later; and `amarsonar-bangla` database plus its full WordPress directory, with only explicitly reviewed transient caches excluded.
- **FR-006**: Git-owned code MUST recover from its remote and pinned revision; only critical unpublished changes MAY be a separate encrypted artifact.
- **FR-007**: Planning MUST be side-effect-free and show inclusions, exclusions, sources, consistency steps, restore order, and destructive boundaries.
- **FR-008**: Every source path MUST pass an allowed-root policy before reading or archiving.
- **FR-009**: Database artifacts MUST use an application-appropriate consistency operation and pass non-empty and format-aware validation.
- **FR-010**: Filesystem capture MUST preserve required permissions, links, and timestamps while preventing traversal outside allowed roots.
- **FR-011**: Each recovery set MUST have a versioned non-secret manifest with provenance, hashes, sizes, dependencies, exclusions, and restore targets.
- **FR-012**: Plaintext staging MUST be owner-only and removed after encryption or explicitly reported for cleanup after failure.
- **FR-013**: The passphrase MUST enter only through an approved secret/environment channel and MUST NOT appear in arguments, output, manifests, repository files, or process listings.
- **FR-014**: Upload MUST publish encrypted artifacts before the manifest and verify remote object identity before success.
- **FR-015**: Failed or partial runs MUST NOT appear as complete recovery sets.
- **FR-016**: Listing MUST distinguish complete, incomplete, legacy, locally pending, and unverifiable sets without decryption.
- **FR-017**: Restore MUST default to a non-mutating plan and require explicit current confirmation for apply.
- **FR-018**: Restore apply MUST verify schema, identifiers, hashes, free space, allowed targets, prerequisites, and compatibility before replacement.
- **FR-019**: Restore apply MUST create or validate a rollback point for every affected valuable target.
- **FR-020**: Restore MUST support one profile or the complete set while preserving dependency order.
- **FR-021**: Verification MUST validate restored state, not merely successful extraction.
- **FR-022**: Scheduling MUST be a reusable Sandbox module, use a single-run lock, honor resource limits, and avoid overlap with existing work.
- **FR-023**: Retention MUST be deterministic, separately plannable, and unable to delete the newest or only verified set.
- **FR-024**: Legacy Drive deletion MUST require a reviewed candidate list, confirmation, and a newly verified set decryptable with the current passphrase.
- **FR-025**: Create, list, verify, restore, schedule, and prune MUST be available through feature-owned Sandbox CLI and MCP modules.
- **FR-026**: Production operations MUST run through Sandbox tools, not operator-run raw container, database, SSH, Drive, or Cloudflare commands.
- **FR-027**: Responses MUST redact secrets and bound child-process output.
- **FR-028**: Existing Hermes local backup remains behind a compatibility facade; the rejected broad Drive snapshot routine MUST NOT be selectable by new profiles.
- **FR-029**: A fresh-server drill MUST prove reconstruction from a clean checkout, approved secrets, and a recovery-set identifier.
- **FR-030**: Production restore, deletion, schedule activation, and public-access mutation each require specific protected confirmation.

### Key Entities

- **Recovery Profile**: Declaration of valuable state, capture, restore, verification, and retention semantics.
- **Artifact Plan**: Resolved source, exclusions, consistency operation, impact, and dependencies.
- **Recovery Set**: One coherent capture attempt with encrypted artifacts and a manifest published after verification.
- **Recovery Manifest**: Non-secret provenance and integrity record.
- **Restore Plan**: Ordered verification, quiesce, replacement, validation, and rollback actions.
- **Schedule Policy**: Frequency, lock, thresholds, profile selection, retry, and retention behavior.
- **Retention Candidate**: Remote object eligible for reviewed deletion under safety floors.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Planning accounts for 100% of initial-profile sources as included, excluded, or deferred with rationale.
- **SC-002**: Fixture capture through integrity verification completes through Sandbox with no secret sentinel in output or manifests.
- **SC-003**: Injected failures at every capture/upload/publication stage create zero complete manifests and leave prior sets unchanged.
- **SC-004**: Restore plan mode causes zero filesystem, service, database, Drive, or Cloudflare mutations.
- **SC-005**: A disposable fresh-server drill restores every selected fixture and passes all declared checks.
- **SC-006**: Overlap tests show at most one active run and no capture under configured resource floors.
- **SC-007**: Retention never selects the newest set, only verified set, or objects outside the configured destination prefix.
- **SC-008**: Existing local WordPress, remote hosting, Hermes status, and public dashboard behavior show no unexplained drift.
- **SC-009**: All operations are callable through CLI and MCP without central bootstrap-list edits.
- **SC-010**: The repository contains all schemas, modules, commands, tests, and fresh-server instructions needed from scratch.

## Assumptions

- Google Drive remains the encrypted off-server destination through the approved remote integration.
- The current passphrase comes from the sourced `RECOVERY_PASSPHRASE` environment variable and is never printed, inspected beyond non-empty presence, or persisted by Sandbox.
- Committed code is available from Git remotes; recovery stores provenance and critical unpublished deltas, not duplicate source trees by default.
- `lenzora.app` needs its production-consistent PostgreSQL database and discovered persistent `/app/storage`; development storage and caches are excluded.
- `alimuzzaman.me` currently recovers from its clean Git checkout because discovery found no persistent mount; partial filesystem protection activates if a valuable non-Git path is later declared.
- `amarsonar-bangla` needs its database and full WordPress directory because local files and uploads are valuable; only explicitly reviewed transient caches may be excluded.
- Legacy broad archives use an unknown passphrase and become candidates only after a new verified set succeeds.
- Schedule activation and destructive cleanup remain protected even though their implementation and dry-run tooling are authorized.
