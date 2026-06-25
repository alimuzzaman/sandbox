# Contract: `sb license` command

**Feature**: 013-pro-license-activation · **Phase 1** · 2026-06-25

A new command module (`sandbox/commands/license.py`, registered via the registry). Manages the global
license secrets + primary designation without ever echoing a key value.

## `sb license set <family> <key>`

- `<family>` ∈ `elementor` | `wpdeveloper`.
- Writes `<key>` to the gitignored per-machine secret store under `$SANDBOX_HOME` (chmod 600).
- Sets the corresponding `*_present` flag in the central licensing state.
- Optionally re-provisions running instances (writes refreshed `sandbox-licensing.json`) so the key
  takes effect without a manual `apply`.
- **Output**: confirms the family was set; MUST NOT print the key value. (SC-005)
- Idempotent: re-running with a new key replaces it.

## `sb license status`

- Prints, for each family: whether a key is set (`set`/`not set`) and a MASKED hint (e.g. last 4
  chars), never the full value.
- Prints the current Elementor primary instance + URL (or `none`).
- **Output**: no secret values.

## `sb license clear [<family>]`

- Removes the key for `<family>` (or both if omitted) from the secret store and clears the
  `*_present` flag.
- Optionally re-provisions running instances so they revert to today's behavior.
- **Output**: confirms the clear; no secret values.

## Guarantees

- The key value appears in zero command outputs and zero tracked files. (FR-007, SC-005)
- With no key set, every Pro plugin behaves exactly as today. (FR-005)
- Resolves the same `$SANDBOX_HOME` base as the rest of the CLI (spec 009 seam).
- Commands that touch instances follow the standard instance-resolution rules; `set`/`clear`
  re-provision is opt-in and scoped to running instances.
