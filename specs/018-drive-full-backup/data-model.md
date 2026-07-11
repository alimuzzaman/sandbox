# Data Model: Drive Full Backup

| Entity | Fields | Security boundary |
|---|---|---|
| DriveBackupConfig | destination reference, retention count, default `full` scope | no OAuth token or passphrase |
| RecoveryPoint | id, created time, encrypted archive ID, encrypted checksum, manifest ID, byte size | Drive holds ciphertext only |
| RecoveryManifest | archive version, source version, included paths, excluded runtime classes, plaintext checksum | no chat, token, or file contents |
| RestoreTransaction | backup ID, stage root, verify result, final result | pre-restore state retained until final replacement |
