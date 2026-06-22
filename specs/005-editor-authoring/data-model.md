# Data Model: AI Editor Authoring

See [research.md](./research.md) for the full data-model deep-dive; this is the
implementation-facing summary.

## Element/Widget node (Elementor)

Stored in `_elementor_data` (JSON tree). Node:
`{ id (7-hex), elType (section|column|container|widget), widgetType?, settings{}, elements[] }`.
Supporting post meta: `_elementor_edit_mode=builder`, `_elementor_version`,
`_wp_page_template` (e.g. `elementor_canvas` for full-width). Settings keys = control
IDs; complex controls: responsive (`k`/`k_tablet`/`k_mobile`), media `{id,url}`,
typography group `{prefix}_typography:"custom"` + `{prefix}_font_*`, repeater rows with
7-hex `_id`. Persist via `Document::save(['elements'=>$tree])`.

## Block (Gutenberg/EB)

Stored in `post_content` as `<!-- wp:essential-blocks/x {attrs} -->…`. Attributes per
block.json; each block carries a unique `blockId` and (for EB) `blockMeta`
(blockId-scoped minified desktop/tab/mobile CSS, assembled lazily into
`uploads/eb-style/eb-style-<postId>.min.css`; server never recomputes). Dynamic blocks
(render_callback) vs static (JS `save()` — byte-validated). Parent/child via
`providesContext`/`usesContext` (child `parentBlockId` + mirrored `inherited*`).

## Block spec (finalizer input)

Attribute-level `{ name, attributes, innerBlocks }` (recursive) — NOT raw markup. The
finalizer serializes it via the block's own JS in a real editor.

## Schema (introspected)

Per builder: Elementor widget → control IDs/types/defaults (from `widgets_manager`); EB
block → block.json `attributes`. Cached `runtime/schemas/<instance>/{elementor,gutenberg}.json`;
refreshable live. EB `src/controls` submodule needed for complete attribute sources.

## Finalization job (EB)

Queue entry in the finalizer mu-plugin: `{ target_post, block_spec, status (queued|
running|done|failed), base_content_hash }`. A headless `visit` session opens the real
editor, runs `createBlock → serialize → parse → validateBlock`, writes back valid
`post_content` (EB CSS generated as a side effect); the agent polls a completion marker.

## Operations (abilities, on the 003 layer)

Registered as `sandbox/<kebab>` per 003's convention (analysis I1):
`sandbox/gutenberg-get|insert|update|delete`, `sandbox/elementor-get|insert|update|delete`,
`sandbox/editor-schema`. The `gutenberg_*`/`elementor_*` snake_case are the Python-MCP
proxy tool names (1:1). All: capability-checked **inline per ability**; destructive ops
flagged + gated; read-before-write (address by id/blockId, not position) with
base-state conflict rejection.
