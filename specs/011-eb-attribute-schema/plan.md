# Implementation Plan: EB-Aware Attribute-Schema Resolver for `editor-schema`

**Branch**: `011-eb-attribute-schema` | **Date**: 2026-06-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/011-eb-attribute-schema/spec.md`

## Summary

`editor-schema` currently reports ~3 attributes for an Essential Blocks block because it reads
attributes from the registered `WP_Block_Type` (block.json-derived), and EB declares 0 attributes
in block.json — the real ~787-attribute surface is assembled at JS runtime from
`src/blocks/<name>/src/attributes.js` plus generator helpers in `@essential-blocks/controls`.

The fix extends the existing in-instance `sandbox_editor_schema()` ability (in
`sandbox/assets/abilities/sandbox-editor.php`) with an EB resolver: when a named EB block is
requested, it scans the bind-mounted plugin-home tree for an EB source checkout, parses that
block's `attributes.js` for explicit attributes and generator-spread calls, and expands each
generator by reading the helper definitions in the controls package — deriving the full attribute
set (name + type + default) from source without executing the JS build. Results are cached keyed by
block + source fingerprint and invalidated on source change. Every EB response carries a fidelity
report (full / partial / reduced + count + which checkout). Elementor and core-block paths are
untouched.

## Technical Context

**Language/Version**: PHP 7.4+ (the in-instance ability runtime; matches the WP image's PHP). No
new host-side Python is required for the resolver itself.

**Primary Dependencies**: WordPress 6.9 Abilities API (the ability is already registered);
`WP_Block_Type_Registry` (existing, for the reduced-fidelity fallback + dynamic flag); the EB source
checkout on disk (`src/blocks/<name>/src/attributes.js`, `src/controls/src/helpers/*.js`). No
Composer/vendor additions — the resolver is hand-rolled lightweight JS-literal parsing in PHP.

**Storage**: Resolved schemas cached via a WordPress transient keyed by block name + a source
fingerprint (checkout path + mtime of the relevant source files). No DB schema changes.

**Testing**: Live-stack verification per constitution — `editor-schema` calls against the running
`templately-fsi-rewrite` instance (which has both the org build and access to the dev checkouts),
captured as evidence. Plus the sandbox's PHP unit harness for the pure parser/expander functions
where they can be exercised without WP.

**Target Platform**: The in-instance mu-plugin runtime inside the project's WP container (nginx/fpm,
apache, or litespeed). The EB source checkout is visible because `plugins_home` is bind-mounted at
its same absolute path and exported as `SANDBOX_PLUGINS_HOST`.

**Project Type**: Single-component enhancement to one existing ability file (no new service, no new
MCP tool, no new CLI command).

**Performance Goals**: Single named-block resolution < 300 ms cold (parse + expand), near-instant on
cache hit. Whole-builder `eb_only` listing stays at today's cost (no per-block deep resolution unless
a block is named or full resolution is explicitly requested).

**Constraints**: The resolver can only read paths visible inside the container — i.e. under the
bind-mounted `plugins_home` (or an explicitly mapped plugin path). EB source checkouts outside the
mounted tree are not resolvable in-instance and MUST degrade to reduced fidelity with a clear reason
(honest degradation, US2). No mutation of plugin source or instance state (idempotent reads).

**Scale/Scope**: ~82 EB free blocks + EB Pro blocks; the most attribute-heavy single block resolves
to ~800 attributes. One ability function path changes; six generator helper families to expand
(typography, dimensions, border/shadow, background, responsive range, responsive align), with
border/shadow nesting dimensions.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Is the Only Instance Model** — PASS. No instance-model change; the resolver runs
  inside whichever project instance invokes the ability. No global/fallback instance introduced.
- **II. The Registry Is the Single Source of Truth** — PASS. No change to project→instance mapping or
  resolution precedence. The plugin-home scan uses the already-mounted `SANDBOX_PLUGINS_HOST`, not a
  new resolution authority.
- **III. Single Entry File, Modular Package** — PASS / N/A. No change to `sb` or the `sandbox/`
  package shape; the change lives in the in-instance ability asset
  (`sandbox/assets/abilities/sandbox-editor.php`), which is bundled, not part of the CLI entry.
- **IV. Live-Stack Verification Is the Only Proof of Done** — PASS (enforced). Success criteria are
  defined as live `editor-schema` calls (SC-001..SC-006); quickstart.md captures the exact runnable
  checks. Code reading is not accepted as proof.
- **V. Idempotency and Docs-With-Code** — PASS. Resolution is read-only and re-runnable; the cache is
  safe to rebuild. The change ships with updates to the `gutenberg-eb` SKILL, the editor-schema notes
  in CLAUDE.md (gotcha #17 wording about reduced fidelity), and a `memory/plugin-behavior/` note on
  EB attribute resolution.
- **VI. Feature Parity Before Removal** — PASS. Additive only; the reduced-fidelity fallback (today's
  behavior) is preserved as the no-source path. Nothing is removed.

**Gate result: PASS — no violations, Complexity Tracking not required.**

## Project Structure

### Documentation (this feature)

```text
specs/011-eb-attribute-schema/
├── plan.md              # This file
├── research.md          # Phase 0 output — design decisions
├── data-model.md        # Phase 1 output — entities
├── quickstart.md        # Phase 1 output — live validation guide
├── contracts/
│   └── editor-schema-eb.md   # editor-schema EB request/response contract
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
sandbox/assets/abilities/
└── sandbox-editor.php        # CHANGED: sandbox_editor_schema() EB branch gains the resolver;
                              #   new helper functions for source discovery, attributes.js parsing,
                              #   generator expansion, fidelity reporting, and transient caching.

skills/gutenberg-eb/
└── SKILL.md                  # CHANGED (docs-with-code): describe full-fidelity EB schema +
                              #   the source-checkout requirement and fidelity field.

memory/plugin-behavior/
└── eb-attribute-schema.md    # NEW: how EB declares 0 attrs in block.json and assembles them in JS;
                              #   the generator key-families; the resolver's source-scan + mount rule.

CLAUDE.md                     # CHANGED: gotcha #17 reduced-fidelity wording updated to reflect the
                              #   new resolver and its plugin-home-mount constraint.
```

**Structure Decision**: Single in-instance ability file is the unit of change
(`sandbox/assets/abilities/sandbox-editor.php`). This keeps the feature within the spec's stated
scope ("editor-schema ability only"), avoids a new MCP tool or CLI command, and leverages the
existing same-path `plugins_home` bind mount so in-container PHP can read the dev's EB source. The
resolver is decomposed into small pure functions (discover → parse → expand → cache → report) so the
parse/expand logic is unit-testable independent of WP.

## Complexity Tracking

> No constitution violations — section intentionally empty.
