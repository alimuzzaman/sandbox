# Schema catalog — runtime-registry-as-truth (spec 012)

## Key insight: PHP server-side ≠ JS runtime registry

For Gutenberg blocks, `WP_Block_Type_Registry` only carries block.json attributes (~3 keys for EB
blocks). The full attribute set lives in the JS bundle (`registerBlockType` call) — PHP can't see
it without a source checkout. For EB free with `src/controls/` reachable, the PHP source resolver
(`sandbox_editor_eb_resolve`) expands `attributes.js` + the `@essential-blocks/controls`
generators to get full fidelity. For EB Pro (always a dist build — no `src/`), the only way to
get full attributes is from the JS runtime via a headless `wp.blocks.getBlockTypes()` dump.

## Bundled catalog approach

A committed, gzipped catalog at `sandbox/assets/editor-schema/<builder>.json.gz` holds the full
attribute/control set for every block/widget, version-keyed, captured from a generation instance
with all Pro plugins active. Provisioned to each instance's
`mu-plugins/sandbox-schema-catalog/<builder>.json.gz` on `up`/`apply`. No per-user regen needed.

- `editor-schema` prefers live (PHP resolver) when it returns `full`. Falls back to catalog when
  live is `partial`/`reduced` AND the catalog entry has more attributes.
- Response carries `source: "catalog"` when catalog wins; `source: "live"` for EB blocks that the
  live resolver handled (full or partial); no `source` marker for live Elementor/core responses
  (pre-feature, byte-identical).
- `version_mismatch: true` is set when catalog entry's plugin version ≠ installed version —
  schema served anyway (better than reduced).

## Current catalog (2026-06-28)

| Builder   | Entries | Compressed | Notes |
|-----------|---------|-----------|-------|
| gutenberg | 188     | 85 KB     | EB Pro: 24 full; EB free: 54 partial; core: 110 full |
| elementor | 254     | 1.2 MB    | EP: 81 full; EA Pro: 42 full; EA free: 60 full; EL: 71 full |

Plugin versions at generation time: EB free v6.2.1, EB Pro v2.9.3, Elementor v4.1.4,
Elementor Pro v4.1.1, EA free v6.6.8, EA Pro v6.9.1.

## How to regenerate

1. Ensure the generation instance has EB free + Pro and Elementor + Pro/EA active.
2. Drive the headless dump page to capture the JS runtime block registry:
   visit `https://<instance>.tst/wp-admin/admin.php?page=sandbox-schema-dump`
   (wait for `#sandbox-schema-dump-done` in DOM — writes
   `wp-content/sandbox-schema-dump/gutenberg.json`).
3. Run `./sb schema-catalog generate --instance <gen>` — reads the JS dump + PHP Elementor
   dump, merges, packs to `sandbox/assets/editor-schema/*.json.gz`, prints coverage report.
4. Commit the updated `.json.gz` files; provision to instances via `./sb apply` or `up`.

## Elementor catalog — v2 normalized format

The Elementor catalog uses a pool-based v2 format to avoid duplicating the ~73.7% of controls
that are identical across widgets (reduces from 4.3MB to 1.4MB compressed).

```
_format: "v2"
_pool:   {ctrl_id → definition}   — controls appearing in ≥2 widgets; most-common value wins
<widget>: {
  controls:   [ctrl_id, ...]       — ordered list of IDs that match pool exactly
  overrides:  {ctrl_id → def}      — pool controls with widget-specific values
  own:        {ctrl_id → def}      — widget-unique controls (not in pool)
  content_ids:[ctrl_id, ...]       — ordered IDs where tab=="content" (primary widget controls)
}
```

`sandbox_editor_catalog_entry()` resolves this back to a flat controls dict before returning.
3,434 pool entries; 730 have `description` strings (from `control-descriptions.json`).

## Elementor catalog — groups

Every named-widget `editor-schema` response includes a `groups` key for navigation:
- `content` — flat `{ctrl_id: def}`: the widget's primary controls (from `content_ids`). Agents
  should inspect these first to understand what the widget does.
- `style` — `{section_name: {ctrl_id: def}}`: widget-specific appearance controls targeting
  widget-inner elements. Sections are named by Elementor (e.g. `section_title_style`).
- `common` — `{section_name: {ctrl_id: def}}`: Elementor base + EA extension wrapper controls,
  identical across all widgets. Key sections:
  - `_section_background` — background type, color, gradient, image, video, slideshow (~67 controls)
  - `_section_border` — border type, width, radius, box-shadow
  - `section_effects` — entrance animation (CSS class applied at scroll-into-view)
  - `_section_transform` — rotate, scale, offset, skew, flip
  - `section_motion_effects` — scrolling effects (parallax, mouse-track, 3D tilt)

Structural editor-chrome types (section, tab, tabs, raw_html, alert, heading, divider) are
omitted from all groups — they are Elementor UI scaffolding, not widget settings.

## Search param

Pass `search:"keyword"` to `editor-schema` for a widget to filter controls by id, label,
description, and CSS selector text. Each match is tagged with `group` and `section`:

```json
{"builder":"elementor","name":"heading","search":"background color"}
→ {"matches":{"_background_color":{"type":"color","group":"common","section":"_section_background",...}}}
```

Source is `live` when the widget is registered, `catalog` otherwise.

## EA widget attribution

EA free and EA Pro share the `Essential_Addons_Elementor` PHP namespace. Pro subclasses have
`\Pro\` in their class path — `_ns_to_plugin()` detects this and attributes them to
`essential-addons-elementor` (Pro slug); all others go to `essential-addons-for-elementor-lite`.

## EB Pro JS dump

The headless dump page (`00-sandbox-schema-dump.php`) enqueues `enqueue_block_editor_assets`
and runs `wp.blocks.getBlockTypes()` after `registerCoreBlocks()`. On the sandbox instance with
EB Pro active, this captured 24 EB Pro blocks with 116–1764 attributes each. EB free blocks
did NOT appear (their JS doesn't register in the headless page context — they need the iframed
editor). EB free is handled by the PHP source resolver (partial from attributes.js).
