# Suites and timing bounds (macOS)

**Scope**: T076 — unit/contract/integration suites plus the 3-second preflight/status and
20-second warm-start bounds, and `git diff --check`.

**Host**: macOS 15 (Darwin 25.6.0), Compose runtime (the default). 2026-08-02.

## Suites

```text
./sb selftest
  Ran 1770 tests in 30.3s
  OK (skipped=3)
```

## Bounds

```text
native preflight --json      0.30s      bound 3s
native support --json        0.27s      bound 3s
native install-plan --json   0.25s      bound 3s
./sb status                  1.03s      bound 3s
warm ensure (running inst)   6.28s      bound 20s
```

Warm start was measured through the project's own entry point
(`node scripts/sandbox-env.js start`, which calls `./sb ensure`) against an already-running
instance, so it includes registry resolution, config reconciliation, and URL refresh.

## Whitespace

```text
git diff --check   -> clean
```

## Scope note

These are Compose-runtime numbers on darwin. Managed-native preflight on a host where the
managed runtime can actually start (Ubuntu 24.04) is still covered by the pending
`ubuntu-*.md` files; this file does not claim them.
