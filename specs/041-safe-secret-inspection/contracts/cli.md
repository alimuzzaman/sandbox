# CLI Contract

All commands accept `--project-dir` where project configuration is required. Human-readable and `--json` modes contain the same allowed disclosure, except reveal never accepts `--json`.

## Inventory and metadata

```text
sb secrets inspect --source ALIAS [--key KEY ...] [--mode keys|metadata]
                   [--exact-length] [--json] [--project-dir DIR]
```

- Default mode is `keys`.
- `--exact-length` requires metadata mode and exactly one key.
- Result contains source alias, operation, key list or one-key safe state, and audit correlation only.

## Validation

```text
sb secrets validate --source ALIAS --key KEY --profile PROFILE
                    [--json] [--project-dir DIR]
```

- Result contains named check states and `live_checked=false`.

## Fixed mask

```text
sb secrets inspect --source ALIAS --key KEY --mode masked
                   [--json] [--project-dir DIR]
```

- Exactly one key; no offset/width/template options.
- Result contains a fixed mask only when policy permits.

## Use without seeing

```text
sb secrets run --source ALIAS --key KEY [--destination NAME]
               [--timeout-seconds N] [--project-dir DIR] -- ARGV...
```

- Local CLI only for arbitrary direct argv.
- No implicit shell, secret substitution, or parent export.
- Result reports exit/termination, elapsed class, and truncation; redacted output is bounded.

## Targeted update

```text
sb secrets set --source ALIAS KEY [--stdin | --from-ref ALIAS:KEY | --generate PROFILE]
               [--create-only | --replace-only] [--if-revision REV]
               [--profile PROFILE] [--json] [--project-dir DIR]
```

- With no input option, hidden controlling-TTY input is required.
- Plaintext flags, `KEY=value`, environment input, and arbitrary files do not exist.
- Result contains only source, key, created/updated status, validation, and opaque revision.

## Human reveal

```text
sb secrets reveal --source ALIAS --key KEY [--project-dir DIR]
```

- Requires controlling TTY and exact key re-entry after warning.
- Writes the selected value only to the controlling TTY; stdout remains empty.
- No JSON, pipes, redirection, `--yes`, wildcard, batch, or noninteractive form.

## Compatibility

```text
sb secrets migrate-zshrc [--json]
```

Existing behavior remains supported. All `secrets` actions bypass unrelated runtime/Compose reconciliation.

## Stable refusal codes

The transport-neutral service exposes bounded codes including:

`source_unknown`, `source_unsafe`, `source_too_large`, `source_changed`, `syntax_unsupported`, `duplicate_key`, `key_invalid`, `key_missing`, `mode_requires_one_key`, `mask_denied`, `profile_unknown`, `shape_failed`, `audit_unavailable`, `destination_denied`, `command_invalid`, `command_timed_out`, `output_truncated`, `revision_conflict`, `intent_conflict`, `input_invalid`, `tty_required`, and `confirmation_failed`.

## Convergence amendment — 2026-08-13: shared redaction boundary

Before human or JSON rendering, every CLI result and child-process diagnostic
passes through the shared redaction policy. It removes bearer/API assignment
values, common provider token prefixes, private-key markers, and credentials in
URL userinfo or token-like query values. A safe URL may retain scheme, host,
path, and approved non-sensitive query names; it must never retain Basic Auth
userinfo or a credential-bearing query value.

Redaction applies to stdout, stderr, exception chains, command/argv summaries,
feedback references, and remote-verification diagnostics. If a value cannot be
classified safely, the field is omitted or replaced with a stable redaction
marker and the operation fails closed. `--json` and human modes have identical
disclosure; no raw traceback bypass is permitted. This closes `81f43e6f`.
