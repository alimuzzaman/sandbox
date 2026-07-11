# CLI Contract: Google Drive Full Backup

- `sb hermes drive setup --remote NAME` configures the private Drive destination
  through the authenticated connector; it stores only a destination reference.
- `sb hermes drive backup --remote NAME --passphrase-stdin --confirm` creates an
  encrypted full recovery point by default.
- `sb hermes drive list --remote NAME` returns non-sensitive recovery-point
  metadata.
- `sb hermes drive restore --remote NAME --backup-id ID --passphrase-stdin
  --confirm` verifies/decrypts/restores one recovery point atomically.
