# Contract: Editor authoring abilities (on the spec 003 layer) + proxies

All register as WP abilities on the spec-003 in-instance Abilities layer (reachable by
external clients + via the Python-MCP proxy). All capability-checked **inline per
ability** (Application Password + `permission_callback` capability — nonces are N/A for
external-client REST/MCP, same model as 003); destructive ops flagged destructive
(eligible for a `confirmationMessage`); reads flagged read-only.

**Naming (analysis I1)**: ability names MUST follow 003's convention —
`sandbox/<kebab-case>`: `sandbox/gutenberg-get`, `sandbox/gutenberg-insert`,
`sandbox/gutenberg-update`, `sandbox/gutenberg-delete`, `sandbox/elementor-get`,
`sandbox/elementor-insert`, `sandbox/elementor-update`, `sandbox/elementor-delete`,
`sandbox/editor-schema`. The snake_case forms below (`gutenberg_insert`, …) are the
**Python-MCP proxy tool names** in `tools/editor.py`, which map 1:1 to those abilities.

## Gutenberg / EB (ships first)

### `gutenberg_get(post_id)` → compact parsed-block tree (`parse_blocks`).
### `gutenberg_insert(post_id, block_spec, position?)`, `gutenberg_update(post_id, selector, attrs)`, `gutenberg_delete(post_id, selector)`
(Full CRUD, matching FR-004 + the data model. `selector` = `blockId`/path.)
- `block_spec` = attribute-level `{name, attributes, innerBlocks}` (not raw markup).
- Dynamic blocks → may write markup directly; **static/third-party → routed to the finalizer**.
- Enforce unique `blockId`; carry `blockMeta`; set child `parentBlockId`+`inherited*` for nested.
- Address by `blockId`/path (not position); read-before-write.
- Refuse all-raw-HTML; refuse deprecated blocks with a suggested replacement.

### EB finalizer (mu-plugin + `visit`)
- Queue a `block_spec` → headless editor serializes via `wp.blocks` JS → writes valid `post_content` (EB CSS generated) → agent polls a completion marker. No human step.

## Elementor / EA (ships second)

### `elementor_get(post_id)` → element tree (`get_elements_data()`).
### `elementor_insert(post_id, widget, settings, parent?)` / `elementor_update(post_id, element_id, settings)` / `elementor_delete(post_id, element_id)`
- Build node(s) with 7-hex IDs; persist via `Document::save(['elements'=>$tree])` as `--user=admin`.
- Enable a required EA widget first + verify the node survived (else silently dropped).
- Regenerate CSS; set `_wp_page_template` for full-width; fill media `{id,url}`.
- Raw-meta fallback only if needed: `wp_slash`+`_elementor_css` delete.
- Address by `id`; read-before-write.

## Schema

### `editor_schema(builder, name?)`
- `builder ∈ {elementor, gutenberg}`. With `name` → that widget/block's settings/attributes (types, defaults), introspected live. Without → list all registered EA widgets / EB blocks on the instance. Cached under `runtime/schemas/<instance>/`.

## Notes
- Depends on spec 003. New MCP proxies ⇒ Claude Code restart (gotcha #4).
- EB `src/controls` submodule must be checked out for complete `editor_schema`.
