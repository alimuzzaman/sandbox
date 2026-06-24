# Elementor `Document::save()` silently no-ops without a current user

**Plugins:** Elementor (+ Essential Addons)
**Discovered:** 2026-06-23, testing spec 005 editor-authoring engine.

`\Elementor\Plugin::$instance->documents->get($pid)->save(['elements'=>$tree])`
returns `false` and stores **0 elements** when there is no authenticated user,
because Elementor gates the save on `is_editable_by_current_user()`. The widget
node is dropped silently — no error, no log.

- **Symptom:** `sandbox_editor_elementor_insert()` returns
  `{saved:false, widget_survived:false}` and the page stays empty.
- **Cause:** called from a context with no `wp_get_current_user()` (e.g. bare
  `wp eval` / wp-cli). It is NOT a widget-registration problem — even a
  registered widget fails this way.
- **Fix when testing via wp eval:** `wp_set_current_user(1);` before the insert.
  Through the real entry point (the authenticated `sandbox/elementor-insert`
  MCP/REST ability) the request already has an admin user, so it works there.

Separately: EA Lite registers only ~59 of its widgets by default. Widgets like
`eael-counter` are NOT enabled, so even a correct save drops them. The engine's
"enable the EA widget if not registered" is currently only a comment — the
auto-enable step (spec 005 US1 acceptance scenario #2) is unimplemented.
