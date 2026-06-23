---
name: elementor-ea
description: Insert/modify Elementor & Essential Addons widgets programmatically — when adding or editing Elementor widgets (eael-*) on a page without the browser editor.
---

# elementor-ea

Author Elementor/Essential Addons content via the in-instance abilities (spec 003/005).

## Insert
`sandbox/elementor-insert` (or `sandbox_editor_elementor_insert`) — `{post_id, widget, settings?}`.
Builds a section>column>widget node with **7-char hex ids** (Elementor's format) and
persists via `\Elementor\Plugin::$instance->documents->get($id)->save(['elements'=>$tree])`
(the editor's own save path — runs control-schema sanitization + regenerates CSS). Sets
`_elementor_edit_mode=builder` and verifies the widget node survived (unregistered/disabled
EA widgets are silently dropped on save — enable them first).

## Gotchas (from wp-pilot)
- Full-width: also set `_wp_page_template = elementor_canvas` (data alone renders inside
  the theme container).
- Media/background-image controls need **both** `{id, url}` — `id` alone renders empty.
- Settings key = the control id; responsive controls add `_tablet`/`_mobile`; typography
  groups gate on `{prefix}_typography:"custom"`.

## Schema
`sandbox/editor-schema {builder:"elementor"}` lists registered widget names (incl. `eael-*`).
