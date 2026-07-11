# Google Drive Full Backup Quickstart

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
