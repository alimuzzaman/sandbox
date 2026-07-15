# Feature Specification: Google Drive Full Backup

**Feature Branch**: `018-drive-full-backup`

**Created**: 2026-07-11

**Status**: Superseded by [scoped recovery profiles](../023-scoped-recovery-profiles/spec.md)

**Input**: User description: "Create a Google Drive alternative to Git backup. Full encrypted backup and restore is the default."

## Clarifications

### Session 2026-07-16

- Q: Which authorization model applies to this historical backup request? → A: The existing single-operator Sandbox control plane and inherited passphrase channel; this feature does not introduce application-user authentication.
- Q: What retention policy applies? → A: The canonical scoped-recovery policy in `specs/023-scoped-recovery-profiles`, using verified-set keep-count and minimum-age floors; this historical feature does not define a separate fixed retention period.
- Q: Which feature owns implementation and acceptance? → A: `specs/023-scoped-recovery-profiles`; this directory remains an archival record of the superseded broad-backup proposal.

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Create Full Recovery Backup (Priority: P1)

As the operator, I can create one encrypted Google Drive archive that captures all state needed to reconstruct Hermes and Sandbox after losing the remote server.

**Why this priority**: A recoverable off-server copy is required before resetting the remote.

**Independent Test**: Create a full backup fixture, encrypt it, upload it to a fake Drive client, and verify its signed manifest declares chats, sessions, files, repositories, Sandbox metadata, and database exports.

**Acceptance Scenarios**:

1. **Given** a configured Drive destination and recovery passphrase, **When** backup runs without a scope option, **Then** it creates a full encrypted archive and immutable manifest.
2. **Given** source state changes, **When** another backup runs, **Then** it creates a distinct timestamped recovery point without overwriting the prior archive.

---

### User Story 2 - Restore on a Replacement Remote (Priority: P2)

As the operator, I can download, authenticate, decrypt, verify, and restore a selected full backup onto a clean replacement remote.

**Why this priority**: An archive is useful only when it restores predictably.

**Independent Test**: Restore a fixture archive into a clean staging root and verify file checksums, repository worktrees, and state database are present while runtime containers are recreated rather than copied.

**Acceptance Scenarios**:

1. **Given** a valid archive and passphrase, **When** restore runs with confirmation, **Then** it verifies the manifest and atomically stages the archive before replacing eligible state.
2. **Given** a wrong passphrase, modified archive, or incomplete manifest, **When** restore runs, **Then** it fails without mutating the target.

---

### User Story 3 - Protect Sensitive Contents (Priority: P3)

As the operator, I can store chats, sessions, provider authentication, credentials, and uncommitted files in the backup without exposing them to Google Drive.

**Why this priority**: Full continuity requires sensitive state, so client-side encryption is mandatory.

**Independent Test**: Inspect uploaded artifacts and logs to confirm no plaintext archive, passphrase, provider token, chat body, or file content is present.

**Acceptance Scenarios**:

1. **Given** sensitive state, **When** the archive uploads, **Then** Drive receives only encrypted bytes plus non-sensitive inventory metadata.
2. **Given** an archive key is absent from the server and Drive, **When** an unauthorized party downloads the archive, **Then** it cannot recover contents.

---

These scenarios are retained for historical traceability. New implementation and acceptance
work belongs to the scoped recovery feature referenced above.

### Edge Cases

- Remote interrupted during backup: upload resumes or a new immutable backup is created; incomplete archives are not marked restorable.
- An instance cannot create a logical snapshot because its port is occupied: capture its stopped database volume into the encrypted archive without stopping the conflicting service.
- Drive is unreachable: no source state is removed and no successful backup result is reported.
- Archive exceeds a normal request size: resumable upload is used.
- Runtime containers or caches are present: omit them; record the recreation instructions in the manifest.
- A provider cannot authenticate after restore: restore completes the files and reports that operator login is still required.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `sb hermes drive backup` MUST default to a full backup scope.
- **FR-002**: Full scope MUST include Hermes chats, sessions, checkpoints, profiles, memories, skills, provider/Git credentials, managed repositories, worktrees, uncommitted files, Sandbox metadata, WordPress database exports, and uploads.
- **FR-003**: Full scope MUST exclude recreatable container images, package caches, and transient runtime sockets while documenting their recreation requirements.
- **FR-004**: Every archive MUST be compressed, encrypted client-side with an operator recovery passphrase, integrity-checked, and uploaded only as encrypted data.
- **FR-005**: Passphrases and decrypted contents MUST never be stored in command arguments, persistent configuration, logs, manifests, Git, Drive metadata, or result envelopes.
- **FR-006**: Backup MUST create immutable timestamped recovery points; retention MUST use the verified-set keep-count and minimum-age policy defined by the scoped recovery feature.
- **FR-007**: Restore MUST require explicit confirmation, verify integrity before mutation, stage files atomically, and preserve the pre-restore state until success.
- **FR-008**: Drive access MUST be least-privilege and limited to the application backup folder/files.
- **FR-009**: Existing Git state sync remains a separate sanitized configuration mirror and does not substitute for Drive full recovery.

### Key Entities *(include if feature involves data)*

- **DriveBackupConfiguration**: destination folder, retention, default scope, and non-secret provider reference.
- **RecoveryPoint**: timestamped encrypted archive, plaintext checksum, encrypted checksum, manifest, scope, and status.
- **RecoveryManifest**: versioned non-secret inventory, archive format, restore instructions, and excluded runtime assets.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In fixture verification, a default full capture produces exactly one immutable encrypted recovery point with a verifiable non-sensitive manifest.
- **SC-002**: In a disposable restore drill, every declared artifact checksum is verified before replacement and no plaintext secret is present in destination metadata or operator output.
- **SC-003**: In every interrupted or invalid capture/restore test, the source remains usable, incomplete data is not classified as restorable, and no destructive target replacement occurs.
- **SC-004**: When Drive recovery is unconfigured, existing Hermes state-sync and setup flows continue to pass focused checks without requiring Drive credentials.

## Assumptions

- The operator keeps the recovery passphrase in an external password manager; it is not recoverable from the server or Drive.
- Google Drive is a private personal destination using the narrowest available application/file scope.
- A full restore recreates containers from configuration; it does not copy live Docker layers.
- Full backup requires temporary remote disk space approximately equal to the compressed archive.
- The existing Sandbox remote and inherited secret channel are the only authorization boundary for this historical proposal.
- Live capture, restore, deletion, and schedule activation remain protected actions governed by the scoped-recovery acceptance gates.
