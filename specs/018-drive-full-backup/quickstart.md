# Google Drive Full Backup Quickstart

> Superseded: use the scoped recovery workflow in
> [`specs/023-scoped-recovery-profiles/quickstart.md`](../023-scoped-recovery-profiles/quickstart.md)
> and [`docs/recovery.md`](../../docs/recovery.md) for current commands, safety gates, and
> acceptance evidence. The commands below are retained only as historical evidence.

Full is the default scope.

Before using the commands, install `rclone` on the remote and run `rclone
config` for a Drive remote named `gdrive` using the `drive.file` scope. Rclone
will create and own the private `hermes-full-recovery` backup folder.

```bash
./sb hermes drive setup --remote scaleway-sandbox --drive-destination gdrive:hermes-full-recovery
./sb hermes drive backup --remote scaleway-sandbox --passphrase-stdin --confirm
./sb hermes drive list --remote scaleway-sandbox --json
printf '%s' "$RECOVERY_PASSPHRASE" | ./sb hermes drive restore \
  --remote replacement-remote --backup-id BACKUP_ID --passphrase-stdin --confirm
```

The passphrase must come from the operator's password manager. It is not stored
on Drive, in the remote, or in Sandbox configuration. Test restore only on a
disposable replacement remote before resetting the primary server.

## Live acceptance attempt — 2026-07-16

Drive setup succeeded on `scaleway-sandbox`, and `drive list` continued to show
only the two pre-existing manifest objects. The authorized encrypted backup was
attempted through passphrase stdin, but the remote streamed command terminated
before returning a backup manifest after stale disposable-instance snapshot
handling. No new manifest or restore was claimed; T007, T009, T011, and T012
remain open pending successful bounded backup and disposable restore verification.
