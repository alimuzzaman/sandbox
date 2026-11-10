# Research: Secrets unset and remote copy

## R1. Removal primitive for unset

- **Decision**: reuse `writer.remove_keys(path, (key,), expected=SourceContinuity)`.
  The service reads the source with `SourceRegistry.read_with_stat`, checks the
  optional opaque revision, parses the document, refuses `key_missing`, captures
  continuity with `ownership.continuity.capture(alias, stat, entry_names)` and
  passes it in. The new revision is computed from the same records `remove_keys`
  keeps (`b"".join(r.raw for r in records if r.key != key)`).
- **Rationale**: `remove_keys` already does lock, metadata continuity before and after
  parse, sibling temp file with preserved mode, fsync and directory fsync. It only
  writes bytes derived from parsed records, never a reconstructed value.
- **Error mapping**: `remove_keys` speaks in cleanup terms. The service maps
  `source_changed` to `revision_conflict` and `cleanup_ineligible` to `key_missing`;
  `journal_locked`, `continuity_unreliable` and `source_unsupported` pass through
  (the last as `syntax_unsupported`, the code `set` uses).
- **Alternatives**: a new `update_source`-style remover using `_atomic_replace`
  (duplicates `remove_keys`); `rewrite_source` with caller-built bytes (weaker
  continuity than `remove_keys`).

## R2. Revision precondition flag

- **Decision**: `--if-revision`, the flag `set` and `organize` already have. The
  brief's "`--expected-revision` as `set` has it" refers to the same thing
  (`expected_revision` is the service keyword).

## R3. Remote transport for copy

- **Decision**: one SSH session through `sandbox.core._remote.ssh_run(entry, command,
  timeout=60, input_data=value_bytes)`, the public helper that existing sensitive
  stdin paths use (MCP bearer token install, `_hermes._ssh_stdin`). The command is:

  ```sh
  home="${SANDBOX_HOME:-$HOME/sandbox}"; exec "$home/sb-src/sb" secrets set \
    --project-dir <quoted dir or "$home"> --source <alias> <key> \
    --stdin --skip-unchanged --json [--create-only|--replace-only]
  ```

  Every operator word is validated first and `shlex.quote`d. `exec` hands the SSH
  stdin straight to `sb`; the value never touches the shell.
- **Rationale**: `sb-src/sb` is the staged runtime that `sb remote service migrate`
  installs (same path `remote_sb_path` returns). One session instead of
  `resolve_sandbox_home` plus a second session. The control service exposes only fixed
  routes (`/resources`, `/inventory`, `/wp-cli`, `/diagnostics`); `/wp-cli` rejects
  stdin, and adding a secret-write route to the control service would widen a network
  surface for a one-shot operator action.
- **Alternatives**: control-service HTTP route (new authority, rejected); durable
  remote job transport (argv only, no stdin); private `_hermes._ssh_stdin` (private).

## R4. Readiness gate

The remote readiness gate (spec 063) guards job submissions and deploys that transfer
a working tree. `copy` transfers no tree and starts no job, like `sb remote ssh` and
the MCP token install, so it does not call `require_ready`. It does require
`entry["provisioned"]`, as the Hermes paths do.

## R5. Idempotency without disclosure

- **Decision**: new opt-in `set --skip-unchanged`. `update_source(...,
  skip_unchanged=True)` returns `("already_current", current_revision)` without
  writing when the key exists and `hmac.compare_digest` of the UTF-8 bytes of the
  stored and incoming values is true. The check runs after the revision check and
  before the intent check, so create-only reruns converge (spec clarification 2).
- **Rationale**: the remote already holds both values at the moment of the write, so
  no digest has to leave either host. A controller-side keyed digest would need a
  shared key and still disclose equality to the controller.
- **Older remote**: argparse rejects the unknown flag with exit 2 and no JSON, which
  the controller maps to `remote_unsupported` without anything written.

## R6. Remote response handling

The controller decodes at most the last 64 KiB of stdout, takes the last non-empty
line, parses JSON and keeps only: `ok` (bool), `action` in
{`created`, `updated`, `already_current`}, `revision` matching `r1_[0-9a-f]{32}`,
`error.code` matching `[a-z][a-z0-9_]{0,63}`. Everything else, and all of stderr, is
dropped. SSH exit 255 maps to `remote_unreachable`; `TimeoutExpired` to
`remote_timeout` (no retry); exit 2 with no JSON to `remote_unsupported`; anything
else without JSON to `remote_failed`. A remote JSON refusal passes its code through
unchanged (`intent_conflict`, `source_unknown`, ...) with the fixed message "the
remote refused the secret update", so the acceptance scenarios read the same locally
and remotely.

## R7. Audit on both sides

- **Controller**: `_operate("copy", source, (key,), "cli", ..., input_channel="remote_stdin",
  destination="remote:<name>/<target_source>/<target_key>")`. `destination` is a new
  optional audit field, emitted only when set, so existing events keep their shape.
  The succeeded outcome carries the remote action in `reason_code`. `revision` stays
  empty because the remote revision is not a revision of the local source.
- **Remote**: its own `set` writes intent and outcome (`input_channel="stdin"`) through
  its own broker; nothing new is needed there.

## R8. Value channel checks

Local value must be non-empty, at most `MAX_VALUE_BYTES` UTF-8 bytes and contain no
CR, LF or NUL (the remote `_stdin_secret` contract). It is sent as
`value.encode() + b"\n"`. No env var is set, `ssh` inherits the caller's env, and no
temp file is created on either side (`update_source` writes a sibling temp of the
*source*, which is the existing atomic-replace behaviour on the remote).
