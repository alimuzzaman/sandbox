---
name: elementor-ea
description: Insert/modify Elementor & Essential Addons widgets programmatically — when adding or editing Elementor widgets (eael-*) on a page without the browser editor.
---

# elementor-ea

Author Elementor/Essential Addons content via the in-instance abilities (spec 003/005).

## Insert
`sandbox/elementor-insert` (or `sandbox_editor_elementor_insert`) —
`{post_id, widget, settings?, full_width?, base_hash?}`.
Builds a section>column>widget node with **7-char hex ids** (Elementor's format) and
persists via `\Elementor\Plugin::$instance->documents->get($id)->save(['elements'=>$tree])`
(the editor's own save path — runs control-schema sanitization). Sets
`_elementor_edit_mode=builder`, regenerates the post CSS, and verifies the widget node
survived. Returns the widget's `element_id` + a `state_hash` for the next edit.
**Auto-enable**: if the requested `eael-*` widget exists but is disabled, the engine flips
it on in `eael_save_settings` and re-registers EA in-request (US1 #2). A genuinely absent
widget (Pro-only / not installed) returns `widget_unavailable`.

## Read / modify / delete
- `sandbox/elementor-get {post_id}` → element tree (`id`/`elType`/`widgetType`) + `state_hash`
  (read-before-write).
- `sandbox/elementor-update {post_id, element_id, settings}` — locate by **id**, merge per
  control id (responsive/typography/media/repeater round-trip), re-save, regen CSS. Siblings
  untouched.
- `sandbox/elementor-delete {post_id, element_id, confirm:true}` — destructive; needs `confirm`.

## Gotchas
- **`Document::save()` no-ops without a current user** (`is_editable_by_current_user`); the
  engine calls `sandbox_editor_ensure_user()` so the authenticated ability path AND bare
  `wp eval` both work. See `memory/plugin-behavior/elementor-save-needs-current-user.md`.
- Full-width: pass `full_width:true` (sets `_wp_page_template = elementor_canvas`); data
  alone renders inside the theme container.
- Media/background-image controls need **both** `{id, url}` — `id` alone renders empty.
- Settings key = the control id; responsive controls add `_tablet`/`_mobile`; typography
  groups gate on `{prefix}_typography:"custom"`.
- Concurrency: pass the `state_hash` you read as `base_hash`; a mismatch returns `conflict`.

## Schema
`sandbox/editor-schema {builder:"elementor"}` lists registered widget names (incl. `eael-*`);
add `name:"eael-..."` for one widget's full control set (enable it first if EA).

### Response shape
Every named-widget response includes `groups` for navigation:
- `groups.content` — flat `{ctrl_id: def}` of the widget's primary controls (text, image, link).
  Read these first to understand what the widget does.
- `groups.style` — `{section: {ctrl_id: def}}` for widget-inner appearance controls (typography,
  colors targeting inner elements, etc.).
- `groups.common` — `{section: {ctrl_id: def}}` for Elementor base + EA wrapper controls, identical
  across all widgets. Key sections: `_section_background` (background fill, image, video, gradient),
  `_section_border`, `_section_box_shadow`, `section_effects` (entrance animation),
  `section_motion_effects`, `_section_transform`.
  **To set the wrapper background** → `groups.common._section_background`.

Structural editor-chrome controls (section headers, dividers, raw_html) are omitted from groups.

### Search
Pass `search:"keyword"` to filter controls by id, label, description, and CSS selector text.
Returns `{matches: {ctrl_id: {def…, group, section}}}` — each match tagged with its group and
section so the agent knows where it lives for `elementor-update`.

```
editor-schema {builder:"elementor", name:"heading", search:"background"}
```

**Bundled schema catalog (spec 012)**: `editor-schema` serves full control sets from the committed
catalog when a widget isn't live-registered (e.g. Elementor Pro / EA on a consumer instance without
those plugins). Response carries `source: "catalog"` + `catalog.version`. A `version_mismatch` flag
is set when the installed plugin version differs from the catalog entry — schema served regardless
(better than nothing). Catalog uses v2 normalized format (`_pool` + per-widget split); resolved
transparently before response. To regenerate after a plugin update: `./sb schema-catalog generate
--instance <gen>` on an instance with Elementor + Pro + EA active.
See `memory/plugin-behavior/schema-catalog.md`.
