# Data Model: Hermes State Sync

| Entity | Contents | Exclusions |
|---|---|---|
| StateRepository | private Git URL, default branch, configured remote | URL userinfo and embedded tokens |
| StateManifest | schema, source remote, snapshot revision, allowlisted paths, exclusions | prompt bodies and raw credentials |
| SanitizedSnapshot | profile, Sandbox integration metadata, policy, memory, user-authored harness files | auth, sessions, checkpoints, logs, databases, binaries |

Snapshots are staged outside the active Hermes home. Restore validates paths and
content, then atomically replaces only allowlisted files. A failed validation leaves
the existing state unchanged.
