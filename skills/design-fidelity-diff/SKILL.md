---
name: Design Fidelity Diff
description: Strict, procedural method to rebuild a reference design 1:1 in a page builder (Elementor/Gutenberg) and PROVE fidelity by measuring computed styles in a real browser — section by section, then element by element. Use when rebuilding a Templately/Figma/live design and you must match padding, margin, gaps, fonts, colors, radii, backgrounds, image dims and box-model OWNERSHIP — not just "looks close". Covers control discovery, native-control-first building (Pro Custom CSS over global <style>), a capability probe, and a numeric done-gate.
---

# Design Fidelity Diff

A procedure, not a tip sheet. Follow the phases in order; each has an exit gate. The
expensive failure is working reactively and trusting what you *set* or what the *schema
says* instead of what actually *rendered*. The phases kill that.

## The three laws
1. **MEASURE, never eyeball.** Every fidelity claim is a number read from the live DOM
   (`getComputedStyle` + `getBoundingClientRect`) on BOTH reference and build at the same
   viewport. A screenshot is a locator, never proof.
2. **Match the CAUSE, on the CORRECT element.** Never hit a target with empty space. The
   box-model belongs to the element that owns it in the reference — **the element with the
   background must also carry the padding that gives its content breathing room** (Box-Model
   Owner rule). Splitting background onto one element and its padding onto a parent is a
   defect even if the section height "matches".
3. **VERIFY every change in the rendered DOM.** Builder settings silently no-op (wrong key
   for the element type, control dropped, kit override). After EVERY change re-measure the
   exact thing you changed. A setting you didn't verify probably did nothing.

---

# THE PROCEDURE

## Phase 0 — Capability probe of the target builder (once)
Customized builds differ from stock; prove behavior before relying on it. Build a 2-element
throwaway and MEASURE which settings take effect. Confirm at minimum:
- section `padding`/`content_width` (with `layout:"boxed"`); section `gap:"no"` removes the
  10px `.elementor-widget-wrap` gutter padding.
- column `background_color`/`padding`/`css_classes` (note: column custom-class key is
  `css_classes`, NOT `_css_classes`); widget `_margin`, `_element_width`.
- whether the flexbox **Container** experiment is active (`experiments->is_feature_active('container')`).
- a typography control may NAME a font without LOADING the webfont (silent fallback).
- Pro `custom_css` (`selector{...}`) renders via injection.
- image widgets may lack `.elementor-widget-container` (`<img>` is a direct child).
**Exit gate:** you can name which controls render and your fallback for each.

## Phase 1 — Spec the reference: SECTION pass, then ELEMENT pass
Reference at a fixed viewport (e.g. 1280), images force-loaded. Save raw output to files.
- **1a Section pass** — per top-level band: landmark text, `top`, `height`, **which element
  carries the background**, that element's `padding` + `border-radius`, the content
  `max-width` + left offset (centered?), and the column layout (widths + gap).
- **1b Element pass** — every `h*/p/a/button/img`: `{text|src, top, left, w, h, fontFamily,
  fontSize, fontWeight, lineHeight, color, textAlign}`. AND for each colored element record
  the **box-model owner**: walk up and note which element owns background / padding / margin
  / radius / border. **Background-owner and padding-owner must be the same element** (the
  "Contact us" bug: panel had the bg, `padding-bottom:0`, button bottom-aligned → flush to
  the colored edge). Also diff `textContent.length` per element — truncated/reworded copy is
  a top cause of vertical drift and masquerades as a font problem.
**Exit gate:** per-section table + per-element map + box-model-owner notes saved.

## Phase 2 — Build (native-first, correct element, right primitive)
- **Pick the layout primitive first.** If Container is active, BUILD WITH CONTAINERS, not
  legacy Section/Column. Container has native `flex_gap` (inter-card gaps),
  `flex_align_items`/`flex_justify_content` (vertical centering), `flex_direction`/`flex_wrap`,
  `margin`, `padding`, `content_width` — the exact things that force CSS hacks on legacy
  Section/Column (24px gaps, vertical centering, breathing room were all self-inflicted by
  the wrong primitive; legacy column `margin` is unreliable, Container `flex_gap` is not).
- Put background + its padding + radius on the SAME element (Law 2).
- **Build-vehicle priority** (use the first that the probe proved works), and report a
  **control budget** (native vs custom_css vs global-`<style>`; global ≈ 0):
  1. the element's own native control (button `text_padding`/`background_color`/`border_radius`;
     column/section `background_color`/`padding`; widget `typography_*`; Container `flex_*`).
  2. a native common control verified in Phase 0.
  3. **Pro per-element `custom_css`** (`"custom_css":"selector{...}"`) — renders via injection.
  4. global `<style>` (html widget) — last resort, with a logged reason.
- Look up keys via the discovery method below — do not guess.
**Exit gate:** page renders; all images load (verify via DOM `naturalWidth`, not a screenshot).

## Phase 3 — Full diagnosis BEFORE fixing anything (produce all, then rank)
1. **Fonts loaded?** `document.fonts.check('700 56px Archivo')` per family/weight — a matching
   `font-family` with status `unloaded` is a FALSE PASS; inject the webfont yourself
   (`<link>`/`@import`, include the italic axis if accent words are italic).
2. **Container capped + centered at a WIDE viewport (~1680).** A section can look right at the
   design width yet be full-bleed. Measure `.elementor-top-section>.elementor-container`
   `width`/`maxWidth`/`left`.
3. **pixelmatch overlay = LOCATOR, not a score.** Crop both PNGs to common dims,
   `pixelmatch(...,{threshold:0.1})`. **Classify every red zone by measurement:** text
   doubled/ghosted = position offset (FIXABLE — find the band via the height table); isolated
   red rectangle with clean surrounding text = inherent image diff (ignore). Never wave a
   region off as "just images" without the height table.
4. **Per-SECTION height table** (build vs ref) — the anti-accumulation metric.
5. **Per-ELEMENT dTop/dLeft map**, keyed by text/src. `dLeft` should be ~0; if not it's a
   width/alignment/structure bug, not fonts.
**Exit gate:** every defect listed with measured magnitude + cause.

## Phase 4 — Fix by cause, top-down, SECTION HEIGHTS FIRST, verify each
Per-element `dTop` is mostly cumulative: a section 20px too tall shoves everything below it
20px down. So **make each section's height == reference ±2px, top to bottom, before any
per-element gap work.** Match the cause (padding the reference actually has, image size,
content, wrapping) — not blank space.
- **Measure content height BEFORE adding padding** — reference padding on already-too-tall
  content overshoots.
- **A local fix cascades:** before pulling one element up, check the elements below; if they
  are already aligned, moving the block edge mis-aligns all of them. Re-measure after each
  batch; a cluster of new same-signed outliers below the edit = "you changed a height", revert.
**Exit gate:** every section height ±2px; per-element dTop median ≤ 3px.

## Phase 5 — Done-gate (numeric) + responsive + hover
- **dLeft** median ~0, max ≤ ~3px (horizontal off = real bug, not fonts).
- **Per-section height** every section ±2px.
- **Per-element dTop** median ≤ ~3px; every residual >5px named with its cause.
- **Control budget** reported; global-`<style>` ≈ 0.
- **Honest cross-engine floor:** literal 0px on every element is NOT achievable rebuilding in
  a different engine — sub-pixel line-height/rounding across ~40 text blocks accumulates.
  Target ±2–3px, state it, never claim "pixel-identical across engines."
Then re-test at 768 + 480 and verify hover.

---

# CONTROL DISCOVERY — find the exact key (validated method, hybrid)
No single source is complete; use this order:
- **Widget controls → `editor-schema {builder, name}` (full).** Authoritative; resolves the
  merged common controls (`_margin`/`_padding`/`typography_*`) that a cold raw `get_controls()`
  silently omits. Scan `groups` (content/style/common) or `controls`. This is also how you
  discover an unknown widget's real key (e.g. an EA control id you can't guess).
- **Section / Column / Container → `elements_manager->get_element_types(type)->get_controls()`.**
  `editor-schema` is WIDGET-ONLY (`section`/`column`/`container` → `not_found`). Container has
  `flex_gap`/`flex_*`/`margin`/`padding`/`content_width`; column has `margin`/`padding`/`css_classes`.
- **`editor-schema` `search` is a HINT only.** Per-widget; case-insensitive SUBSTRING over
  id+label+description+selectors; no ranking, no tokenization. Single id-tokens rank well
  (`padding`,`radius`,`width`); human phrases MISS (`font size`→0, `button text`→0); EA-Pro
  floods precision (`background`→core at 48/124, `radius`→14/17). Scan the full list for the
  non-`eael_` id; confirm by injection.
- **Plugin source grep → last-resort confirm** of a selector/registration. Fiddly: EA
  registers via shared traits (ids not where you'd guess); `flex_gap` lives in
  `elementor/includes/elements/container.php`.

**Key naming by element type (wrong prefix silently no-ops):** widgets use `_`-prefixed
(`_margin`, `_padding`, `_css_classes`); sections & columns use UNPREFIXED (`margin`,
`padding`, `css_classes`). Kit rule `.elementor-widget:not(:last-child){margin-block-end:
var(--widgets-spacing)}` (spec 0,0,2,0) out-specifies a widget's own `_margin` (0,0,1,0) — a
widget `mb:0` is ignored; override via the widget's `custom_css`. See [[elementor-kit-widget-spacing-gotcha]].

---

# EDITOR TOOLS (sandbox abilities — validated; discover → read → mutate)
`*`=required. Thread `base_hash` (from the prior read) into every mutate — a stale hash
returns `conflict` (concurrency guard).
- **editor-schema** `{builder*: elementor|gutenberg, name?, search?, source_root?}` — discover
  (Phase 0/2). No name → list; name → full schema; +search → filter (hint). Elementor branch
  is widget-only.
- **elementor-get / gutenberg-get** `{post_id*}` → tree (ids, elType/widgetType) + `state_hash`.
- **elementor-insert** `{post_id*, widget*, settings?, full_width?, base_hash?}` /
  **gutenberg-insert** `{post_id*, name*, attributes?, inner_blocks?, inner_html?, base_hash?}`
  — adds ONE element (Elementor wraps section›column›widget). For a WHOLE page prefer
  injecting `_elementor_data` wholesale via `execute-php` + `Document::save`
  ([[elementor-page-data-injection-recipe]]) — far fewer calls.
- **elementor-update / gutenberg-update** `{post_id*, element_id|block_id*, settings|attributes, base_hash?}` — merge per key.
- **elementor-delete / gutenberg-delete** `{…, confirm:true*, base_hash?}` — confirm-gated.
Guards (rely on them): stale hash→`conflict`, unknown id→`not_found`, no confirm→`confirm_required`,
unknown/Pro-absent widget→`widget_unavailable` (insert auto-enables a disabled `eael-*`),
missing→`bad_input`. Discovery fidelity by builder: Elementor widgets full/live (heading=879
controls); EB Gutenberg blocks may resolve only `"partial"` without a `src/controls` checkout
(pass `source_root` for full).

---

# REFERENCE — measurement snippets
**Force-load images + bounded wait (prepend to any full-page measure/screenshot):**
```js
document.querySelectorAll('img[loading="lazy"]').forEach(i=>{i.loading='eager';i.src=i.src;});
await new Promise(r=>setTimeout(r,1500));
const d=[...document.images].map(i=>i.decode().catch(()=>{}));
await Promise.race([Promise.all(d), new Promise(r=>setTimeout(r,8000))]); // NEVER await unbounded decode()
```
**Per-element map (run identically on ref + build; key by text/src; diff dTop/dLeft):**
```js
const r2=n=>Math.round(n),box=el=>el.getBoundingClientRect(); const key=s=>s.replace(/\s+/g,' ').trim().slice(0,40).toLowerCase();
const out=[]; document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,button,img').forEach(el=>{
  const b=box(el); if(b.width<3||b.height<3) return; const im=el.tagName==='IMG';
  const t=im?(el.currentSrc||el.src).split('/').pop():el.textContent; if(!im&&!key(t))return;
  out.push({k:(im?'img:'+t.slice(0,40):el.tagName+':'+key(t)),top:r2(b.top+scrollY),left:r2(b.left),w:r2(b.width),h:r2(b.height)});}); return out;
```
**Box-model owner walk (on a colored element):**
```js
let el=node,chain=[]; for(let i=0;i<5&&el;i++){const c=getComputedStyle(el);
  chain.push({cls:el.className.toString().slice(0,40),bg:c.backgroundColor,pad:c.padding,radius:c.borderRadius}); el=el.parentElement;} return chain;
```
**Per-section height table:** heights of top-level sections on both pages; diff per section (NOT cumulative tops).
**pixelmatch (node, pngjs+pixelmatch):** crop both PNGs to `min(w,h)`; `pixelmatch(a,b,diff,w,h,{threshold:0.1})`; read % as a locator only.

# REFERENCE — gotchas
- **Decode race ≠ missing image.** Verify render via DOM (`naturalWidth`, `complete`); a
  full-page headless screenshot blanks fine images. [[headless-screenshot-image-decode-race]].
- **Match viewport width** ref vs build; also check the container at a WIDE viewport.
- **Playwright `browser_take_screenshot`/`browser_evaluate` filename writes to cwd** — pass `tmp/...`.
- **Header/nav is an inherent approximation** (sticky/dropdowns) — spec it as its own section,
  state the approximation, keep it out of the body-fidelity median.
