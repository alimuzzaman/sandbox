# Research: Google Drive Full Backup

## Decisions

- Use timestamped immutable encrypted blobs, not Drive file revisions. Drive may
  purge ordinary revisions; retained blob revisions are bounded to 200 per file.
- Use resumable upload for all archive sizes. It is appropriate for interrupted
  transfers and is required once bundles exceed a small request size.
- Use the least-privilege Drive application/file scope and a dedicated private
  folder. The connector/API receives only ciphertext and non-sensitive manifest
  metadata.
- Encrypt with a passphrase through an audited streaming cipher available on the
  remote. The passphrase enters through standard input and is never persisted.
- Full backup includes encrypted state and worktrees, while containers/images and
  package caches are recreated from configuration.

## Alternatives rejected

- Git repository: unsuitable for opaque databases, large worktrees, and secrets.
- Server-side Drive encryption alone: insufficient because Drive OAuth or account
  compromise would expose plaintext chats and credentials.
- Copying Docker layers: non-portable and larger than a deterministic rebuild.
