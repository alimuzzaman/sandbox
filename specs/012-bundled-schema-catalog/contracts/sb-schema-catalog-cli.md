# Contract: `sb schema-catalog` command

**Feature**: 012-bundled-schema-catalog · **Phase 1** · 2026-06-25

New command module (`sandbox/commands/schema_catalog.py`, registry-wide). Maintainer/CI tool that
generates the committed catalog; end users never run it.

## `sb schema-catalog generate [--instance <name>]`

- Drives, on the target instance (free + Pro plugins active):
  - **Elementor**: PHP `get_controls()` over all registered widgets (incl Pro/EA).
  - **Gutenberg**: the headless `wp.blocks.getBlockTypes()` dump page (incl EB Pro).
- Packs the result into the committed, gzipped, version-keyed catalog under
  `sandbox/assets/editor-schema/` + refreshes the index.
- **Output**: a coverage report — per plugin: present/absent, full/partial, version, entry counts,
  and the resulting compressed size. Idempotent (re-running regenerates deterministically).
- **Guardrails**: warns (does not fake) when a plugin/Pro is absent at generation; fails clearly if
  the instance isn't reachable or the dump page errors.

## `sb schema-catalog status`

- Prints, per covered plugin: catalog version vs the version installed in the current/〈--instance〉
  instance (drift), per-builder entry counts, and the committed compressed size vs the ~3MB bound.
- **Output**: read-only; no generation.

## Guarantees
- The committed asset stays compressed (≤~3MB, SC-004); `status` surfaces the size.
- Generation reflects only what was actually registered (honest coverage; FR-002/FR-011).
- Resolves the same `$SANDBOX_HOME` base + instance-resolution rules as the rest of the CLI.
