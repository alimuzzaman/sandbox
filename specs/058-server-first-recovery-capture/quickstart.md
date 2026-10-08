# Quickstart: verifying server-first capture and promotion

Steps 1–2 need no remote. Steps 3–7 touch a production remote and need explicit
approval first (see the pending decision on live proof). Never pass a secret as
an argument; the database credential reaches the child only through
`./sb secrets run`, and `RECOVERY_PASSPHRASE` only through the inherited
environment.

## 1. Unit and contract tests (no remote)

```bash
python3 -m unittest tests.test_server_capture_helper tests.test_server_capture \
  tests.test_remote_server_capture tests.test_recovery_promote \
  tests.test_recovery_cli_server_capture tests.test_mcp tests.test_recovery_service \
  tests.test_remote_recovery tests.test_recovery_capture
```

Expected: all pass. The helper tests run the real helper against a temporary
root with fake `docker`, `mariadb`, `mariadb-dump`, `tar` and `loginctl` on `PATH`.

## 2. Memory bound (no remote)

```bash
python3 -m unittest tests.test_server_capture_memory
```

Builds a 2 GB sparse fixture, runs the helper job and the promote transfer and
publication with fakes, and asserts peak RSS < 256 MB for each (SC-007).

## 3. Start a capture (approved live run)

```bash
unset RECOVERY_PASSPHRASE RECOVERY_RCLONE_DESTINATION
./sb remote status <remote>            # runtime_revision_state: match
./sb secrets run … -- ./sb recovery capture --remote <remote> \
  --backup-id <id> --profile amarsonar-bangla-prod --confirm --json
```

Expected: returns in under 30 s with `status` `queued` or `running` (SC-001).

## 4. Status, repeated

```bash
./sb recovery status --remote <remote> --backup-id <id> --json   # during
./sb recovery status --remote <remote> --backup-id <id> --json   # ×3 after completion
./sb recovery list --remote <remote> --json
```

Expected: phases never regress; the three terminal answers are identical;
`members`, `archive` and `inventory_summary` present; listing works with no
Drive configured (SC-002, SC-003, SC-004).

## 5. Replay and conflict

Re-run step 3 with the same id → `existing: true`, same `request_id`.
Start another id while one is running → `capture_in_progress`.

## 6. Promote

```bash
export RECOVERY_RCLONE_DESTINATION=<reviewed test destination>
# RECOVERY_PASSPHRASE inherited from the approved secret channel
./sb recovery promote --remote <remote> --backup-id <id> --confirm --json
./sb recovery verify --backup-id <id> --json
./sb recovery list --remote <remote> --json
```

Expected: `published`; manifest `artifacts[]` hash for the archive equals the
receipt `archive_sha256`; status shows `promoted: true` (SC-006). Running
promote again returns `already_published` with no upload.

## 7. Interruption

Interrupt a promote mid-transfer (Ctrl-C), re-run it: `resumed_from_bytes > 0`
(SC-008). On a disposable capture id, stop the job on the server via
`./sb recovery` tooling only if such a path exists; otherwise rely on the
helper tests for the `incomplete` state. Do not use raw SSH.
