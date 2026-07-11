# Research: Hermes State Sync

## Decisions

- Use the existing named-remote SSH abstraction and local Git CLI credentials. This
  avoids placing a second GitHub token on the remote and keeps organization access
  out of the Hermes runtime.
- Store a versioned manifest plus an explicit allowlist. Provider auth, sessions,
  checkpoints, logs, databases, worktrees, and binaries are never state artifacts.
- Stage and validate before replacement or push. Conflicts and secret-like content
  fail closed rather than being auto-merged or redacted silently.
- Setup restore is opt-in when a state repository is configured and remains a no-op
  when it is absent, preserving existing installations.

## Alternatives rejected

- Mirroring all of `$HOME/.hermes`: rejected because it includes OAuth/session
  databases, cookies, logs, and runtime binaries.
- Remote-side cron push: rejected for v1 because it requires persistent GitHub
  credentials on the remote and makes external writes less observable.
- GitHub organization storage: rejected because the operator requires personal-repo
  scope only.
