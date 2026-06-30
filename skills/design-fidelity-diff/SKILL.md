---
name: Design Fidelity Diff
description: Strict, procedural method to rebuild a reference design 1:1 in a page builder (Gutenberg/Elementor) and prove fidelity by measuring computed styles in a real browser — section by section, then element by element. Use when rebuilding a Templately/Figma/live design and you must match padding, margin, gaps, fonts, colors, radii, backgrounds, image dims and box-model OWNERSHIP, not just "looks close". Mandates a capability probe, native-control-first building, the Elementor Pro Custom CSS control over global <style>, and a numeric done-gate.
---

# Design Fidelity Diff — strict rebuild procedure

This is a **procedure, not a tip sheet**. Follow the phases IN ORDER. Each phase has an
exit gate; do not advance until it is met. The repeated, expensive failure mode is
working reactively (fix one symptom, re-render, find the next) and trusting values you
*set* or the *schema* instead of what actually *rendered*. The phases below exist to kill
that.

## THE THREE LAWS (violating any one is the root of every failure)
1. **MEASURE, never eyeball.** Every fidelity claim is backed by a number read from the
   live DOM (`getComputedStyle` + `getBoundingClientRect`) on BOTH reference and build at
   the same viewport. A screenshot is a locator, never proof of done.
2. **Match the CAUSE, on the CORRECT element.** Never hit a target number with empty
   padding/margin. And the box-model belongs to the element that *owns* it in the
   reference — **the element with the background color MUST also carry the padding that
   gives its content breathing room** (see the Box-Model Owner rule). Splitting background
   onto one element and padding onto its parent is a defect even when the section height
   "matches".
3. **VERIFY every change in the rendered DOM.** In customized builds, settings silently
   no-op: the schema lists controls that don't apply and omits ones that do. After EVERY
   change, re-measure the exact thing you changed. A setting you didn't verify probably
   did nothing.

## Tool priority for ANY visual property (use the first that the probe proves works)
1. **The element's own native style control** — `button` `text_padding`/`background_color`/
   `border_radius`, `column`/`section` `background_color`, widget `typography_*`,
   `_element_width`. These are reliable.
2. **A native common control** — ONLY if Phase 0 proved it renders in this build (in the
   ChatAIBot build, section `padding` applied but column `_margin`, `content_position`,
   `_css_classes` did NOT).
3. **Elementor Pro per-element Custom CSS control** (`custom_css` setting; scope with the
   `selector` keyword). VERIFIED to render via injected `_elementor_data` (Pro 4.1.1).
   `"custom_css": "selector{padding-bottom:35px}"` on the element. This is the correct
   vehicle for anything native controls can't express — NOT a global `<style>`.
4. **Global `<style>` (html widget)** — LAST resort only, with a one-line logged reason
   why 1–3 couldn't do it. Each global rule is debt; keep the count near zero.

**Control budget (report it):** at the end, count native controls used vs `custom_css`
uses vs global-`<style>` rules. Global-style count should be ~0; every one is justified in
writing. (A build that used 0 `custom_css` and 5 global-style hacks was doing it wrong.)

---

## PHASE 0 — Capability probe of the TARGET builder (MANDATORY, once)
**Use the right introspection lens, then verify by injection.** Padding/margin/background
are standard Elementor **common controls** and they DO exist — do not conclude otherwise.

- **Enumerate controls via the project `editor-schema` ability, NOT raw `get_controls()`.**
  `$widget->get_controls()` returns the common-control SECTION *containers* (e.g.
  `_section_style`) but NOT the merged controls inside them, so padding/margin falsely look
  absent. `sandbox_editor_schema(['builder'=>'elementor','name'=>'heading'])['groups']['common']`
  resolves them correctly: `_section_style` → `_margin` + `_padding`; plus `_section_background`,
  `_section_border` (border + radius + shadow), `section_effects`, `_section_transform`,
  `_section_responsive`, `_section_attributes`, and **`section_custom_css`** (Pro).
- **Key prefix depends on element type — getting it wrong SILENTLY no-ops** (this, not a
  "build quirk", is what made settings "disappear" and caused most thrashing):
  **widgets** use `_margin` / `_padding` / `_css_classes` (underscore); **sections & columns**
  use `margin` / `padding` / `css_classes` (NO underscore). Verified on the ChatAIBot build:
  column `css_classes` rendered the class; column `_css_classes` did nothing.
- **Still verify EACH control by injection** — a few behave oddly even with the right key
  (column `margin` read 0 in test; use the column gap / `custom_css` instead). Image
  widgets have **no `.elementor-widget-container`** (`<img>` is a direct child of
  `.elementor-widget-image`). A typography control may NAME a font without LOADING it
  (silent fallback → inject the webfont yourself). Pro `custom_css` (`selector{...}`) DID
  render via injection.

So: build a 2-section throwaway page and **measure** which of these actually take effect.
Record PASS/FAIL for each before building the real page:

| Probe | How to verify |
|---|---|
| section `padding` / `content_width` (boxed) | measure section + `.elementor-container` maxWidth at a WIDE viewport (~1680) |
| section `gap:"no"` removes `.elementor-widget-wrap` 10px padding | measure widget-wrap padding |
| column `background_color` / `padding` | computed bg + padding on the column |
| column `_margin` / `content_position` / `_css_classes` | does it appear in DOM / change layout? (often NO) |
| widget `_margin` (e.g. image `mt`) | computed margin |
| `_css_classes` on widget/column | `document.querySelectorAll('.x').length` |
| image widget DOM shape | is there `.elementor-widget-container`? |
| typography control LOADS font | `document.fonts.check('700 56px Archivo')` |
| Pro `custom_css` (`selector{...}`) | inject `selector{box-shadow:0 0 0 6px lime inset}`, check computed |

**Exit gate:** you can name, for this build, exactly which native controls render and what
your fallback is (custom_css). You will not "discover" a quirk mid-fix again.

---

## PHASE 1 — Spec the reference: SECTION pass, then ELEMENT pass (the two-level scan)
Run on the reference at a FIXED viewport (e.g. 1280) via Playwright `browser_evaluate`,
images force-loaded. Save raw output to files (`tmp/<name>-spec.json`). Two passes,
strictly:

### 1a. SECTION pass — one row per top-level band
For each band record: landmark text, `top`, `height`, **which element carries the
background** (the section, or an inner column/panel?), that element's `padding` + `border-
radius`, the content **max-width** and left offset (centered?), and the column layout
(widths + the gap between columns).

### 1b. ELEMENT pass — every element inside each section
For EVERY `h*/p/a/button/img`: `{text|src, top, left, w, h, fontFamily, fontSize,
fontWeight, lineHeight, color, textAlign}`. AND, for each element, answer the **box-model
owner** question explicitly:

> For background, padding, margin, border-radius, border — **which element in the
> hierarchy carries it?** Walk up from the visible node and record the owner of each.

**THE BOX-MODEL OWNER RULE (the "Contact us" lesson — do not skip):**
The element that has the **background color** must also carry the **padding** that creates
space between its content and its colored edge. Record `bgOwner` and `paddingOwner` for
every colored element; if the reference has them on the SAME element, the build MUST too.
Failure example: a CTA panel had the cream/yellow background but `padding-bottom:0`, and
the button was bottom-aligned → the button sat flush on the colored bottom edge, no
breathing room, "ugly". The fix is padding on the bg element (or not bottom-aligning into a
zero-padding edge) — never "it's close enough".
Also: a button's own background+padding live on `.elementor-button` (control
`background_color` + `text_padding`); confirm the visible colored pill is that element, not
a child/parent.

**Exit gate:** a per-section table AND a per-element map AND a box-model-owner note for
every colored element exist as files. Fonts + `:hover` captured.

---

## PHASE 2 — Build (native-first, on the CORRECT element)
**Choose the layout primitive FIRST.** If the build has the flexbox **Container** active
(`experiments->is_feature_active('container')` — it was, on ChatAIBot), BUILD WITH
CONTAINERS, not legacy Section/Column. Container has native `flex_gap` (inter-card gaps),
`flex_align_items`/`flex_justify_content` (vertical centering), `flex_direction`/`flex_wrap`,
`margin`, `padding`, `content_width` — i.e. the exact things that, on legacy Section/Column,
forced CSS hacks (the 24px card gap, integration-text centering, button breathing room were
ALL self-inflicted by choosing Section/Column). Legacy column `margin` is unreliable;
Container `flex_gap` is not.
Generate the builder JSON (`_elementor_data`) with native controls per the Phase-0 toolset.
For every colored element, put background AND its padding AND radius on the SAME element
(Law 2). Use `custom_css` for what natives can't express; global `<style>` only with a
logged reason. Look up keys via `editor-schema` for widgets and
`elements_manager->get_element_types(<type>)->get_controls()` for section/column/container
— do not guess key names.

**Exit gate:** page renders, all images load (verify via DOM `naturalWidth`, not a
screenshot — decode race shows blank bands for fine images).

---

## PHASE 3 — FULL diagnosis BEFORE fixing anything (produce ALL, then rank)
Do not fix between measurements. Produce, in order:
1. **Fonts loaded?** `document.fonts.check(...)` for every family/weight. A matching
   `font-family` string with `status:"unloaded"` is a FALSE PASS — inject the webfont
   (`<link>`/`@import`) yourself; include the italic axis if accent words are italic.
2. **Container capped + centered at a WIDE viewport (~1680).** A section that looks right
   at the design width can be full-bleed (side padding coincidentally caps it). Measure
   `.elementor-top-section>.elementor-container` `width`/`maxWidth`/`left`. (`layout:boxed`
   + `content_width`; or a `custom_css`/global `max-width;margin-inline:auto`.)
3. **pixelmatch overlay = LOCATOR, not a score.** Crop both full-page PNGs to common dims,
   `pixelmatch(...,{threshold:0.1})`. The % is meaningless cross-engine. CLASSIFY every red
   zone by measurement, never by glance:
   - text **doubled/ghosted** = position/layout offset → FIXABLE (find the band via the
     height table).
   - isolated red **rectangle, clean surrounding text** = inherent image diff → ignore.
   Never dismiss a region as "just images" without the height table proving it.
4. **Per-SECTION height table** (build vs ref) — the anti-accumulation metric.
5. **Per-ELEMENT dTop/dLeft map** keyed by text/src. `dLeft` should be ~0 (horizontal);
   if not, it's width/alignment/structure, not fonts.

**Exit gate:** every defect is listed with its measured magnitude and suspected cause.

---

## PHASE 4 — Fix by CAUSE, TOP-DOWN, SECTION HEIGHTS FIRST, verify each
**Anti-accumulation (why per-element dTop never reaches zero if you skip it):** per-element
`dTop` is mostly cumulative — a section 20px too tall shoves everything below it 20px down.
So **make each section's measured height == reference ±2px, top to bottom, BEFORE any
per-element gap work.** Match the cause of each height delta (padding the reference
actually has, image size, content, wrapping), not blank space.
- **Measure content height BEFORE adding padding.** Reference padding added on top of
  already-too-tall content overshoots (real miss: forcing footer 64/48 + CTA 35/65 padding
  blew both out because their *content* was already too tall). Reduce content first.
- **Local fix → global cascade:** before pulling one element up with negative margin, check
  the elements BELOW it — if they're already aligned, moving the block edge mis-aligns all
  of them. Re-run the gap pass after each batch; a cluster of new same-signed outliers
  below the edit means "you changed a height," revert.
- **Box-model owner on every fix** (Law 2): if a fix is "add breathing room", it goes on
  the element with the background.

Only when every section height matches do the residual per-element gap pass: for each
off element measure the specific gap preceding it on both pages and set ours to match;
gaps cascade, re-measure the column after each change.

**Exit gate:** per-section height all within ±2px; per-element dTop median ≤ 3px.

---

## PHASE 5 — Done-gate (numeric, decided up front), then responsive + hover
- **dLeft:** median ~0, max ≤ ~3px. If horizontal is off it's a real bug, not fonts — fix it.
- **Per-section height:** every section ±2px of reference.
- **Per-element dTop:** median ≤ ~3px; every residual >5px named with its cause.
- **Control budget:** report native-control count vs `custom_css` vs global-`<style>` (≈0).
- **Honest cross-engine floor:** literal 0px on every element is NOT achievable rebuilding
  in a different engine — sub-pixel line-height/rounding across ~40 text blocks accumulates.
  Target ±2–3px, state it, match every cause you can, document the rounding residual. Never
  claim "pixel-identical across engines."
Then re-test at 768 + 480 and verify hover states.

---

## Reference A — measurement snippets

**Force-load images + bounded wait (prepend to any full-page measure/screenshot):**
```js
document.querySelectorAll('img[loading="lazy"]').forEach(i=>{i.loading='eager';i.src=i.src;});
await new Promise(r=>setTimeout(r,1500));
const d=[...document.images].map(i=>i.decode().catch(()=>{}));
await Promise.race([Promise.all(d), new Promise(r=>setTimeout(r,8000))]); // NEVER await unbounded decode()
```

**Per-element map (run identically on ref + build; key by text/src; diff dTop/dLeft):**
```js
const r2=n=>Math.round(n),box=el=>el.getBoundingClientRect(),cs=el=>getComputedStyle(el);
const key=s=>s.replace(/\s+/g,' ').trim().slice(0,40).toLowerCase(); const out=[];
document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,button,img').forEach(el=>{
  const b=box(el); if(b.width<3||b.height<3) return; const im=el.tagName==='IMG';
  const t=im?(el.currentSrc||el.src).split('/').pop():el.textContent; if(!im&&!key(t))return;
  out.push({k:(im?'img:'+t.slice(0,40):el.tagName+':'+key(t)),top:r2(b.top+scrollY),left:r2(b.left),w:r2(b.width),h:r2(b.height)});
}); return out;
```

**Box-model owner walk (run on a colored element to find bg-owner vs padding-owner):**
```js
let el=node,chain=[]; for(let i=0;i<5&&el;i++){const c=getComputedStyle(el);
  chain.push({cls:el.className.toString().slice(0,40),bg:c.backgroundColor,pad:c.padding,radius:c.borderRadius}); el=el.parentElement;}
return chain; // the bg!=transparent row is the bg-owner; its pad must give breathing room
```

**Per-section height table:** dump `[...document.querySelectorAll('<top-section-selector>')]`
heights on both pages; diff per section (NOT cumulative tops).

**pixelmatch (node, pngjs+pixelmatch):** crop both PNGs to `min(w,h)`, `pixelmatch(a,b,diff,w,h,{threshold:0.1})`; write diff png; read the % as a locator only.

## Reference B — verified control map + KEY-NAMING by element type (re-probe on a new build!)
Discover keys via `editor-schema` `groups` (content/style/common); the `_section_style`
common section holds margin/padding. **Mind the prefix:**
- **Widget keys are `_`-prefixed:** `_margin`, `_padding`, `_css_classes`, `_element_width`,
  `_background_color`; plus `typography_*`, and per-widget style controls. Verified to apply:
  `_element_width`, `_background_color`, `typography_*`, widget `_margin` (image `mt` worked).
  Button: `text_padding` + `background_color` + `border_radius` on `.elementor-button`.
  Image widget has no `.elementor-widget-container` (`<img>` is the direct child).
- **Section/Column keys are UNPREFIXED:** `margin`, `padding`, `css_classes`. Verified:
  section `padding` + `content_width` (with `layout:"boxed"`); section `gap:"no"` removes
  the 10px `.elementor-widget-wrap` gutter padding; section/column `background_*`; column
  `padding`; **column `css_classes` renders a real class** (use it to scope CSS — no fragile
  `:has()` needed). Using `_css_classes`/`_margin` (widget prefix) on a column SILENTLY
  no-ops — that was the bug, not a missing control. Column `margin` read 0 in test → use
  the section column-gap or `custom_css` for inter-column spacing instead.
- **Custom CSS:** Pro `custom_css` per element renders via injection — scope with `selector`
  (the proper vehicle for anything the native controls don't cover).

**Discovery-tool limits (benchmarked — trust accordingly):** `editor-schema` is the right
lens for WIDGET controls (it resolves the merged common controls that a raw `get_controls()`
call drops — that resolution is cache/context-dependent, so never use raw `get_controls()`).
But: (1) **the tool is WIDGET-ONLY** — `editor-schema` enumerates `get_widget_types()`, so
`section`/`column`/`container` return `WP_Error "not registered"`. The elements absolutely
EXIST (this build registers `section, column, container, e-div-block, e-flexbox, e-tabs,
e-form`); they live in a different registry and are fully introspectable directly:
`\Elementor\Plugin::$instance->elements_manager->get_element_types('container')->get_controls()`
(container = 853 controls incl. `flex_gap`/`flex_direction`/`flex_align_items`/`margin`/
`padding`/`content_width`). For structural controls, query `elements_manager` directly.
(2) **`search` has no relevance ranking and
floods with EA-Pro controls** on an EA-heavy instance: e.g. `search:"radius"` → core
`_border_radius` ranked 14/17; `"background"` → `_section_background` ranked 48/124;
`"border"` 44/54. Scan the FULL match list for the core (non-`eael_`) id, don't take the
top hit. (3) **`search` is literal substring** over id/label/desc/selector — conceptual or
multi-word terms miss: `"font size"`→0 (it's `typography_font_size`/label "Size"),
`"space"`→0, `"dimension"`→0. Search single id-like tokens (`padding`, `margin`,
`custom css`) and treat search as a hint, not ground truth — confirm by injection.
- **Kit:** `.elementor-widget:not(:last-child){margin-block-end:var(--widgets-spacing)}`
  (spec 0,0,2,0) out-specifies a widget's own `_margin` (0,0,1,0) — a widget `mb:0` is
  ignored; override via the widget's `custom_css` or a higher-specificity rule.
  See [[elementor-kit-widget-spacing-gotcha]].

## Reference C — gotchas
- **Decode race ≠ missing image.** Verify image render via DOM (`naturalWidth`,
  `complete`), not a full-page headless screenshot (blank bands are decode timing). See
  [[headless-screenshot-image-decode-race]].
- **Match viewport width** ref vs build before comparing geometry; also check container at a WIDE viewport.
- **Playwright `browser_take_screenshot` / `browser_evaluate` filename writes to cwd** — pass `tmp/...`.
- **Truncated/reworded copy** is a top cause of vertical drift (fewer wrapped lines →
  shorter section). Diff `textContent.length` per element before blaming fonts.
- **Header/nav is an inherent approximation** (sticky/dropdowns) — spec it as its own
  section and state the approximation; keep it out of the body-fidelity median.
- Inject Elementor page data via the [[elementor-page-data-injection-recipe]] memory note.

## Reference D — editor tools (sandbox abilities; all validated green)
**Order of use: discover → read → mutate.** (`*` = required; thread `base_hash` from the
prior read into every mutate — a stale hash returns `conflict`, the concurrency guard.)
1. **editor-schema** `{builder*: elementor|gutenberg, name?, search?, source_root?}` — FIRST,
   to learn control keys/options before building. No `name` → list (Elementor = 250 widgets);
   `name` → full schema (Elementor groups `content/style/common` + `controls`/`count`; Gutenberg
   `attributes` + `fidelity`). **Elementor branch is WIDGET-ONLY** — section/column/container
   error here; introspect those via `elements_manager->get_element_types(<type>)->get_controls()`
   (Reference B).
2. **elementor-get / gutenberg-get** `{post_id*}` → element/block tree (ids, elType/widgetType)
   + `state_hash`. Read-before-write: take the ids for update/delete and the hash as `base_hash`.
3. **elementor-insert** `{post_id*, widget*, settings?, full_width?, base_hash?}` /
   **gutenberg-insert** `{post_id*, name*, attributes?, inner_blocks?, inner_html?, base_hash?}`
   — adds ONE element (Elementor wraps it in section›column›widget; returns `element_id`/`blockId`
   + `widget_survived`). For a WHOLE page prefer injecting `_elementor_data` wholesale via
   `execute-php` + `Document::save` (the [[elementor-page-data-injection-recipe]]) — far fewer calls.
4. **elementor-update / gutenberg-update** `{post_id*, element_id|block_id*, settings|attributes, base_hash?}`
   — merge per key/attr into one element located by id (responsive/typography/repeater round-trip).
5. **elementor-delete / gutenberg-delete** `{…, confirm:true*, base_hash?}` — confirm-gated.
   `gutenberg-finalize {post_id, block_spec, base_hash?}` queues a static block for the headless finalizer.

**`search` (control search) — accepted values + behavior:** it is **per-WIDGET** (filters the
named widget's controls; there is NO cross-widget/global search — you must know the widget).
Matching = case-insensitive **SUBSTRING** of the query over `id + label + description +
selector keys/values`, returned in registration order — **no ranking, no tokenization**. So
id-like tokens work (`padding`→`_padding`), but core controls sink under EA-Pro on noisy terms
(`radius`→`_border_radius` rank 14/17; `background`→`_section_background` 48/124) and literal
terms miss (`font size`→0, `space`→0, `dimension`→0). Treat search as a hint: scan the full
match list for the non-`eael_` core id, then **verify by injection**.

**Validated guards (rely on them):** stale `base_hash`→`conflict`; unknown id→`not_found`;
delete without `confirm`→`confirm_required`; unknown / Pro-absent widget→`widget_unavailable`
(insert also auto-enables a disabled `eael-*`); missing required→`bad_input`.

**Discovery FIDELITY differs by builder:** Elementor widgets resolve FULL/live (heading = 879
controls); **EB Gutenberg blocks may resolve only `"partial"`** without a `src/controls`
checkout (e.g. `essential-blocks/button` = 5 attrs) — check the `fidelity.level` before
trusting a GB block's attribute set (pass `source_root` to point at a checkout for full fidelity).
