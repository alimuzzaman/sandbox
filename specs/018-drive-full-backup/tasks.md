# Tasks: Google Drive Full Backup

## Phase 1: Setup

- [X] T001 Add Drive CLI command/options in `sandbox/cli.py` and `sandbox/commands/hermes.py`
- [X] T002 Create the private `Hermes Full Recovery Backups` Drive folder

## Phase 2: Foundational

- [X] T003 Add Drive destination validation and secret-safe SSH stdin handling in `sandbox/core/_hermes.py`
- [X] T004 Add Drive command and destination validation coverage in `tests/test_hermes.py`

## Phase 3: User Story 1 - Full Recovery Backup (P1)

- [X] T005 [US1] Implement default full archive, database snapshot capture, GPG encryption, manifest, and rclone upload in `sandbox/core/_hermes.py`
- [X] T006 [US1] Add full-backup scope and encryption documentation in `docs/hermes-agent.md`
- [ ] T007 [US1] Configure `rclone` against the created Drive folder on the remote and perform a live encrypted backup

## Phase 4: User Story 2 - Restore (P2)

- [X] T008 [US2] Implement validated staged Drive restore in `sandbox/core/_hermes.py`
- [ ] T009 [US2] Restore a recovery point onto a disposable remote and verify chats, session state, repository worktrees, database snapshots, and uploads

## Phase 5: User Story 3 - Sensitive State (P3)

- [X] T010 [US3] Ensure passphrases use standard input and result envelopes contain only recovery metadata in `sandbox/core/_hermes.py`
- [ ] T011 [US3] Verify Drive contains only ciphertext plus a non-sensitive manifest

## Phase 6: Verification

- [ ] T012 Run full local suite and live Drive acceptance in `specs/018-drive-full-backup/quickstart.md`
