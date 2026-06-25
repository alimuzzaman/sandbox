# Essential Blocks attribute schema — how the real attribute set is assembled (spec 011)

EB blocks declare **0 attributes in `block.json`** (both the registered `assets/` copy and
`src/`). The registered `WP_Block_Type` therefore exposes only the ~3 generic WP-injected keys
(`lock`, `metadata`, `className`). The **real** attribute set (hundreds per block) is assembled at
**JS runtime** from:

- `src/blocks/<name>/src/attributes.js` **or** `src/blocks/<name>/src/components/attributes.js`
  (some blocks, e.g. `button`, `call-to-action`, keep it under `components/`) — explicit attribute
  keys **plus** `...generateXxxAttributes(PREFIX, opts?)` spreads, and
- generator helpers in the controls package `src/controls/src/helpers/*.js`, which expand each
  spread into a deterministic key-family for the block's prefix constant.

`titleText` (not `title`) is `advanced-heading`'s content attribute — inserting with a guessed
`title` is why it stored nothing useful. Verified counts (full resolution): advanced-heading **787**,
accordion 2378, advanced-tabs 1554, countdown 1321, flipbox 1054, call-to-action 570, social-share
473, advanced-image 452, dual-button 143, button 21.

## Generators and their helper files (the complete set)

| Generator | Helper file | keys/prefix (verified) |
|---|---|---|
| `generateTypographyAttributes` | `typoHelpers.js` | 24 (12 desktop + 6 tab + 6 mob) |
| `generateDimensionsAttributes` | `dimensionHelpers.js` | 16 |
| `generateBorderShadowAttributes` | `borderShadowHelpers.js` | 85 (21 own + 4 nested dimensions) |
| `generateBackgroundAttributes` | `backgroundHelpers.js` | 155 |
| `generateResponsiveRangeAttributes` | `responsiveRangeHelpers.js` | 7 |
| `generateResponsiveAlignAttributes` | `responsiveAlignControlHelpers.js` | 3 |
| `generateShapeDividerAttributes` | `shapeDividerHelpers.js` | — |
| `generateResponsiveSelectControlAttributes` | `responsiveSelectControlHelpers.js` | — |
| `generateTextControllerAttributes` | `responsiveTextControllerHelpers.js` | — |

`generateBorderShadowAttributes` **nests** four `generateDimensionsAttributes` calls
(`Bdr_`, `Rds_`, `HRds_`, `HBdr_`) — the resolver expands nested spreads recursively.

## The `editor-schema` resolver (in-instance, `sandbox-editor.php`)

`sandbox/editor-schema` for a named EB block now reads the source and returns the full attribute
set + a structured `fidelity` report (`level`: full | partial | reduced, `count`, `source_checkout`,
`unresolved`, `reason`). Discovery scans, in order: an optional `source_root` input, the EB plugins
WP loads from (`WP_PLUGIN_DIR/essential-blocks`, `…-pro`, resolving symlinks to mounted source), and
a `SANDBOX_PLUGINS_HOST` plugin-home if that env is set.

**Container-visibility rule (the gotcha):** the resolver runs as in-instance PHP, so it can only read
paths visible **inside the container**. The `.org` build ships `attributes.js` (→ explicit attrs,
incl. `titleText`, at `partial` fidelity) but **NOT** `src/controls` → generators can't expand. **Full
fidelity requires `src/controls/src/helpers` to be reachable**, i.e. the active EB plugin (or a
mapped source root) must be a full source checkout, not the `.org` build. `SANDBOX_PLUGINS_HOST` is
**not** exported to the container runtime (only into the compose `.env` for `${...}` substitution),
so `getenv()` returns false there — do not rely on it; the `WP_PLUGIN_DIR` scan is the reliable path.

## Render ≠ schema (important boundary)

Fixing the attribute name is necessary but **not sufficient** to render. `advanced-heading` (and
similar) ship a real `save.js` — they are effectively **static** blocks that render from saved
`save()` markup, not purely from attributes. A `gutenberg-insert` self-closing block (no inner
markup) renders empty even with the correct `titleText`. Non-empty render of such blocks requires the
**headless finalizer** (`sandbox/gutenberg-finalize`, spec 005 US5) to emit canonical save markup.
The editor-schema resolver's job is to surface the correct attribute names/types — which it does;
render fidelity is the finalizer's job.

See [[eb-finalizer]].
