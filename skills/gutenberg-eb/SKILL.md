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
dynamic + its block.json attribute keys (shallow, fast). Add `name:"essential-blocks/..."` for one
block's FULL attribute set (spec 011): the resolver reads the EB source (`attributes.js` + the
`@essential-blocks/controls` generators) and returns every attribute name + type + default — e.g.
`advanced-heading` resolves to ~787 attributes (the content key is **`titleText`**, not `title`).

Each named-EB response carries a structured `fidelity` report:
- `level: "full"` — all generators expanded from a source checkout (needs `src/controls/src/helpers`
  reachable, i.e. the active EB plugin is a full source checkout, not the `.org` build).
- `level: "partial"` — `attributes.js` found (explicit attrs incl. `titleText`) but some generators
  couldn't expand; `unresolved` names them.
- `level: "reduced"` — no EB source found; block.json (generic) attributes only, with a `reason`.

Pass `source_root:"<path>"` to point discovery at a specific EB checkout. The resolver runs
in-instance, so the checkout must be readable **inside the container** — the `.org` build alone
yields `partial` (no `src/controls`). See `memory/plugin-behavior/eb-attribute-schema.md`.

**Bundled schema catalog (spec 012)**: when the live resolver returns partial/reduced AND the
bundled catalog has a richer entry, `editor-schema` automatically serves the catalog entry
instead (`source: "catalog"`). This covers EB Pro blocks on any instance — they're a dist build,
so the live resolver can't reach full fidelity, but the catalog has the full JS-runtime attribute
set (1764 attrs for `pro-business-hours`, etc.). The catalog is committed at
`sandbox/assets/editor-schema/gutenberg.json.gz` and provisioned to each instance on `up`/`apply`.
`source: "live"` means the PHP resolver ran and won (preferred over catalog when full).
To regenerate: run the headless dump page (`admin.php?page=sandbox-schema-dump`) on an instance
with EB free + Pro active, then `./sb schema-catalog generate --instance <gen>`.
See `memory/plugin-behavior/schema-catalog.md`.

**Schema ≠ render**: the correct attribute names make authoring correct, but static EB blocks (e.g.
`advanced-heading`, which ships a real `save.js`) still need the **finalizer** to render non-empty —
a self-closing `gutenberg-insert` renders empty even with the right `titleText`.
