# Editor Schema API

A read-only lookup for Gutenberg block attributes and Elementor widget/element
controls — "what settings does this block/widget have, and how do I actually
use each one to change styling?"

## Endpoints

```
GET /wp-json/sandbox/v1/editor-schema        — the schema lookup
GET /wp-json/sandbox/v1/editor-schema/docs   — this document, as JSON
```

## Authentication

None required.

## Query parameters

| Param | Applies to | Meaning |
|---|---|---|
| `builder` | both, **required** | `gutenberg` or `elementor` |
| `name` | both, optional | Block name (`core/heading`, `essential-blocks/advanced-heading`) or widget/element name (`heading`, `eael-pricing-table`, `container`). Omit for the full listing. |
| `search` | both, optional | Filter/search attributes or controls. With `name`: searches that one block/widget's own attributes/controls. Without `name` (Elementor only): a GLOBAL scan across every registered widget+element, returns the best-matching control per host, ranked. |
| `eb_only` | gutenberg listing | `1` to restrict the full block listing to `essential-blocks/*` names only. |
| `types` | elementor | `all` \| `widgets` \| `elements` — restrict a global search or listing to one kind. |
| `limit` | elementor global search | Max results returned (default 40). |
| `variants` | elementor, with `name` | Resolve a specific control's per-breakpoint keys (see Responsive variants, below). |
| `source_root` | gutenberg EB blocks | Override the Essential Blocks source-checkout path used to resolve full attribute sets; rarely needed. |
| `include_variants` | gutenberg, with `name` | `1` to include MOB/TAB/hover-prefixed variant attributes as their own top-level entries instead of hiding them (see "Decoded descriptions + hidden variants", below). |

## Gutenberg: response shape

```
GET ?builder=gutenberg&name=core/heading
```

```jsonc
{
  "builder": "gutenberg",
  "name": "core/heading",
  "dynamic": true,               // true if this block's class defines real server-render logic (see Dynamic flag, below)
  "title": "Heading",             // human label, like Elementor's widget label
  "description": "Introduce new sections and organize content...",
  "eb_attribute_fidelity": "full", // full | partial — see Fidelity levels, below
  "attributes": {                 // FLAT map, every attribute, full definition (not just type/default)
    "content": {
      "type": "rich-text",
      "source": "rich-text",      // <-- HOW this attribute maps into saved markup
      "selector": "h1,h2,h3,h4,h5,h6",
      "role": "content"
    },
    "level": { "type": "number", "default": 2 },
    "align": { "type": "string", "enum": ["left","center","right","wide","full",""] },
    "backgroundColor": { "type": "string" },
    "...": "..."
  },
  "groups": {                      // the SAME attributes map, split block-specific vs shared
    "content": { "content": {...}, "level": {...}, "levelOptions": {...}, "placeholder": {...} },
    "common":  { "lock": {...}, "metadata": {...}, "className": {...}, "style": {...},
                 "anchor": {...}, "align": {...}, "backgroundColor": {...}, "textColor": {...},
                 "gradient": {...}, "fontSize": {...}, "fontFamily": {...}, "borderColor": {...} }
  },
  "supports": { "color": {...}, "spacing": {...}, "typography": {...}, "...": "..." },
  "style_paths": {                 // see "Styling attributes NOT in block.json", below
    "spacing.padding": "style.spacing.padding",
    "color.text": "style.color.text",
    "...": "..."
  },
  "source": "live"                 // live | catalog — see Fidelity levels, below
}
```

### Attribute definition fields ("how to use it")

Each entry in `attributes` (and `groups.content`/`groups.common`) is the
**full** attribute definition, not a stripped `{type, default}` pair. The
fields that actually tell you how to use the attribute:

| Field | Meaning |
|---|---|
| `type` | JSON Schema type (`string`, `number`, `boolean`, `object`, `array`, `rich-text`) |
| `default` | Default value, if declared |
| `enum` | Valid values list — the only values the editor UI will ever set |
| `source` | How this attribute is read FROM saved markup: `attribute` (an HTML attribute), `rich-text` (inner HTML of a selector), `query` (a list parsed from repeated child nodes), or absent (stored as block-comment JSON, not in markup) |
| `selector` | The CSS selector (relative to the block's saved markup) this attribute reads from/writes to, when `source` is set |
| `attribute` | The literal HTML attribute name (e.g. `src`, `alt`, `href`), when `source: "attribute"` |
| `role` | `content` (user-facing content) vs `local`/absent (internal bookkeeping, e.g. `blob`) |

Example — `core/image`'s `url` attribute: `{"type":"string","source":"attribute","selector":"img","attribute":"src"}` means the value lives in the saved markup as `<img src="...">`, not as block-comment JSON.

### Decoded descriptions + hidden MOB/TAB/hover variants

Essential Blocks attribute names are one long abbreviated camelCase string
with no separate `label` field — `wrpMrg_Top` gives you nothing to go on
without knowing EB's naming convention. Two things address this, both only
applied to `essential-blocks/*` names (core/third-party block ids are
already self-describing and don't get this treatment):

**1. `decoded` — a mechanical breakdown, not a guessed sentence.** Every
surviving attribute gets a `decoded` field: the id is split into segments
against a dictionary of ~140 tokens, each confirmed against the real
Essential Blocks source (the actual UI control's `label` string or a code
comment next to where that attribute is used — not inferred from the
abbreviation shape alone).

```jsonc
"wrpMrg_Top": {
  "type": "string",
  "decoded": {
    "responsive": null,      // or "mobile" / "tablet" if this is itself a MOB/TAB variant
    "hover": false,           // true if this is itself a hov_ variant
    "decoded": [
      { "token": "wrp", "meaning": "Wrapper (outer container)" },
      { "token": "Mrg", "meaning": "Margin" },
      { "token": "Top", "meaning": "Top side" }
    ]
  }
}
```

Deliberately **not** composed into a single English sentence — a template
risks silently misreading an unusual token order (the same category of
mistake as a case-sensitivity bug found and fixed while building the search
feature above). Returning verified `{token, meaning}` facts instead of a
synthesized sentence means a wrong guess can't be dressed up as confident
prose; the calling agent decides how (or whether) to phrase the parts.
Any segment that doesn't match a known token is still reported, just with
`"meaning": null` — never silently dropped or invented.

**2. MOB/TAB/hov_-prefixed variants are hidden by default.** Verified
across the whole catalog: 99.9–100% of these variant attributes have a
matching un-prefixed base attribute in the same block (the only exceptions,
~0.1%, stay visible and flagged `orphaned_variant`). Hiding them roughly
halves the visible attribute count everywhere (measured: 18,857 → 10,236
total occurrences across all blocks; the worst single block,
`pro-business-hours`, goes from 1,764 to 918). Nothing is lost — the base
attribute carries pointers to the exact hidden key names:

```jsonc
"wrpMrg_Top": {
  "responsive": { "mobile": "MOBwrpMrg_Top", "tablet": "TABwrpMrg_Top" },
  "hover": "hov_wrpMrg_Top",
  "hover_responsive": { "mobile": "hov_MOBwrpMrg_Top", "tablet": "hov_TABwrpMrg_Top" }
}
```

Pass `include_variants=1` to get the raw, undecorated full list back
instead (still with `decoded` attached to each). `search` still finds a
base attribute by "mobile"/"tablet"/"hover" even when hidden — those
keywords match against the presence of its `responsive`/`hover` pointers,
not just the (now-absent) literal prefix in its id.

### `groups.content` vs `groups.common`

Mirrors Elementor's own `groups.content`/`groups.style`/`groups.common` split
(Gutenberg has no reliable signal to further split off a `style` bucket the
way Elementor's `tab==='style'` does, so it's a 2-way split here):

- **`content`** — attributes specific to THIS block (a heading's `level`, an
  image's `url`/`alt`, a button's `text`).
- **`common`** — attributes present on nearly every block regardless of what
  it does (`lock`, `metadata`, `className`, `style`, `anchor`, `align`,
  `backgroundColor`, `textColor`, `gradient`, `fontSize`, `fontFamily`,
  `borderColor`). This list is data-driven — found by diffing `attributes`
  across several unrelated blocks and keeping names that recurred across
  most/all of them.

### Searching a Gutenberg block's attributes (`search`, with `name`)

```
GET ?builder=gutenberg&name=essential-blocks/pro-data-table&search=margin
```

Essential Blocks' attribute names are abbreviated, not self-describing —
`wrpMrg_isLinked`, `MOBclGp_Range`, `maxW_Range` mean "wrapper margin linked
toggle", "mobile column gap", "max width", but there's no `label` field
(unlike Elementor controls) and often no literal substring overlap with the
word you'd actually search (`"gap"` is not a substring of `"clGp"`). Plain
substring search silently returns nothing on exactly the attributes it's
most needed for. A large Pro block can have 1000+ flat attributes — finding
the 10 that matter by eye isn't practical.

`search` (with `name`) resolves this: query tokens expand through a
synonym dictionary mapping human terms to the literal abbreviation tokens
EB actually uses (`"gap"` → `Gp`/`Gap`, `"margin"` → `Mrg`/`Margin`,
`"radius"`/`"rounded"`/`"corner"` → `Rds`, `"hover"` → `H`/`hov_`/`Hv`,
`"mobile"`/`"tablet"` → `MOB`/`TAB`). The dictionary was built by reading
real attribute names across the whole catalog, not guessed. Returns
`{builder, name, search, source, matches: {...}}`, each match carrying its
full attribute definition plus a `group` (`content`/`common`) and a `score`,
ranked highest first — same shape as Elementor's search response.

One sharp edge worth knowing: the abbreviation tokens are matched
**case-sensitively** against the real attribute id on purpose. A
case-insensitive match on a short token like `Gp` produces false positives
(`"gp"` is a substring of `"bgImgPos"` — background-image-position, nothing
to do with gap) that case-sensitive matching correctly excludes, since EB's
camelCase casing convention is itself part of the signal. This means a
search can occasionally miss a real match when a block's author used a
different casing/word-order variant than the dictionary was built from (e.g.
`"ShadowEffectBorder"` instead of the more common `"BorderShadow"`) — plain,
longer human words (`"shadow"`, `"gap"`, `"border"`) fall back to ordinary
case-insensitive substring matching and don't have this limitation.

### Styling attributes NOT in `attributes` (`supports` + `style_paths`)

This is the Gutenberg analogue of "how do I change this widget's background/
border/shadow" for Elementor's `groups.common` — except Gutenberg's mechanism
is one level more indirect.

A block's `supports` flags (color, spacing, typography, border, shadow,
dimensions, position, ...) don't add named attributes to `attributes` at
all. Instead they enable the *generic* `style` attribute — a single opaque
JSON object with no schema of its own — to accept specific sub-paths.

`style_paths` tells you, **for this specific block**, which `style.*` JSON
paths its `supports` flags actually enable — e.g. if `supports.spacing.
padding` is on, `style_paths` includes `"spacing.padding": "style.spacing.
padding"`. One real gotcha worth knowing: `color.text`/`color.background`
default to ENABLED when the `color` support group exists at all and doesn't
explicitly say otherwise (every other flag defaults to disabled when simply
absent).

**To actually apply one**: write into the block's `style` attribute at that
path. Example — a heading with 16px top/bottom padding and a custom text
color ends up with:

```jsonc
{
  "style": {
    "spacing": { "padding": { "top": "16px", "bottom": "16px" } },
    "color":   { "text": "#0c0c24" }
  }
}
```

A couple of practical notes:
- `typography.fontSize`/`fontFamily` are special: if the user picks a
  **preset** (a theme.json-defined size/family), it's written as a *slug* to
  the top-level `fontSize`/`fontFamily` attribute instead of `style.
  typography.*`. The `style.typography.*` path is only used for a fully
  custom (non-preset) value. `style_paths` flags this inline.
- `style` should be treated as a deep-merge target, not a full-replace —
  setting `style.color.text` shouldn't clobber an existing
  `style.spacing.padding` set earlier.

### Dynamic flag

`dynamic: true` means the block's class defines its OWN `render_callback()`
— i.e. it generates some content server-side from its attributes at render
time (a live query, current-post binding, etc.), not just static
`save.js`-baked markup. **Essential Blocks gotcha**: EB's base block
registration attaches a generic `render_callback` to literally every block
it registers (handling conditional display + inline-SVG substitution only),
so the raw signal is `true` for every single EB block whether or not it has
real dynamic logic. This endpoint corrects for that — `dynamic` here
reflects whether the concrete block subclass defines its own
`render_callback()` method, which is the real signal. Note this is a
**block-type-level** signal (does this kind of block support dynamic
rendering at all), not a per-instance one — e.g.
`essential-blocks/advanced-heading` reports `dynamic:true` because its class
supports a dynamic title source, even for a specific instance configured
with `source:"custom"` (static).

### Fidelity levels (`eb_attribute_fidelity` / `fidelity`, `source`)

- **Non-EB blocks** (`core/*`, any third-party block, ACF, etc.):
  `eb_attribute_fidelity: "full"`. The declared attribute set is the
  complete, authoritative one for these — there's nothing further to
  resolve.
- **`essential-blocks/*` blocks**: EB declares only ~3 generic attributes up
  front; the REAL attribute set (dozens to low hundreds) is assembled at
  runtime by generator functions (`generateTypographyAttributes()`,
  `generateBackgroundAttributes()`, ...). This endpoint resolves the full
  set when possible (`source: "live"`, `eb_attribute_fidelity: "full"` or
  `"partial"` if some generator couldn't be expanded — see
  `fidelity.unresolved` for which ones), and falls back to a precomputed
  catalog (`source: "catalog"`) when the full set can't be resolved live.
- **`source: "catalog"` responses**: `title`/`description`/`supports`/
  `style_paths` are only populated when the same block is also
  live-registered (common — catalog is chosen for richer attribute counts,
  not because the block is unavailable). Pure catalog-only entries don't
  carry these fields yet.
- **`version_mismatch: true`**: the installed plugin version differs from
  the version the catalog entry was generated against — treat catalog
  attribute names as probably-right but re-verify against `source: "live"`
  if available.

## Elementor: response shape

```
GET ?builder=elementor&name=heading
```

```jsonc
{
  "builder": "elementor",
  "name": "heading",
  "kind": "widget",              // widget | element (section/column/container/e-flexbox/...)
  "source": "live",
  "controls": {                   // FLAT map, every control
    "title": { "type": "text", "label": "Title", "default": "Add Your Heading Text Here", "section": "section_title", "tab": "content" },
    "typography_font_size": { "type": "slider", "label": "Size", "section": "section_title_style", "tab": "style", "responsive": true },
    "_padding": { "type": "dimensions", "label": "Padding", "section": "_section_style", "tab": "advanced",
                  "selectors": { "{{WRAPPER}}": "padding: {{TOP}}{{UNIT}} {{RIGHT}}{{UNIT}} {{BOTTOM}}{{UNIT}} {{LEFT}}{{UNIT}};" } },
    "...": "..."
  },
  "groups": {
    "content": { "title": {...} },                          // this widget's own settings
    "style":   { "section_title_style": { "typography_font_size": {...}, "...": "..." } },  // widget-specific appearance
    "common":  { "_section_background": { "...": "..." }, "_section_border": {...}, "...": "..." } // shared wrapper controls, identical across ALL widgets
  },
  "responsive": { "breakpoints": ["mobile","tablet"], "controls": ["typography_font_size", "..."] }
}
```

### Control fields ("how to use it")

| Field | Meaning |
|---|---|
| `type` | Elementor control type (`text`, `select`, `slider`, `dimensions`, `color`, ...) |
| `label` | Human label shown in the editor |
| `default` | Default value |
| `description` | Rare (most controls have none), but real help text when present |
| `section` / `tab` | Where this control lives in the editor panel |
| `responsive` | `true` if this control has per-breakpoint variants — resolve them with `variants` (below), don't guess `_tablet`/`_mobile` suffixes |
| `selectors` | **The literal CSS this control writes**, keyed by selector template (`{{WRAPPER}}` = this widget's own wrapper div) — the most direct "how to use it" data Elementor exposes |
| `options` | Valid choices for `select`/`choose`-type controls, `{value: label}` |

### `groups.content` / `groups.style` / `groups.common`

- **`content`** — this widget's own primary settings (what it shows).
- **`style`** — this widget's own appearance controls (colors, typography
  targeting the widget's inner elements).
- **`common`** — base + extension controls targeting `{{WRAPPER}}` (the
  outer div) — **identical across every widget**: `_section_background`
  (color/image/gradient), `_section_border`, `_section_box_shadow`,
  `section_effects` (entrance animation), `section_motion_effects`,
  `_section_transform`. To change a widget's wrapper background, look in
  `groups.common._section_background`, not `groups.style`.

### Global search (no `name`)

```
GET ?builder=elementor&search=box%20shadow
```

Scans every registered widget AND element type, keeps each host's single
best-scoring match, returns the top matches ranked — "which widget/element
has a control matching X?" Heavier than a per-widget search (instantiates
every control stack, ~1s) — pass `types=widgets` or `types=elements` to
scope it, `limit` to cap results (default 40).

### Responsive variants

```
GET ?builder=elementor&name=heading&variants=typography_font_size
```

Elementor's control list only ever includes ONE base control key per
responsive setting — the per-device keys (`{key}_tablet`, `{key}_mobile`,
...) are derived, never listed directly. This resolves them: returns the
active breakpoints and the exact key to write for each device, so you never
have to guess the suffix convention.

## Examples

List every registered Gutenberg block:
```
GET /wp-json/sandbox/v1/editor-schema?builder=gutenberg
```

List only Essential Blocks blocks:
```
GET /wp-json/sandbox/v1/editor-schema?builder=gutenberg&eb_only=1
```

Get one block's full schema:
```
GET /wp-json/sandbox/v1/editor-schema?builder=gutenberg&name=essential-blocks/advanced-heading
```

Search one Elementor widget's controls for anything spacing-related:
```
GET /wp-json/sandbox/v1/editor-schema?builder=elementor&name=heading&search=spacing
```

Find which widget/element exposes a box-shadow control:
```
GET /wp-json/sandbox/v1/editor-schema?builder=elementor&search=box%20shadow&types=widgets
```

## Applying changes

Editor Schema itself is **read-only** — it tells you what settings exist and
how to use them, but it doesn't write anything. Actually changing a page
means pairing a schema lookup with one of the paired editor-authoring
abilities (`gutenberg-get`/`-insert`/`-update`/`-delete`,
`elementor-get`/`-insert`/`-update`/`-delete`). Unlike Editor Schema, those
require authentication. The workflow is always: **look up → read current
state → write**.

Both `gutenberg-update` and `elementor-update` locate the target by a stable
id (`block_id` / `element_id`), not by position, and **merge** the settings
you pass rather than replacing the whole block/element — so you only need to
send the keys you're changing. Both also accept an optional `base_hash` (the
`state_hash` a prior `-get` call returned); if the post changed since you
read it, the update is refused with a `conflict` error instead of silently
overwriting someone else's edit — read-before-write.

### Change the color of an existing block

1. **Gutenberg**: `gutenberg-get {post_id}` → find the block's `blockId`.
   `editor-schema {builder:"gutenberg", name:"<blockName>"}` → check
   `style_paths` for `color.text` / `color.background` / `color.gradients`.
   Then `gutenberg-update {post_id, block_id, attributes: {style: {color: {text: "#0c0c24"}}}}`.
   If the block exposes a plain preset attribute instead (`textColor`,
   `backgroundColor` in `groups.common`), set that directly with a
   theme-preset slug rather than going through `style`.
2. **Elementor**: `elementor-get {post_id}` → find the element's `id` +
   `widgetType`. `editor-schema {builder:"elementor", name:"<widgetType>",
   search:"color"}` → find the exact control id (e.g. `title_color`, or a
   shared one in `groups.common` like `_background_color`) and whether it's
   `responsive`. Then `elementor-update {post_id, element_id, settings:
   {<control_id>: "#0c0c24"}}`.

### Change the content of an existing block

1. **Gutenberg**: find the `blockId` via `gutenberg-get`. Look up the
   block's content attribute via `editor-schema` — usually named `content`
   (rich text) but check `groups.content` for the real name and its `source`/
   `selector` (e.g. an image's text-like fields are `alt`/`title`, not
   `content`). Then `gutenberg-update {post_id, block_id, attributes:
   {content: "New text"}}`.
2. **Elementor**: find the element `id` + `widgetType` via `elementor-get`.
   `editor-schema {builder:"elementor", name:"<widgetType>"}` →
   `groups.content` for the content control id (a heading widget's is
   `title`, a text-editor's is `editor`). Then `elementor-update {post_id,
   element_id, settings: {title: "New text"}}`.

### Create a new block

1. **Gutenberg**: `editor-schema {builder:"gutenberg", name:"<block-name>"}`
   to see what attributes it takes. Then `gutenberg-insert {post_id,
   name:"core/paragraph", attributes:{content:"Hello"}}`. It's appended to
   the END of the post (no positional insert). Nest children with
   `inner_blocks: [{name, attributes, inner_blocks}, ...]` (recursive, same
   shape). A block whose class has no real server-render logic (see the
   Dynamic flag section, above — most static/third-party blocks) may need a
   follow-up `gutenberg-finalize` call so the saved markup passes real
   editor validation; `gutenberg-insert`'s response flags when that's the
   case.
2. **Elementor**: `editor-schema {builder:"elementor", name:"<widget-name>"}`
   to see its settings. Then `elementor-insert {post_id, widget:"heading",
   settings:{title:"Hello"}}`. It's automatically wrapped in a
   section→column and appended to the end of the page. An Essential Addons
   widget not yet registered is auto-enabled for you; a Pro-only or
   not-installed widget returns a `widget_unavailable` error instead.

### Change other styles of an existing block

Same shape as color, generalized:

1. Locate the block/element (`gutenberg-get` / `elementor-get`) for its id.
2. Look up `editor-schema` for that block/widget name:
   - **Gutenberg**: check `style_paths` for the `style.*` path (spacing,
     typography, border, shadow, dimensions, position — see "Styling
     attributes NOT in `attributes`", above) or `groups.common` for a named
     preset attribute.
   - **Elementor**: check `groups.style` (widget-specific appearance) and
     `groups.common` (shared wrapper controls — background, border,
     box-shadow, entrance animation, transform) for the control id and its
     `selectors` (what CSS it actually writes).
3. Write it: `gutenberg-update {attributes: {style: {...}}}` (or a named
   attribute) / `elementor-update {settings: {<control_id>: <value>}}`.

## Known limitations

- Catalog-only Gutenberg entries (block not live-registered) don't carry
  `title`/`description`/`supports`/`style_paths` yet.
- Per-block `search` (with `name`) exists for Gutenberg (see above), but
  there's no GLOBAL variant yet — Elementor's `search` with no `name` scans
  every widget/element at once ("which widget has a control matching X?");
  Gutenberg has no equivalent, so "which blocks support border color" still
  means checking `supports`/`style_paths` per-block yourself.
- The Gutenberg search synonym dictionary was built from ~10,500 real
  Essential Blocks attribute names but EB's naming isn't fully consistent
  across every block/author — an uncommon casing or word-order variant can
  occasionally miss a match (plain, longer words like `"shadow"`/`"gap"`
  don't have this limitation; see the case-sensitivity note above).
- Essential Blocks attribute-generator functions (`generateTypographyAttributes`,
  `generateBackgroundAttributes`, `generateBorderShadowAttributes`,
  `generateDimensionsAttributes`, `generateResponsiveAlignAttributes`,
  `generateResponsiveRangeAttributes`) can't always be expanded — when they
  can't, `fidelity.level` is `"partial"` and `fidelity.unresolved` lists
  which generators were skipped; treat the attribute count as a floor, not
  exact.
