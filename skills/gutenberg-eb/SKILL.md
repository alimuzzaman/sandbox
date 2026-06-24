---
name: gutenberg-eb
description: Insert/modify Gutenberg & Essential Blocks blocks programmatically — when authoring or editing block content (essential-blocks/*) on a post/page without the browser editor.
---

# gutenberg-eb

Author Gutenberg/Essential Blocks content via the in-instance abilities (spec 003/005).

## Insert
`sandbox/gutenberg-insert` (or `sandbox_editor_gutenberg_insert`) —
`{post_id, name, attributes?, inner_html?, inner_blocks?, base_hash?}`.
Parses `post_content` → appends the block → `serialize_blocks`. A unique `blockId` is
auto-added (EB keys per-block CSS off it); nested `inner_blocks` get `parentBlockId`.
Returns the `blockId` + a `state_hash` for the next edit.

## Read / modify / delete
- `sandbox/gutenberg-get {post_id}` → parsed tree (name + blockId + attr keys + children) +
  `state_hash` (read-before-write).
- `sandbox/gutenberg-update {post_id, block_id, attributes}` — locate by **blockId**, merge
  attrs, re-serialize.
- `sandbox/gutenberg-delete {post_id, block_id, confirm:true}` — destructive; needs `confirm`.

## Dynamic vs static (the validity rule)
- **Dynamic** EB blocks (PHP `render_callback`) render from attributes — direct insert is safe
  and renders styled immediately.
- **Static** blocks must byte-match their JS `save()` output or the editor flags
  "invalid/recovery". For those use the **real-editor finalizer**:
  `sandbox/gutenberg-finalize {post_id, block_spec}` queues an attribute spec and returns a
  `job_id`; drive `finalizer_url` headlessly with `visit`, then poll
  `sandbox_eb_finalizer_status('<job_id>')` until `done`. The finalizer page runs real
  `wp.blocks` (with `registerCoreBlocks()` + EB's editor assets) to emit canonical save
  markup — no human step. Batched jobs on one post chain; an external edit since enqueue is
  caught (base-content-hash). See `memory/plugin-behavior/eb-finalizer.md`.

## Guards (shared)
All-raw-HTML insert (no `name`) is refused; deprecated slugs are refused with a replacement
(filter `sandbox_editor_deprecated`); a stale `base_hash` returns `conflict`.

## Schema
`sandbox/editor-schema {builder:"gutenberg", eb_only:true}` lists blocks + whether each is
dynamic + its attribute keys; add `name:"essential-blocks/..."` for one block's attributes +
defaults + the `eb_attribute_fidelity` flag (full only with EB's `src/controls` checkout).
