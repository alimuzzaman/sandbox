# Quickstart: AI Editor Authoring — live verification

Prerequisites: a running instance with **spec 003 abilities enabled** + EA and EB
active; the EB `src/controls` submodule checked out. All checks are live
(constitution IV).

## 1. Schema introspection

- `editor_schema(builder="gutenberg")` lists EB blocks; `editor_schema(builder="gutenberg",
  name="essential-blocks/button")` returns its attributes/defaults.
- `editor_schema(builder="elementor", name="eael-counter")` returns its control IDs/defaults.

## 2. Insert + restyle an EB block (Gutenberg first)

- `gutenberg_insert(post_id, block_spec={name:"essential-blocks/accordion", …, innerBlocks:[…accordion-item…]})`.
- Frontend renders the accordion **styled** (blockMeta CSS present); editor shows **no**
  "invalid/recovery"; child items carry `parentBlockId`.
- `gutenberg_update(post_id, selector, attrs)` changes a setting; re-render reflects it;
  unique `blockId`s preserved.

## 3. EB finalizer (static/third-party block)

- Queue a static block spec → finalizer (headless `visit`) serializes via real
  `wp.blocks` → post ends valid + styled; agent polls completion (no human step).

## 4. Insert + restyle an EA widget (Elementor)

- `elementor_insert(post_id, widget="eael-counter", settings={ending_number:250}, parent=…)`.
- Widget renders **styled** + opens in the editor without errors; node has a 7-hex id.
- If `eael-counter` was disabled, it's enabled first + the node is verified to survive.
- Full-width page sets `_wp_page_template=elementor_canvas`; media fields resolve `{id,url}`.
- `elementor_update(post_id, element_id, settings)` changes a setting; CSS regenerates;
  the rest of the page is intact (addressed by id).

## 5. Guards

- All-raw-HTML content refused; deprecated widget/block refused with a replacement suggestion.
- Destructive ops (delete/reset) require confirmation; reads are read-only.
- Concurrent-edit base-state mismatch is detected, not silently overwritten.
