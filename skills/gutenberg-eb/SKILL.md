---
name: gutenberg-eb
description: Insert/modify Gutenberg & Essential Blocks blocks programmatically — when authoring or editing block content (essential-blocks/*) on a post/page without the browser editor.
---

# gutenberg-eb

Author Gutenberg/Essential Blocks content via the in-instance abilities (spec 003/005).

## Insert
`sandbox/gutenberg-insert` (or `sandbox_editor_gutenberg_insert`) — `{post_id, name, attributes?, inner_html?}`.
Parses `post_content` → appends the block → `serialize_blocks`. A unique `blockId` is
auto-added (EB keys per-block CSS off it).

## Read / modify
`sandbox/gutenberg-get` returns the parsed tree (name + blockId + attr keys). To modify,
read, edit `attrs` by `blockId`, re-serialize.

## Dynamic vs static (the validity rule)
- **Dynamic** EB blocks (PHP `render_callback`) render from attributes — direct insert is safe.
- **Static** blocks must byte-match their JS `save()` output or the editor flags
  "invalid/recovery". For those, use the **real-editor finalizer** (queue an
  attribute spec; a headless editor serializes it) — see spec 005. EB also needs
  per-block `blockMeta` CSS for full styling, which the finalizer produces.

## Schema
`sandbox/editor-schema {builder:"gutenberg", eb_only:true}` lists blocks + whether each
is dynamic + its attribute keys.
