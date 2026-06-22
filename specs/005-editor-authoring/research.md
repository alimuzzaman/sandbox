# Research: AI-driven editor authoring (Elementor/EA + Gutenberg/EB)

Consolidated from four source-grounded deep-dives (2026-06-22): Novamira's Gutenberg
finalizer, the Elementor/EA + Essential Blocks data models, Elementor's own
MCP/Abilities + Angie SDK, and a survey of comparable open-source MCP projects.

## Headline findings

1. **Elementor's own architecture validates spec 005's design.** Elementor core
   ships a real WP-Abilities MCP server *and* its recommended programmatic write
   path is exactly `Document::save(['elements'=>$tree])`. The reference
   third-party project (`msrbuilds/elementor-mcp`) independently uses the same
   path. We are not inventing — we're matching the converging standard.
2. **No widget-aware MCP exists for Essential Addons or Essential Blocks.** This
   is a genuine gap and a first-mover opportunity for WPDeveloper.
3. **The two builders need different engines** (JSON tree + `Document::save` for
   Elementor; parse→mutate→serialize + a real-editor finalizer for Gutenberg) —
   there is no single mechanism.

## Do EL / EA / EB provide the WP Abilities API?

| Plugin | WP Abilities API? | Detail |
|--------|-------------------|--------|
| **Elementor core** | **Yes, but gated** | `modules/mcp/module.php` instantiates `McpAdapter`, registers 5 abilities on `wp_abilities_api_init`, serves them at `/wp-json/elementor/mcp`. Gated behind **hidden** experiment `e_wp_abilities_api` (default off) + **WP 7.0+**. Abilities: `list-pages`, `get-page-structure`, `update-page-settings` (settings only), `create-page` (blank shell), `get-globals`. **No element-tree write ability** — widget insertion is deliberately delegated to the in-browser `editor-mcp`/Angie. `composer.json` pins `wordpress/mcp-adapter ^0.5.0` (which requires `abilities-api`). |
| **Elementor Pro** | No | No mcp/abilities/adapter wiring anywhere. |
| **Essential Addons (lite + pro)** | No | None. |
| **Essential Blocks (free + pro)** | No | None. |

So: only Elementor *core* touches the Abilities API, only in a hidden pre-prod
experiment, and even then it can't insert widgets. Everything widget/block-aware
in the ecosystem is custom.

## Elementor / EA — the validated write path

All sources agree on the recipe (Elementor core's own `Document::save`,
`msrbuilds/elementor-mcp`, and the EA data-model dive):

- **Build the element tree server-side from schema** — never let the LLM author
  raw `_elementor_data`. Node shape: `{id, elType, widgetType, isInner, settings,
  elements}`.
- **Element IDs are caller-supplied 7-char hex**: `substr(bin2hex(random_bytes(4)),
  0, 7)`. Elementor does **not** generate them on save; wrong-shaped IDs break
  editor reopen, duplicate, and the `.elementor-element-{id}` CSS selector.
- **Persist via the document save pipeline**, not raw meta:
  `\Elementor\Plugin::$instance->documents->get($id)->save(['elements'=>$tree])`.
  This runs per-widget control-schema sanitization (throws on malformed data) and
  triggers CSS regeneration. Run it in an **admin user context**
  (`wp --user=admin eval …`) so `is_editable_by_current_user()` and
  `unfiltered_html` resolve.
- **Raw-meta fallback** (only if avoiding `save()`): `update_post_meta($id,
  '_elementor_data', wp_slash(wp_json_encode($tree)))` + `_elementor_edit_mode=
  'builder'` + `_elementor_version` + `delete_post_meta($id,'_elementor_css')` to
  force CSS regen. `wp_slash` is the classic gotcha.
- **EA widget enablement gate**: an EA `widgetType` only resolves if that widget
  is enabled in EA settings; otherwise the node is **silently dropped** on save.
  Enable first, then verify the node survived by re-reading `get_elements_data()`.
- **Layout**: section→column→widget (classic) or container→widget (Flexbox, needs
  Elementor ≥ 3.20). **V4 atomic widgets** use a heavier atomic-prop settings
  schema — read an example via `get-page-structure` first.
- **Address elements by `id`** (not index) for multi-turn edits.
- Settings: control_id = key; responsive `_tablet`/`_mobile` suffixes; typography
  group gated by `{prefix}_typography:"custom"`; media `{id,url}`; repeater rows
  carry 7-hex `_id`; URL `{url,is_external:"on"}`; dimensions `{unit,top,…,isLinked}`.

The canonical in-editor command API (for the optional headless-browser path) is
`$e.run('document/elements/create'|'delete'|'duplicate'|'settings'|'set-settings')`
— `Document::save` is its server-side equivalent.

## Gutenberg / EB — the validated write path

- **Never string-concatenate block markup.** Use `parse_blocks` → mutate the AST
  → `serialize_blocks` (GravityKit `block-mcp` model). Address blocks by stable
  `blockId`/path, not position.
- **Validation problem**: WP re-validates *static* blocks against their JS
  `save()` output; mismatch → "invalid/recovery". Two proven mitigations:
  - **Real-editor finalizer** (Novamira): queue an attribute-level spec, let the
    block's own JS `save()` serialize it in a hidden editor iframe, `validateBlock`
    it. Valid + correctly-styled from first save. Dynamic (`save:null`) blocks
    bypass the browser.
  - **Pre-validate generated markup** against WP's real parser
    (`@wordpress/block-serialization-default-parser`) + the block's `save()`
    (pluginslab `wp-blockmarkup-mcp` model) before writing.
- **EB per-block CSS**: each block's `blockMeta` attribute holds blockId-scoped
  minified desktop/tab/mobile CSS; the server only *assembles* stored `blockMeta`
  lazily into `uploads/eb-style/eb-style-<postId>.min.css` on first view — it
  **never recomputes** from raw style attrs. So a direct static write must embed
  `blockMeta`; the finalizer produces it naturally. Missing/duplicate `blockId` →
  skipped block or CSS bleed.
- **Parent/child** (accordion→accordion-item) use `providesContext`/`usesContext`;
  for static nested writes set child `parentBlockId` + mirrored `inherited*` attrs.
- **Refuse all-raw-HTML** content; tier/deny deprecated blocks and suggest
  replacements (block-mcp preference tiers).

## Prior art surveyed

| Project | Approach | Lesson |
|---------|----------|--------|
| `msrbuilds/elementor-mcp` | Abilities API + mcp-adapter; element factory + `Document::save` | **Reference impl** — schema-described abilities, 7-hex IDs, save-pipeline persistence, CSS invalidation, per-tool caps |
| `aguaitech/Elementor-MCP` | Node→WP REST, LLM authors `_elementor_data` string | Anti-pattern — no IDs/schema/CSS regen → stale, unstyled pages |
| `GravityKit/block-mcp` | WP REST over a parsed block tree; path/ref ops | parse→mutate→serialize; address by id/path; auto-transforms keep attrs↔HTML in sync |
| `pluginslab/wp-blockmarkup-mcp` | Local read/validate KB (SQLite of block schemas + real-parser/`save()` validation) | Pre-validate markup before writing |
| `WordPress/mcp-adapter` (Automattic/wordpress-mcp deprecated) | Canonical Abilities→MCP bridge | The ecosystem's standard in-WP transport |
| Elementor **Angie** + `angie-sdk` | First-party in-editor MCP; tools → `$e.run(...)` | Borrow the **contract layer**, not the transport |

## What to borrow from Angie (contract layer only)

- Tool annotations `readOnlyHint` / `destructiveHint` + an LLM-authored
  `confirmationMessage` for destructive ops (delete widget / reset settings).
- **`requiredResources` "read-before-write"**: a mutate tool declares it needs the
  current page/element model first; the orchestrator fetches + injects it before
  the mutation runs. Exactly right for widget/block editing.
- **Resource URI schemes** (`elementor://page-context`, `eb://…`,
  `wp://…`) to expose page structure / selected element / widget+block catalog as
  enumerable MCP resources.
- Per-server **instructions** describing capability *and limits* (one server for
  Elementor/EA, one for Gutenberg/EB) to improve tool selection.

**Not applicable**: Angie's postMessage/iframe transport and OIDC auth (it runs
in-browser with a human present); driving `$e.run` only works inside a live
editor. An out-of-process agent edits the *stored document*, not the live editor.

## Local source notes

- Essential Blocks uses a git **submodule** for controls (`src/controls`,
  "Bump src/controls submodule" in its log). For complete attribute introspection
  run `git -C /Users/alim/Sites/git/essential-blocks submodule update --init
  --recursive`. The built plugin (block.json + lib/) is present regardless.
- Sources: EA lite `/Users/alim/Sites/git/essential-addons-for-elementor-lite`,
  EA pro `/Users/alim/Sites/plugins-pro/essential-addons-elementor`, EB
  `/Users/alim/Sites/git/essential-blocks` (+ `-pro`), Elementor
  `/Users/alim/Sites/git/elementor`, Elementor Pro
  `/Users/alim/Sites/plugins-pro/elementor-pro`, Angie SDK
  `/Users/alim/Sites/git/angie-sdk`.
