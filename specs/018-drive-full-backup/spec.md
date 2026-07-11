# Feature Specification: Google Drive Full Backup

**Feature Branch**: `018-drive-full-backup`

**Created**: 2026-07-11

**Status**: Ready for planning

**Input**: User description: "Create a Google Drive alternative to Git backup. Full encrypted backup and restore is the default."

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

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- Remote interrupted during backup: upload resumes or a new immutable backup is created; incomplete archives are not marked restorable.
- An instance cannot create a logical snapshot because its port is occupied: capture its stopped database volume into the encrypted archive without stopping the conflicting service.
- Drive is unreachable: no source state is removed and no successful backup result is reported.
- Archive exceeds a normal request size: resumable upload is used.
- Runtime containers or caches are present: omit them; record the recreation instructions in the manifest.
- A provider cannot authenticate after restore: restore completes the files and reports that operator login is still required.

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: `sb hermes drive backup` MUST default to a full backup scope.
- **FR-002**: Full scope MUST include Hermes chats, sessions, checkpoints, profiles, memories, skills, provider/Git credentials, managed repositories, worktrees, uncommitted files, Sandbox metadata, WordPress database exports, and uploads.
- **FR-003**: Full scope MUST exclude recreatable container images, package caches, and transient runtime sockets while documenting their recreation requirements.
- **FR-004**: Every archive MUST be compressed, encrypted client-side with an operator recovery passphrase, integrity-checked, and uploaded only as encrypted data.
- **FR-005**: Passphrases and decrypted contents MUST never be stored in command arguments, persistent configuration, logs, manifests, Git, Drive metadata, or result envelopes.
- **FR-006**: Backup MUST create immutable timestamped recovery points and retain a configurable bounded history.
- **FR-007**: Restore MUST require explicit confirmation, verify integrity before mutation, stage files atomically, and preserve the pre-restore state until success.
- **FR-008**: Drive access MUST be least-privilege and limited to the application backup folder/files.
- **FR-009**: Existing Git state sync remains a separate sanitized configuration mirror and does not substitute for Drive full recovery.

*Example of marking unclear requirements:*

- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified - email/password, SSO, OAuth?]
- **FR-007**: System MUST retain user data for [NEEDS CLARIFICATION: retention period not specified]

### Key Entities *(include if feature involves data)*

- **DriveBackupConfiguration**: destination folder, retention, default scope, and non-secret provider reference.
- **RecoveryPoint**: timestamped encrypted archive, plaintext checksum, encrypted checksum, manifest, scope, and status.
- **RecoveryManifest**: versioned non-secret inventory, archive format, restore instructions, and excluded runtime assets.

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: A default backup produces one encrypted full recovery point with a verifiable manifest.
- **SC-002**: A clean replacement remote restores a fixture archive with all declared state checksums present and no plaintext secrets left in Drive.
- **SC-003**: Interrupted/invalid backup or restore leaves the source and target usable with no partial destructive mutation.
- **SC-004**: Existing state sync and Hermes setup remain functional when Drive backup is unconfigured.

## Assumptions

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right assumptions based on reasonable defaults
  chosen when the feature description did not specify certain details.
-->

- The operator keeps the recovery passphrase in an external password manager; it is not recoverable from the server or Drive.
- Google Drive is a private personal destination using the narrowest available application/file scope.
- A full restore recreates containers from configuration; it does not copy live Docker layers.
- Full backup requires temporary remote disk space approximately equal to the compressed archive.
