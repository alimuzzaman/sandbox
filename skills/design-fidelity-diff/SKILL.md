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

> **CRITICAL RULE — NATIVE WIDGETS ONLY, NEVER THE HTML WIDGET.** Build every element with a
> real builder widget (heading, button, text-editor, image, icon-list, container) and style it
> with that widget's own controls; fall back to `custom_css` when a control is missing — **never**
> to a `widgetType:"html"` block or hand-written HTML/inline-style markup for layout. An HTML
> widget bypasses the control system the whole workflow depends on and is a hard NO. If a look
> seems to "need" HTML you picked the wrong primitive — see Phase 2. [[never-html-widget-native-controls]]
>
> **CRITICAL RULE — LAYOUT-FIRST, ABSOLUTE IS A FALLBACK.** Reproduce the reference's auto-layout
> with FLOW: flex containers (`flex_direction`/`flex_gap`/`justify_content`/`align_items`) +
> `padding`/`margin` so spacing comes from padding/margin/gap, not coordinates. Use
> `_position:absolute` + offsets ONLY for genuinely overlapping / free-floating elements (text over
> a hero image). A section built entirely from absolute offsets is a smell — rebuild it as flow.
> **Custom CSS is allowed** (not banned like HTML): add a class via the CSS Classes control
> (`_css_classes` on widgets, `css_classes` on containers) and style it in an enqueued stylesheet
> (mu-plugin), or Pro `custom_css`. Fonts still load via a mu-plugin `<link>` (enqueue, not markup).
>
> **CRITICAL RULE — ONE SECTION AT A TIME, GATED AGAINST A MEASURED SPEC (never whole-page-then-diff).**
> The signature, expensive failure of this workflow is generating/building the WHOLE page from
> estimated numbers and diffing only at the end. Per-section height errors then compound into runaway
> vertical drift — every row is off by the SUM of all errors above it — so the final overlay is red
> top-to-bottom (doubled/ghosted text that fans further apart the lower you go) with no single obvious
> cause. This is a rebuild that "looks like a complete mess with everything in the wrong position."
> Two invariants prevent it — treat both as hard gates, not advice:
> 1. **Phase 1 is MANDATORY and MEASURED — a hand-authored generator is a Phase-1 violation.** Before
>    building, extract the reference into a per-section + per-element spec of REAL `getBoundingClientRect`
>    numbers (extract-web.js). A Python/JS generator whose padding/gap/width/height/min-height are
>    *guessed* (literal px you did not read off the reference DOM) has no ground truth to build toward
>    or gate against, so it cannot help but drift. If you are typing a px value into a generator and
>    can't point at the reference measurement it came from, STOP and measure first.
> 2. **Build TOP-DOWN and GATE each section before the next.** After emitting section N, render it and
>    measure its rendered top + height against the reference section (±2px) AND its per-element
>    dTop/dLeft. Do NOT start section N+1 until N passes. The per-section height table is NOT a Phase-4
>    post-mortem you run once at the end — it is a BUILD-TIME gate applied section by section. A section
>    that renders at ~half its reference height is missing rows/cards/content or has collapsed padding:
>    fix the CAUSE (Law 2) and re-gate before moving on. Never proceed past a known-short section.
>
> **Corollary — SECTION-HEIGHT MATCHING IS NOT FIDELITY; gate every LEAF ELEMENT.** A section can render
> at the exact reference height while its interior is wrong, and height-gating is structurally BLIND to
> three defects that make a page "look like nothing is aligned": (a) **internal Y-drift** — the heading
> sits 60px too low inside a correct-height section (over/under-shoots elsewhere cancel in the sum);
> (b) **a MISSING element** — a whole button/text/badge absent, its space absorbed by centering, so the
> height still matches; (c) **a WRONG-SIZED element** — an icon rendering 24px vs 38px, a logo at the
> wrong x. WORSE: gating on section-INDEX height sums can cancel a big positive error (a +120px nav band)
> against a big negative one (a −800px collapsed grid) and report "converged" on a page that is a mess.
> So the real gate is: for each section, enumerate EVERY leaf (`h1..h6, p, img, a, li, svg`) in a
> Y-range on BOTH pages, PAIR them by label/order, and assert per-element `dTop/dLeft/dW/dH` within ±3px
> AND that the counts match (a missing/extra element is an instant fail). Height is a cheap smoke test,
> never the pass condition. If you only ever measured section tops+heights, you have NOT verified fidelity.
>
> **Corollary — LAZY IMAGES MAKE A GOOD SECTION LOOK COLLAPSED; dwell before you measure.** A section
> whose cards contain `loading="lazy"` images (Elementor/EB default) with no width+height attributes
> renders at a FRACTION of its true height until those images actually load — an unloaded `<img>` is
> 0px tall, so the card collapses and the section measures e.g. 880px when its real height is 1250px.
> A fast scroll (≤100ms dwell per step) does NOT give the images time to fetch+decode+reflow, so you
> capture the collapsed state and mis-diagnose a perfectly good section as a "−364 collapse." Before ANY
> section/element measurement: scroll through in steps with a real dwell (≥400ms/step), then confirm
> every in-range `img` has `complete===true && naturalWidth>0` BEFORE trusting a single top/height/count.
> A card that is short ONLY because its image hasn't loaded is a measurement bug, not a build defect —
> re-measure loaded before touching the generator. [[headless-screenshot-image-decode-race]]
>
> **Corollary — the diff must be captured FULL-PAGE at the reference's viewport.** A build screenshot
> shorter than the reference (e.g. build 952px vs reference 9562px) is a BROKEN capture (full-page not
> enabled / lazy images not forced), NOT a short page. Any mismatch % from it is meaningless and hides
> the real drift — confirm `dimensionsMatch` and that both heights are full-page before trusting a
> single diff number. [[headless-screenshot-image-decode-race]]

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

## Phase 1 — Spec the reference into DesignSpec v1 (the extraction standard)
Emit **DesignSpec v1** — ONE canonical, source-agnostic JSON (schema + adapters in
`DESIGNSPEC.md`). The build emits the same shape, so the diff is key-for-key. Do NOT invent a
per-session shape (that drift — `{vw,bodyH}` vs `[{k,fs,fw}]` — is why diffing was fragile).
- **Web** → **RUN `extract-web.js`** (paste the function into `browser_evaluate`; images
  force-loaded; override `window.__DS_ROOT` if section auto-detect is wrong). `fidelity:full`.
  Run it on the reference AND, in Phase 3/5, on the build. **Do NOT hand-roll a shallow
  `browser_evaluate` that grabs text + `backgroundColor`** — that shortcut silently drops every
  gradient, `::before`/`::after` layer, and decorative object-PNG background, producing flat
  white sections that "don't match at all". The tool captures them (`bgOwner.{gradient,image}` +
  `decor[]`); an ad-hoc scrape does not. If you must inline-measure, port `bgOf()`/`decorOf()`.
- **PNG** → `python3 extract-png.py <img> --out spec.json` gives reliable page dims + page bg +
  best-effort band boundaries/colors (`fidelity:low`); then a VISION pass fills each section's
  `elements` (text/kind/≈font/≈box), every value flagged low. If the reference exists ONLY as a
  PNG, cap the done-gate at "visually matches", not ±2px.
- **Figma** → FIRST scope: a node URL usually names a BOARD of many artboards; read
  `get_metadata`, pick the single **page frame** node id (e.g. `Home page`, 1600×tall), target
  THAT. Then by mode (URL `-`→API `:` in the id): **REST** `/v1/files/:key/nodes?ids=<id>` →
  `.document` → `extract-figma.js` `figmaToDesignSpec(frame, meta)`. **Desktop Dev-Mode MCP**
  (`mcp__figma-desktop__*`) is NOT node JSON — merge `get_metadata` (geometry) + `get_design_context`
  (styles/code/assets, must-call to build) + `get_variable_defs` (tokens); `get_screenshot` is the
  Phase-3 baseline. → DesignSpec v1, `fidelity:full`, `colorFormat:hex`. See `DESIGNSPEC.md` for
  the full adapter + Figma gotchas (board-vs-page, huge metadata, MCP disconnects, asset hashes).

DesignSpec captures, per Phase-1 intent: section `top/height/bgOwner{background,gradient,image,
backgroundSize/Position,padding,radius}/decor[]/contentWidth/columns`, and per element
`kind/text|src/top/left/w/h/font{...}/box{...,backgroundImage,backgroundGradient,bgOwner}/image{...}`.
**Backgrounds are captured in full — color, gradient, image, AND pseudo-element/decorative
layers — not just `backgroundColor`.** On EB/Elementor the section's real background almost never
lives on `backgroundColor`: it's a gradient, an inner-wrapper image, a `::before`, or an
absolutely-positioned object PNG. `decor[]` lists those so you REBUILD them (as container
`background_*` / a positioned image widget), never drop them. **The box-model OWNER is explicit**
(`box.bgOwner`): the element with the background must also carry the breathing-room padding (the
"Contact us" bug — panel had the bg, `padding-bottom:0`, button flush to the colored edge). Also
diff `text` length per element — truncated/reworded copy is a top cause of vertical drift that
masquerades as a font problem.
**Exit gate:** a DesignSpec v1 file for the reference exists (sections + elements + bgOwner +
`decor[]`); every section with a non-white reference background has its gradient/image/decor
recorded (a section showing only `background: rgba(0,0,0,0)` when the reference clearly isn't
white means the extractor/root is wrong — fix before building).

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
  3. **native LAYOUT controls** — reproduce the design's auto-layout with flex containers
     (`flex_direction`/`flex_gap`/`justify_content`/`align_items`) + `padding`/`margin`. This is
     the DEFAULT for structure and spacing (the design-context code shows the flex/gap/padding to
     copy). Match gaps with `flex_gap`, offsets with `padding`/`margin` — not coordinates.
  4. **custom CSS via a class** — add a class with the CSS Classes control (`_css_classes` on
     widgets, `css_classes` on containers), then style it in an enqueued stylesheet (mu-plugin
     CSS), or Pro per-element `custom_css` if Pro is active. Available WITHOUT Pro; it is NOT an
     HTML widget. Use for what controls can't express (mixed inline styling, object-fit, etc.).
  5. **absolute positioning — LAST-RESORT FALLBACK.** `_position:absolute` + `_offset_x/y` only
     for genuinely overlapping / free-floating elements (text over a hero image). A whole section
     of absolute offsets is a smell — rebuild as flow (item 3).
  6. **BANNED — HTML widget / inline-style markup / global `<style>`.** A hard NO. Re-pick the
     primitive: two-font single line → two heading widgets; list → icon-list / stacked headings;
     nav → container + heading/button widgets; exact-size media → image widget or container bg.
  - **Abs gotcha (measured):** widget children honor `_position:absolute`; **nested containers
    ignore the injected abs offset** and collapse to flow — another reason to lay out with flow +
    gap. If you must go absolute, do it on a direct widget child, not a wrapping container.
  - **Widget won't match? Swap EL↔EA.** Core Elementor (EL) and Essential Addons (EA, `eael-*`)
    widgets for the "same" thing (button, heading, icon-box, image, accordion, tabs, nav, counter)
    emit **different markup + CSS**, so one is often far easier to style/measure to the reference
    than the other. When a control can't get you there, try the counterpart widget (EL→EA or EA→EL)
    BEFORE reaching for custom CSS — re-run discovery on both (`editor-schema {name}`) to compare
    which exposes the control you need. Prefer whichever lands the design with native controls.
- Look up keys via the discovery method below — do not guess.
**Exit gate:** page renders; the boxed content width == the reference `contentMaxWidth` (set it
explicitly — don't inherit the builder's default); all images load (verify via DOM `naturalWidth`,
not a screenshot) AND every reference asset `src`/decor is actually placed (not just the subset you
remembered).

## Phase 3 — Full diagnosis BEFORE fixing anything (produce all, then rank)
1. **Fonts loaded?** `document.fonts.check('700 56px Archivo')` per family/weight — a matching
   `font-family` with status `unloaded` is a FALSE PASS; inject the webfont yourself
   (`<link>`/`@import`, include the italic axis if accent words are italic).
2. **Content-width parity — match the reference `contentMaxWidth` EXACTLY.** Read it from the
   reference DesignSpec (`page.contentMaxWidth`, e.g. 1240) and set the build's boxed width to the
   SAME number. A 20–24px delta (e.g. building at 1216 vs a 1240 reference) shifts every element's
   `left` by ~12px → the pixelmatch overlay goes red on *every* line even when heights are perfect.
   This is a systemic, whole-page offset — fix it before chasing per-element drift. Also confirm
   the container is capped + centered at a WIDE viewport (~1680), not full-bleed.
3. **Two diff tools — BackstopJS for the web preview, pixelmatch for the numeric locator.**
   For a browsable side-by-side **web report** (reference | test | diff, per viewport) run
   `sb vrdiff <referenceUrl> <buildUrl> --label <name> --viewport 1280x900` (BackstopJS drives its
   OWN full-page screenshots from the URLs, so the build is always captured full-page at the
   reference viewport — this is what makes it immune to the 952px viewport-only capture bug). For
   the **per-band numeric locator** use the pixelmatch tool: `pixelmatch_diff {reference, build,
   diff_out, bands:12}` (MCP) or `sb pxdiff <ref.png> <build.png> --diff-out <p>` — read
   **`worstBands`** to jump straight to the y-ranges that drifted most, and `dimensionsMatch`
   (false → your page height is already wrong). The overlay is a LOCATOR, never the score.
   **Whole overlay red = SYSTEMIC**
   (content-width mismatch per #2, or cumulative height drift per #4), NOT 40 tiny misses — do not
   start per-element. **Classify each red zone:** text doubled/ghosted = position offset (find the
   band via `worstBands` + the height table); solid red blob = missing/wrong image (an absent
   asset, see #7); isolated red rect with clean surrounding text = inherent image diff (ignore).
4. **Per-SECTION height table** (build vs ref) — the anti-accumulation metric.
5. **Per-ELEMENT dTop/dLeft map**, keyed by text/src. `dLeft` should be ~0; if not it's a
   width/alignment/structure bug, not fonts.
6. **Background parity per section.** Diff `bgOwner.{gradient,image}` and the `decor[]` set
   (by `src`) ref↔build. Every reference gradient / object-PNG / pattern must exist on the build.
   A build section that is flat-color (or white) where the reference has a gradient or decorative
   PNG is a defect — list it with the missing `src`/gradient. (This is the class of miss the old
   gate never caught.)
7. **Asset completeness — every reference `src` present.** Collect the set of all image/decor
   `src` filenames from the reference DesignSpec (`elements[].src` + `sections[].decor[].src`) and
   from the build; `reference − build` MUST be empty. Missing assets render as solid-red blobs.
   The Phase-2 "images load" check only proves the images you INCLUDED work — it never notices the
   ones you never added. List every missing `src` with the section it belongs to.
**Exit gate:** every defect listed with measured magnitude + cause — including any missing
background/decor layer AND any missing asset `src`.

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
- **Content width** == reference `contentMaxWidth` (exact). A systemic width delta reddens every
  line in the overlay regardless of heights — this is a gate, not a nicety.
- **dLeft** median ~0, max ≤ ~3px (horizontal off = real bug, not fonts).
- **Asset completeness** (hard gate): `reference_srcs − build_srcs == ∅` (images + `decor[]`).
  Every reference asset is placed; a missing PNG is a solid-red blob, not a rounding error.
- **Background parity** (hard gate): every section's `bgOwner.{gradient,image}` and every
  `decor[]` layer present on the reference is present on the build (matched by `src`/gradient).
  A flat-white/flat-color section where the reference has a gradient or object-PNG FAILS the gate —
  no "close enough". Backgrounds carry the design's identity; a heights-only pass is not done.
- **Per-section height** every section ±2px.
- **Whole-overlay-red is a FAIL, not a floor.** If `pixelmatch_diff` is red across the page, the
  cause is systemic (content width or accumulated height), not the cross-engine sub-pixel floor —
  fix width + section heights until only isolated image rects remain red. "Standing fast" (correct
  content, unmatched heights/width) is a valid INTERIM state but is NOT done; say which one you're in.
- **Per-element dTop** median ≤ ~3px; every residual >5px named with its cause.
- **Control budget** reported; global-`<style>` ≈ 0.
- **Honest cross-engine floor:** literal 0px on every element is NOT achievable rebuilding in
  a different engine — sub-pixel line-height/rounding across ~40 text blocks accumulates.
  Target ±2–3px, state it, never claim "pixel-identical across engines."
Then re-test at 768 + 480 and verify hover.

---

# CONTROL DISCOVERY — find the exact key (`editor-schema` is the primary tool)
`editor-schema` now covers widgets AND structural elements, with ranked search. Use it first:
- **Widget or element controls → `editor-schema {builder, name}`.** `name` = a widget
  (`heading`, `button`, `eael-*`) OR a structural element (`section`, `column`, `container`,
  `e-flexbox`, `e-form`, …) — both resolve (`kind: widget|element`). Container returns
  `flex_gap`/`flex_*`/`margin`/`padding`/`content_width`; column `margin`/`padding`/`css_classes`.
  Scan `groups` (content/style/common) or `controls`. This resolves the merged common controls
  (`_margin`/`_padding`) reliably — the ability primes a REST context first (see next note).
- **`editor-schema {name, search}` — ranked, tokenized, synonym-aware.** Returns `matches`
  sorted by relevance, each with `origin` (core|extension) + `score`. Token-AND with synonyms,
  so human phrases work now (`font size`→`typography_font_size` rank 0; `button text`→
  `button_text` rank 0; `space`→padding/margin; `gap`→`flex_gap`). Core Elementor controls rank
  above `eael_*` noise (`background`/`radius` now surface the core key in the top 1–2). Still
  confirm the intended key by injection for anything ambiguous.
- **`editor-schema {search}` with NO name — GLOBAL search** ("which widget/element has X?").
  Scans all ~263 types (~1s), returns each type's best match, top 40 by score. Use when you
  don't know which widget owns a control (`search:"grayscale"` → `eael-logo-carousel`).
  Options: `types:"widgets"|"elements"` to narrow, `limit:N` to cap.
- **Responsive controls** — Elementor stores ONE base key + an `is_responsive` flag; the
  per-device keys are DERIVED (`{key}_tablet`, `{key}_mobile` for active breakpoints;
  desktop = the bare key). `editor-schema {name}` flags each responsive control
  (`responsive:true`) and returns a `responsive:{breakpoints, controls:[...]}` block. To get
  the exact per-device keys to write, call `editor-schema {name, variants:"typography_font_size"}`
  → `{responsive, breakpoints, variants:{desktop, tablet:..._tablet, mobile:..._mobile}}`.
  Set the desktop value on the base key and each breakpoint on its `_<breakpoint>` key.
- **Raw `get_controls()` is a TRAP — never call it directly.** Elementor v4+ strips the entire
  Advanced/common tab outside a REST context (heading: **623 keys, no `_padding`/`_margin`**);
  primed it returns **879 with them**. The `editor-schema` ability already primes
  (`REST_REQUEST` + reset `Performance::is_frontend` + `clear_stack_cache`) — so USE THE TOOL,
  don't hand-roll `get_controls()`.
- **Plugin source grep → last-resort confirm** of a selector/registration. Fiddly: EA
  registers via shared traits (ids not where you'd guess); `flex_gap` lives in
  `elementor/includes/elements/container.php`.

**Elementor CONTAINER flex keys (verified; wrong key = silent no-op).** These only appear in
`get_controls()` AFTER REST priming (the trap below); inject them by exact name:
- layout: `flex_direction` (`row`/`column`), `flex_justify_content` (`space-between`/`center`/…),
  `flex_align_items` (`center`/`flex-start`/…), `flex_gap` (`{column,row,unit,isLinked}`),
  `flex_wrap`, `flex_align_content`.
- width: `content_width` (`boxed`|`full`); when boxed, the max-width key is **`boxed_width`** (a
  slider) — NOT `width`. `content_width:full` makes a nested flex child stretch to 100% (fills the
  row); there is **no native hug-content** — add a class (`css_classes`) + `width:max-content` in
  the enqueued stylesheet.
- flex-child (on the child): `_flex_grow`, `_flex_shrink`, `_flex_align_self`, `_flex_order`,
  `_flex_size`. NOTE the guessable-but-WRONG names `justify_content`/`align_items`/`width` do
  nothing — always `flex_*`. Verify a set of keys once via primed `get_controls()` per builder.

**CONTAINER DEFAULTS bite (measured — set them explicitly on EVERY container):**
- **`flex_gap` defaults to ~20px**, so a section container silently inserts 20px between its
  children (heading block ↔ row) → a phantom `+20` on everything below. Set `flex_gap` to `0`
  (or the design's gap) on every container, including the top-level section.
- **A nested container's default padding is ~10px** → a uniform `+10` on its content both axes.
  Set `padding` explicitly (usually `0`) on sub-containers.
- **Image widget** won't take a fixed height from a control — give it a class and
  `img{width/height/object-fit:…!important}` in the enqueued stylesheet.
- **A fixed-width image still flex-shrinks below that width inside a flex row** (measured: a `w:260`
  image in a 48% card rendered 169px → its height collapsed proportionally, shorting the whole row).
  WRAP the image in a fixed-width container (a column with `width:<imgw>` + image `width:100%`) so it
  holds its size; a bare image widget as a direct flex child is the shrink trap. This is a top cause
  of a card row measuring too SHORT even though the image `src` and `width` control look correct.
- **Empty Elementor containers collapse to 0 height** even with `min_height`/`background`/`width` set
  (measured: carousel nav dots built as 6 empty styled containers rendered `dotCount:0`). For small
  decorative pips/dots use a real TEXT-GLYPH widget (a heading of `&#9679;` `●` sized/colored), not an
  empty container — a section that silently loses a dots row reads as an unexplained height deficit.
- **A heading with NO explicit line-height renders at ~1.0 (tight); references are usually ~1.2** —
  so EVERY section title comes out ~`0.2×fs` short (measured: an `fs:56` heading rendered 56px vs the
  reference's 67px, i.e. −11px on *every* fs-56 section heading at once). This is a SYSTEMIC per-section
  deficit hiding as many small ones: set `line_height ≈ 1.2×fs` on every heading (read the reference's
  computed `lineHeight`), don't leave it to the kit default. Same trap on text blocks — set `lh`.
- **A multi-row grid's inter-row gap is NOT the section gap** — a `grid(cards, cols=3)` that wraps
  onto 2 rows spaces those rows by the section container's own `flex_gap`, so you cannot tune row
  spacing without also moving every other band. When the reference's row-to-row gap differs from the
  band gap (measured: testimonials rows sat 54px apart while the section band gap was different),
  WRAP the grid in its own column — `col(grid(cards, 3, gap=20), gap=54)` — so the wrapper owns the
  inter-row spacing independently. This is how you close a per-row deficit without disturbing §-level flow.
- Zero `--widgets-spacing` globally (`body{--widgets-spacing:0px}`) since gaps come from `flex_gap`.
Diagnose an unexplained offset by walking the box chain (`getBoundingClientRect` + computed
`gap`/`margin`/`padding` up the ancestors) — don't compensate blindly; find the gap/padding owner.

**Key naming by element type (wrong prefix silently no-ops):** widgets use `_`-prefixed
(`_margin`, `_padding`, `_css_classes`); sections & columns use UNPREFIXED (`margin`,
`padding`, `css_classes`). This extends to POSITIONING/z-index: on a CONTAINER use `z_index`
and `position` (UNPREFIXED); the widget keys `_z_index`/`_position` are silently ignored on a
container (measured: `_z_index` left the nav at computed `z-index:auto`; switching to `z_index`
applied `100`). Kit rule `.elementor-widget:not(:last-child){margin-block-end:
var(--widgets-spacing)}` (spec 0,0,2,0) out-specifies a widget's own `_margin` (0,0,1,0) — a
widget `mb:0` is ignored; override via the widget's `custom_css`. See [[elementor-kit-widget-spacing-gotcha]].
- **After injecting `_elementor_data` directly (wp_eval_live), the per-element CSS does NOT
  regenerate on its own** — `files_manager->clear_cache()` is not enough; positioning/z-index/margin
  rules stay stale and your change looks ignored in the DOM. Force it: `delete_post_meta($id,
  '_elementor_css')` then `\Elementor\Core\Files\CSS\Post::create($id)->update()`, and re-measure.
- **Elementor does NOT emit width / flex-shrink / flex-grow CSS for nested flex-child CONTAINERS
  when the layout is injected via `_elementor_data` (verified):** `min_height`, `flex_direction`,
  `padding`, `background`, `border_radius` all generate their `--var` rules, but `width` (self width),
  `_element_custom_width`, `flex_shrink`, `_flex_shrink`, `flex_grow` produce NO rule — the container
  keeps the default `flex-shrink:1` and collapses to an equal share of its parent. So you CANNOT pin a
  fixed-width flex child (e.g. a pinwheel bento of 357/266px cards) through container settings alone;
  the cards shrink to equal width, wrapping labels and inflating height. FIX = the css_classes +
  enqueued-stylesheet escape hatch: put a stable class on the container (`css_classes` on containers,
  `_css_classes` on widgets) and define `width` + `flex:0 0 <w>` in a mu-plugin stylesheet. Confirm the
  generated rule actually carries a width (`getComputedStyle` + scan `document.styleSheets` for the
  element id) — don't assume a `width` setting took.
- **Overlay nav (transparent nav sitting ON the hero, 0 layout height, like most reference heroes):**
  Elementor won't honor `position:absolute` on a *top-level* container after data injection. Instead
  keep the nav in-flow + TRANSPARENT with `z_index:100`, and give the hero a NEGATIVE top margin
  (`margin=(-navH,0,0,0)`) to pull it up under the nav. Without the z-index the hero (later in DOM)
  paints OVER the nav and hides the logo/CTA — the nav menu text may still show, masking the bug, so
  verify with `elementFromPoint` on the LOGO, not just that "some nav is visible".

---

# EDITOR TOOLS (sandbox abilities — validated; discover → read → mutate)
`*`=required. Thread `base_hash` (from the prior read) into every mutate — a stale hash
returns `conflict` (concurrency guard).
- **editor-schema** `{builder*: elementor|gutenberg, name?, search?, variants?, types?, limit?, source_root?}`
  — discover (Phase 0/2). No name → list (widgets + elements); `name` (widget OR
  section/column/container) → full schema (`kind`, groups, controls, `responsive` block);
  `name`+`search` → ranked matches (`origin`+`score`); `name`+`variants:"<key>"` → per-device
  responsive keys; `search` alone → GLOBAL search (`types`/`limit` to narrow).
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
**Web preview → `sb vrdiff <referenceUrl> <buildUrl> --label <name> --viewport 1280x900`** (BackstopJS;
see `tools/backstop/README.md`). It captures BOTH URLs full-page itself and writes a browsable HTML
report (reference | test | diff) — the fastest way to SEE where the rebuild diverges, and it can't
fall for the viewport-only capture bug. One-time: `npm --prefix tools/backstop install`.
**Numeric locator → use the `pixelmatch_diff` MCP tool (or `sb pxdiff <ref.png> <build.png> --diff-out <p>`)**,
not a hand-rolled node snippet. It crops to the smaller image, writes the red overlay, and returns
`{mismatch, pct, verdict, dimensionsMatch, bands[], worstBands[]}` — read `worstBands` (y-top/height/
pct) as the locator to jump to the drifted section, and `dimensionsMatch:false` means the page height
itself is off. The % is a locator, never the done-gate (Phase 5 numbers are).

# REFERENCE — gotchas
- **Decode race ≠ missing image.** Verify render via DOM (`naturalWidth`, `complete`); a
  full-page headless screenshot blanks fine images. [[headless-screenshot-image-decode-race]].
- **Match viewport width** ref vs build; also check the container at a WIDE viewport.
- **Playwright `browser_take_screenshot`/`browser_evaluate` filename writes to cwd** — pass `tmp/...`.
- **Header/nav is an inherent approximation** (sticky/dropdowns) — spec it as its own section,
  state the approximation, keep it out of the body-fidelity median.
