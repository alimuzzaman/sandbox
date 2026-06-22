# Feature Specification: AI Editor Authoring — Elementor/EA Widgets + Gutenberg/EB Blocks

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: Novamira parity #3 (the Gutenberg "pending batch → finalizer" flow that makes
third-party blocks valid from first save) + the user ask: "create skill/schema for
Elementor (Essential Addons free+pro) widgets and Gutenberg (Essential Blocks free+pro)
blocks. How do they handle widget/block inserts? How do they modify settings?" + a
follow-up to learn from Elementor's Angie SDK, Elementor core's own MCP, and comparable
GitHub projects (`msrbuilds/elementor-mcp` et al.), and to determine whether EL/EA/EB
provide the WP Abilities API.

**See also**: [research.md](./research.md) — the consolidated four-source prior-art study
this design is built on.

## Summary

Let an agent programmatically **insert** and **modify** page-builder content on a
real instance — both Elementor (with Essential Addons widgets) and Gutenberg
(with Essential Blocks) — producing content that is valid and correctly styled,
not "recovery"-flagged or unstyled. Ship three things:

1. **An authoring engine** per builder (insert/modify primitives) exposed as MCP
   tools + CLI.
2. **Machine-readable schemas** of EA widgets and EB blocks (name → settings/
   attributes, types, defaults) introspected from the live registries.
3. **Skill packs** (`skills/elementor-ea/SKILL.md`, `skills/gutenberg-eb/SKILL.md`)
   teaching the agent the exact recipes + gotchas below.

The two builders need **fundamentally different strategies** — that's the core
finding:

| | Elementor / EA | Gutenberg / EB |
|---|---|---|
| Storage | `_elementor_data` postmeta = JSON element tree | `post_content` = block markup with `<!-- wp:… {attrs} -->` |
| Validity model | Elementor **parses JSON** — no byte-match validation | WP **re-validates** static blocks against JS `save()` output → mismatch = "invalid/recovery" |
| Safe write path | `Document::save(['elements'=>$tree])` via `wp eval` | depends on block type (see below) |
| Styling | per-element CSS regenerated from settings on save | per-block CSS must already be embedded (`blockMeta`) — server never recomputes |
| Browser needed? | **No** | **Yes for static blocks** (Novamira finalizer) |

## Prior art & architecture decision

The four deep-dives ([research.md](./research.md)) converge on one architecture,
which this spec adopts:

- **Elementor's own design validates the plan.** Elementor core ships a hidden,
  WP-7.0-gated WP-Abilities MCP server (`/wp-json/elementor/mcp`) that can read
  structure and write *settings*, but **deliberately has no element-tree write
  ability** — widget insertion is delegated to its in-browser `editor-mcp`/Angie
  (which calls `$e.run('document/elements/create')`). The recommended *server-side*
  write path is `Document::save(['elements'=>$tree])`. The leading third-party
  project `msrbuilds/elementor-mcp` independently uses the exact same path. So our
  Elementor engine = **build the tree server-side from schema → `Document::save`**,
  the now-converged standard.
- **No widget-aware MCP exists for EA or EB.** This is a gap and a first-mover
  opportunity for WPDeveloper — we'd ship the first EA/EB-aware authoring tools.
- **Gutenberg has two proven mitigations** for the block-validation problem:
  parse→mutate→serialize with a real-parser/`save()` pre-validator
  (`GravityKit/block-mcp`, `pluginslab/wp-blockmarkup-mcp`) for safe cases, and a
  real-editor finalizer (Novamira) for static/stateful/third-party blocks. We use
  both, routed by block type.
- **Borrow Angie's contract layer, not its transport.** Adopt tool annotations
  (`readOnlyHint`/`destructiveHint` + `confirmationMessage`), `requiredResources`
  "read-before-write", resource URI schemes, and per-server instructions. Skip its
  postMessage/iframe + OIDC plumbing (in-browser, human-present — not our case).

**Relationship to spec 003:** these editor abilities are the strongest argument
*for* the in-instance Abilities layer — if 003 ships, the Elementor/EB engines can
register as WP abilities (msrbuilds-style) and be reachable by any MCP client; if
not, they live as Python-MCP tools. Either way the engine logic is identical.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Insert an EA widget (Priority: P1)

**Acceptance**:
1. **When** the agent calls `elementor_insert(post_id, widget="eael-counter",
   settings={ending_number:250}, parent=…)`, **Then** the widget appears on the
   rendered page, styled, and is editable in the Elementor editor (no errors).
2. The tool generates a unique **7-char lowercase hex** `id` per node (Elementor
   does **not** generate IDs server-side — omitting it breaks the
   `.elementor-element-{id}` CSS selector).
3. If `eael-counter` isn't enabled in EA settings, the tool **enables it first**
   (else the node is silently dropped on save), then **verifies** the node
   survived by re-reading `get_elements_data()`.
4. CSS is regenerated (the `Document::save()` path does this; raw-meta path must
   call `Post_CSS::create($id)->delete()`).

### User Story 2 — Modify an EA widget setting (Priority: P1)

**Acceptance**:
1. **When** the agent calls `elementor_update(post_id, element_id, settings={…})`,
   **Then** the node is located by `id` in the tree, `settings[control_id]` is
   merged, the tree re-saved, CSS regenerated.
2. Complex controls round-trip correctly: responsive (`key`/`key_tablet`/
   `key_mobile`), media (`{id,url}`), typography group (`{prefix}_typography:
   "custom"` + `{prefix}_font_*`), dimensions, URL (`{url,is_external:"on"}`),
   repeater (rows with 7-hex `_id`).

### User Story 3 — Insert an EB block (dynamic) (Priority: P1)

**Acceptance**:
1. **When** the agent inserts a **dynamic** EB block (PHP `render_callback`), **Then**
   writing the `<!-- wp:essential-blocks/… {attrs} /-->` markup to `post_content`
   renders correctly via `do_blocks()` with no validation error (dynamic blocks
   have no static HTML to byte-match).
2. A unique `blockId` is set per block (duplicate/missing `blockId` → skipped
   block or CSS bleed).

### User Story 4 — Insert an EB block (static) with correct styling (Priority: P1)

**Acceptance**:
1. **When** the agent inserts a **static** EB block, **Then** the saved markup
   matches what the block's JS `save()` would produce (no "this block contains
   unexpected or invalid content"), **and** the block's `blockMeta` (blockId-
   scoped minified desktop/tab/mobile CSS) is populated so the block is styled.
2. The robust path uses the **browser finalizer** (US-5); a fast path may write
   markup directly only when validity + `blockMeta` can be guaranteed.

### User Story 5 — Browser finalizer for valid-from-first-save (Priority: P1)

Port Novamira's pending-batch → finalizer so static/third-party Gutenberg blocks
are serialized by the **real editor JS runtime**, guaranteeing validity + correct
generated CSS.

**Acceptance**:
1. The agent queues an **attribute-level spec** (`{name, attributes,
   innerBlocks}`), not raw markup.
2. A headless browser (`visit`) opens the target's real edit screen, runs
   `wp.blocks.createBlock → serialize → parse → validateBlock`, and writes back
   valid `post_content`; EB's per-block CSS is generated as a side effect of the
   real save.
3. The agent observes completion headlessly (poll/marker) — no human step.

### User Story 6 — Live schemas (Priority: P2)

**Acceptance**:
1. `editor_schema(builder="elementor", name="eael-counter")` returns that
   widget's control IDs, types, defaults (introspected from the live
   `widgets_manager`). `editor_schema(builder="gutenberg",
   name="essential-blocks/button")` returns the block.json `attributes`.
2. `editor_schema(builder=…)` with no name lists all registered EA widgets / EB
   blocks present on the instance.

## Requirements

### Elementor / EA engine
- **FR-1** `elementor_insert` / `elementor_update` / `elementor_get` /
  `elementor_delete` MCP tools (+ `./sb elementor …`), all driving
  `\Elementor\Plugin::$instance->documents->get($id)->save(['elements'=>$tree])`
  via `wp eval` — the same path the editor's `save_builder` AJAX uses.
- **FR-2** Generate 7-char lowercase hex IDs (matching JS `getUniqueId()`) for
  every section/column/container/widget/repeater-row created.
- **FR-3** Ensure `_elementor_edit_mode='builder'` on the target; stamp
  `_elementor_version`. Support both section→column→widget and container layouts.
- **FR-4** Enable required EA widgets before save (EA's enabled-widgets option) +
  post-save verification that the `widgetType` node survived (catches the silent-
  drop on unregistered widget).
- **FR-5** Run in an admin context (`wp --user=admin eval`) so
  `is_editable_by_current_user()` passes and `unfiltered_html` doesn't strip
  widget HTML via `wp_kses_post`.
- **FR-5a** Read-before-write: `elementor_update`/`elementor_insert` first read the
  current tree via `get_elements_data()` and address nodes by `id` (never index),
  so multi-turn edits don't corrupt the page (msrbuilds/block-mcp lesson).
- **FR-5b** Containers require Elementor ≥ 3.20; **V4 atomic widgets** use the
  atomic-prop settings schema — the engine reads an example node via
  `get_elements_data()` / `editor_schema` before authoring atomic settings.
- **FR-5c** Raw-meta fallback path (only when `Document::save` is unavailable):
  `update_post_meta($id,'_elementor_data', wp_slash(wp_json_encode($tree)))` +
  `_elementor_edit_mode='builder'` + `_elementor_version` +
  `delete_post_meta($id,'_elementor_css')`. `wp_slash` + CSS-delete are mandatory.

### Gutenberg / EB engine
- **FR-6** `gutenberg_get(post_id)` → compact parsed-block tree (via
  `parse_blocks`). `gutenberg_update` modifies attrs in place
  (`parse_blocks` → edit `attrs` by name/order/`blockId` → `serialize_blocks`).
- **FR-7** Block-type classification: detect dynamic (has `render_callback`) vs
  static from the block registry; **dynamic** blocks → direct markup write is
  safe; **static** blocks → route to the finalizer (FR-8) unless a guaranteed
  fast path applies.
- **FR-8** **Finalizer** (Novamira pattern, re-implemented): a Sandbox mu-plugin
  CPT/queue holding attribute-level specs + a finalizer admin page that the
  `visit` tool drives to serialize via real `wp.blocks` JS and write back valid
  content. Reuse the batch/item + lease + stage-then-commit + base-content-hash
  concurrency model. Agent observes via a poll marker.
- **FR-9** Per-block `blockId` uniqueness enforced; for static direct-writes,
  `blockMeta` (blockId-scoped minified CSS) must be embedded — document that the
  finalizer path produces it naturally and the direct path must precompute it
  (EB assembles stored `blockMeta` lazily into
  `uploads/eb-style/eb-style-<postId>.min.css` and **never** recomputes from raw
  style attrs).
- **FR-10** Honor parent/child blocks (accordion → accordion-item) via
  `providesContext`/`usesContext`: set child `parentBlockId` + mirrored
  `inherited*` attrs when writing nested blocks statically.
- **FR-11** Refuse all-raw-HTML content (push the agent to registered blocks),
  mirroring Novamira's `blocks_are_raw_html_only` guard.
- **FR-11a** Direct-write pre-validator: before writing static-block markup,
  validate it with WP's real parser (`@wordpress/block-serialization-default-parser`
  equivalent) + the block's `save()` output, refusing on mismatch (the
  `wp-blockmarkup-mcp` pattern) — the fast path's safety net before falling back to
  the finalizer.

### Cross-cutting (both engines)
- **FR-14** Tool annotations (Angie contract layer): every tool carries
  `readOnlyHint`/`destructiveHint`; destructive ops (delete widget/block, reset
  settings) require an LLM-authored `confirmationMessage` and are gated.
- **FR-15** Read-before-write contract surfaced as MCP resources / `requiredResources`:
  expose page structure + the widget/block catalog (`elementor://page-context`,
  `eb://catalog`, …) so the agent enumerates what's editable and reads current
  state before mutating.
- **FR-16** Deprecation tiers: refuse inserting deprecated/legacy EA widgets and EB
  blocks and suggest the current replacement (block-mcp preference-tier lesson).
- **FR-17** Auth on every tool: app-password/in-container exec **and** a WP
  capability check (`edit_post` on the target) per operation.

### Schemas + skills
- **FR-12** `editor_schema(builder, name?)` MCP tool introspecting the live
  registries (Elementor `widgets_manager->get_widget_types()` controls; WP
  `WP_Block_Type_Registry->get_all_registered()` attributes). Cache to
  `runtime/schemas/<instance>/{elementor,gutenberg}.json`.
- **FR-13** Ship `skills/elementor-ea/SKILL.md` and `skills/gutenberg-eb/SKILL.md`
  with the recipes, node/markup shapes, complex-control formats, and the
  gotchas below. (Also discoverable as focused-plugin skills when EA/EB is the
  focus.)

## Design notes — how inserts & settings actually work (answering the ask)

**Novamira does Gutenberg only** (no Elementor). Its insight, which we adopt for
EB static blocks: never hand-serialize static blocks server-side — queue
`{name, attributes, innerBlocks}` and let the block's **own JS `save()`** produce
the markup in a real (hidden) editor iframe, then `validateBlock` it. That's why
it's "valid from first save." Dynamic (`save: null`) blocks bypass the browser.
(Full mechanism: spec 005 research / the agent writeup — CPT `*_gb_change`,
batch+item meta state machine, UUID lease, stage→commit with SHA-256 base hash,
token-gated SSE so a headless agent can watch the browser.)

**Elementor needs none of that** — it stores a JSON tree in `_elementor_data` and
renders by parsing it (no byte-match validation), so the agent can build the tree
in PHP and `Document::save()` it. The catches are different: caller-supplied
hex IDs, EA widget enablement, and CSS regeneration.

**Settings modification** is symmetric in concept, different in storage:
- Elementor: locate node by `id`, set `settings[control_id]` (control_id = the
  first arg of `add_control`; sections are organizational, not nested).
- Gutenberg/EB: `parse_blocks`, find by name/`blockId`, edit `attrs`,
  `serialize_blocks`; remember `blockMeta` CSS + `blockId`.

## Integration points

- MCP: `elementor_*`, `gutenberg_*`, `editor_schema` tools in a new
  `tools/editor.py`; reuse `wp_exec`/`wp eval`, `visit` (finalizer), `fs_*`.
- mu-plugin: the EB finalizer queue + admin page (provisioned like other
  mu-plugins). Composes with spec 003's Abilities layer if present.
- Builders are WPDeveloper's own (EA: `/Users/alim/Sites/git/essential-addons-elementor`,
  EA Pro: `/Users/alim/Sites/plugins-pro/essential-addons-elementor`; EB:
  `/Users/alim/Sites/git/essential-blocks` + `-pro`) — schemas can ship
  pre-generated for known versions and refresh live. **EB controls are a git
  submodule** (`src/controls`); run `git -C …/essential-blocks submodule update
  --init --recursive` for complete attribute-source introspection.
- Reference implementation to study while building the Elementor engine:
  `msrbuilds/elementor-mcp` (same `Document::save` + element-factory + 7-hex-ID
  approach). Optional: Elementor core's hidden `e_wp_abilities_api` experiment can
  serve the read/settings/create-shell abilities at `/wp-json/elementor/mcp`
  (WP 7.0+), but never the tree-write — we own that.
- Docs: CLAUDE.md (editor-authoring loop + the "hand-authored PHP only works for
  core blocks" limitation this lifts), MCP table, the two new skills.

## Out of scope (v1)

- Builders beyond Elementor + Gutenberg (Bricks/Divi/Oxygen) — same engine shape,
  later.
- Visual/design correctness review (that's a `visit`-screenshot follow-up).
- Full EB `blockMeta` precomputation in PHP for the direct static path — prefer
  the finalizer; document direct-write as advanced/at-risk.

## Tasks

1. Elementor engine: read-before-write tree read/insert/update/delete via
   `Document::save()` (admin context), hex-ID gen, widget-enable + survive-verify,
   CSS regen, address-by-id, raw-meta fallback. MCP + CLI.
2. `editor_schema` introspection + cache (both builders); EB submodule init.
3. Gutenberg engine: parse/modify/serialize; dynamic-vs-static classification;
   markup pre-validator; `blockId`/`blockMeta`/parent-context handling; raw-HTML
   guard; deprecation tiers.
4. EB finalizer mu-plugin (batch/item queue, lease, stage→commit) + `visit`-driven
   finalizer page + headless completion marker.
5. Contract layer: tool annotations (`readOnly`/`destructive` + `confirmationMessage`),
   read-before-write resources (`elementor://`, `eb://`), per-op capability checks.
6. Skills: `elementor-ea`, `gutenberg-eb`.
7. Live verification: insert + restyle an `eael-counter` and an EB accordion
   (parent+child); confirm rendered + editor-valid + styled; finalizer round-trip
   on a static third-party block.
8. Docs.
