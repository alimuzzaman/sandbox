---
name: Design Fidelity Diff
description: Verify a built page matches a reference design by diffing COMPUTED STYLES per section in a real browser — not by eyeballing screenshots. Use when rebuilding a Templately/Figma/live design as a Gutenberg or Elementor page and you must confirm pixel/spec fidelity (widths, padding, gaps, fonts, colors, radii, image dims, hover states). Catches discrepancies a downscaled screenshot hides.
---

# Design Fidelity Diff

Confirm a built page matches a reference design by **measuring computed styles in a
real browser and diffing the numbers**, section by section. A downscaled full-page
screenshot (1280px squished to ~560) hides gaps, padding, fonts, radii, and hover
state — comparing pictures by eye is the #1 cause of "looks done but isn't".

## Golden rule
**Spec the reference FIRST, build to the numbers, then diff. Never declare done on a
screenshot.** Done = the per-section diff is empty (±2px tolerance).

## RULE 0 — match the CAUSE, never fake the NUMBER (hard-won, do not skip)
A matched metric with the wrong structure is a **FALSE PASS**, and worse than no match
because it hides the real defect. NEVER hit a target by adding empty space:
- ❌ **Section the wrong height?** Do NOT add empty padding/margin to force the height or
  a scroll-top segment to line up. The reference's extra height comes from a REASON —
  a different **layout** (rows vs columns, inline vs stacked), **more content/elements**,
  a **divider**, a taller **image**, different **wrapping**. Find that reason and
  replicate it. (Real failure: a footer was made "the right height" with +300px of blank
  padding while its actual layout — nav inline-right on the top row, a divider, wordmark
  left + hours right — was never built. The number matched; the footer was wrong.)
- ❌ Do NOT absorb a text-wrap shortfall (e.g. font-metric line-count differences) with
  padding on the next section. If you cannot fix the cause (e.g. environmental font
  metrics), SAY SO — don't paper over it.
- ✅ Before changing any spacing to fix drift, ask **"what is structurally different
  here?"** Compare the two layouts' element ARRANGEMENT — order, rows/columns, alignment,
  dividers, and the actual TEXT CONTENT (hours, copyright, labels) — not just per-element
  computed styles. A per-property diff that's "clean" can still sit on a wrong layout.
- If a fix would be empty padding, stop and escalate to a structural rebuild of that
  section instead. Note any padding used purely for spacing so it's auditable, never
  silent.

Apply this to EVERY section, not just the ones that look off — audit layout + content
fidelity per section, not only the measured properties.

**Audit ALIGNMENT separately — element box position ≠ visible text/image position.**
`getBoundingClientRect()` on a centered `<h*>/<p>` returns the FULL-WIDTH element box,
so a per-element `dLeft` reads ~0 even when the text is visibly centered-vs-left. The
pixel diff then shows text/icons doubled horizontally by ~half their width. To catch it:
compare computed **`text-align`** per element (NB: `start` === `left` for LTR — that's a
false mismatch, ignore it), and for images compare the **inset within the parent**
(`img.left - card.left`: ≈padding = left, ≈centered otherwise). Real cases: icon-box
icons were `center` but the demo is `left` (≈110px off); Elementor's image-widget `align`
control silently didn't apply — force with CSS `.elementor-widget-image{text-align:left}
img{margin-left:0!important;margin-right:auto!important}`.

**Header/nav is usually a rough approximation — audit it explicitly.** A quick text-link
rebuild won't match the reference's real menu: logo size, item spacing, font
weight/family, and collapsed items (e.g. demo shows "Others ▾" dropdown where the build
lists "Blog Contact"). Treat the header as its own section to spec + rebuild, not an
afterthought.

**Diff TEXT CONTENT + LENGTH per element FIRST — before blaming fonts/spacing.**
Extract every `h*/p` from both pages and compare `textContent.length` keyed by a text
prefix. **Truncated or reworded copy is a top cause of vertical drift** and it
*masquerades as a font-metric problem*: shorter text wraps to fewer lines → the section
is shorter → everything below drifts. (Real case, cost hours: the mission and
"Designed for Safety" paragraphs were each missing their final sentence(s); ours wrapped
3 lines vs the demo's 4, and I wrongly concluded "different DM Sans font metrics" — when
both sites load the *same* Google-Fonts DM Sans and the only difference was 105 missing
characters. Restoring the full text aligned the whole upper page.) So: when a multi-line
text block is the wrong height, check its **character count vs the reference** before
touching CSS. Fonts are almost never the cause; content and spacing are.

**Contained/"floating card" sections: audit ALL FOUR sides + watch margin-collapse.**
A card inset from the page (footer, hero panel) has a gap top, bottom, left AND right —
measure every side (`card.top-prev.bottom`, `page_height-card.bottom`,
`card.left`, `viewport-card.right`), not just the one that looks wrong. A trailing
**bottom** gap below the LAST section must come from **`padding-bottom` on a PARENT**
(e.g. `.elementor-<id>` page wrapper) — NOT `margin-bottom` on the section, which
**collapses** through a paddingless/borderless wrapper to the body and yields 0 gap
(the element shows `marginBottom:140px` yet `page_height == card.bottom`). The
reference often does exactly this: bg on an inner div, padding-bottom on its parent.

## Step 0 — look up control keys in the bundled schema catalog (do NOT guess)
Before writing `_elementor_data` / block attributes, get the canonical control key,
selector, and options from the project's editor-schema (spec 012). Query the in-instance
ability via `wp_eval_live` (or REST `/wp-json/sandbox/mcp` tool `sandbox/editor-schema`):

```php
$s = sandbox_editor_schema(['builder'=>'elementor','name'=>'heading']);
// $s['groups'] => { content, style, common };  $s['controls'] => { id: def }
// each def has type, label, default, section, tab, selectors, options
```
Find the right key by scanning `groups` (content/style/common) or by keyword:
`array_filter(array_keys($s['controls']), fn($k)=>str_contains($k,'font_size'))`.
Responsive variants append `_tablet` / `_mobile` to the desktop key
(e.g. `typography_font_size`, `typography_font_size_tablet`). NB: column/section
**structural** settings (`_inline_size`, `_inline_size_tablet`, `padding`, `_column_size`)
are element settings, NOT widget controls — they won't appear in a widget's schema.
This matters most for EA/Pro widgets and Gutenberg blocks where keys aren't obvious;
guessing happens to work for core widgets but is the wrong process.

## Workflow
1. **Extract the reference spec** (the ground truth) at a fixed viewport via Playwright
   MCP `browser_navigate` + `browser_evaluate`. Walk every section; capture per element:
   container width, padding, margin, gap, font family/size/weight/line-height/color/
   letter-spacing/align, background, border-radius, image w/h/radius/`filter`, button
   bg/color/padding/radius/font, and `:hover` rules. Save to a file (`tmp/<name>-spec.md`).
2. **Build to those exact numbers** — prefer native page-builder controls; fall back to
   custom CSS only for things the builder can't express (see "What needs CSS").
3. **Re-extract from the built page with the SAME probe and diff** property-by-property.
   Fix until empty.
4. **Vertical-rhythm check (catch section-top drift)** — see below. The per-property
   diff can be clean while the page still looks "off" because sections are the wrong
   HEIGHT, pushing everything below out of alignment.
5. **Repeat at 768px and 480px** (responsive) and **test hover** (e.g. grayscale→color).

## Vertical-rhythm check — section tops + SEGMENT heights
A pixel diff of two builds shows "doubled/ghosted" text that grows toward the bottom —
that's cumulative vertical drift, not per-element error. To find which section is the
wrong height, measure each landmark's absolute top on BOTH pages, then compare the
**segment heights** (gap to the next landmark), NOT the absolute tops:

```js
() => {                              // run on reference AND build (load images first!)
  window.scrollTo(0,0);
  const top = el => el ? Math.round(el.getBoundingClientRect().top + scrollY) : null;
  const H = t => [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].find(e=>e.textContent.trim().startsWith(t));
  const P = t => [...document.querySelectorAll('p')].find(e=>e.textContent.trim().startsWith(t));
  const L = { /* one landmark per section, by heading/text */ };
  // e.g. hero:top(H('Where Fun')), mission:top(P('Our mission')), ... , footer:top(H('Ready'))
  return { ...L, page_height: document.body.scrollHeight };
}
```
Then for each consecutive pair compute `segment = next.top - this.top` on each page and
diff the segments:
- **Absolute-top Δ** = cumulative drift (only tells you it's drifting, not where).
- **Segment-height Δ** = the culprit. The section whose segment differs most is the one
  built at the wrong height. (Real case: footer −300px, gallery −179px [image 430 vs
  shorter], mission −152px [paragraph spacing] → net −441px shorter → everything below
  the first offender ghosts in the pixel diff.)

**Fix — diagnose the cause first (see RULE 0); do NOT just add padding.** A height
delta means the offender is structurally different. Inspect the offender's DOM on both
pages and find which it is: a different **layout** (inline vs stacked, rows vs columns,
a divider present), an **image height** (portrait 318×430 vs square), real **content**
(more elements, longer text), or **paragraph spacing/line-height** that the reference
actually uses. Replicate THAT. Only adjust padding when the reference genuinely uses
padding there — never to manufacture height the reference fills with structure. If the
cause is environmental and unfixable (e.g. font-metric line-count differences), say so
and leave it; do not absorb it with blank space elsewhere. Re-measure segments until
each is within ±a few px **and** the layouts match element-for-element.

### Section-tops aligned ≠ elements aligned — do a GAP-BY-GAP pass
Aligning one landmark per section (segment heights) gets section *boundaries* within
±a few px, but **every element BETWEEN landmarks can still be off**, because the
internal gaps are **interconnected and self-cancelling**: if one gap is too big and the
next too small, they cancel at the section boundary while leaving the elements inside
mispositioned. Symptom: the pixel diff shows *every* line of text doubled by a small
offset even though section tops match, and a per-element `dTop` diff oscillates
(e.g. team-row-2 +58 while the next section is −6). This is NOT fonts/AA — verify by
checking `dLeft` is ~0 (horizontal fine) while `dTop` varies per element.

To actually align elements (target ±3–5px; cross-engine ±0 is unrealistic):
1. **Per-element position diff** — dump `{top,left}` for every `h*/p/button` on both
   pages (force-load images first), key by text prefix, diff `dTop`/`dLeft`.
2. For each off element, measure the **specific gap that precedes it** on BOTH pages —
   the gap to the previous element (`thisEl.top - prevEl.bottom`), or its own
   `margin-top` — not the section padding.
3. Set ours to the demo's measured gap (Elementor: the element's `_margin`, or the
   column/inner spacing). **Remember gaps cascade**: shrinking gap N shifts everything
   after N, so re-measure the whole column after each change and expect to also adjust
   the *next* gap to keep the following section's top still aligned.
4. Iterate top→bottom until per-element `dTop` is within tolerance everywhere.
Budget ~12–15 gaps for a full page; it's tedious but finite. Don't claim "aligned" off
section-tops alone — prove it with the per-element diff.

**When a gap WON'T close with spacing — it's structural, stop tuning and restructure.**
Cascade caveat: apply gap corrections **top→down, re-measuring after each batch** —
upstream additions compound into every section below (a batch of +7/+8/+17 upstream
plus a local +44 made a section overshoot by 60+). Compute fresh deltas each pass.
Hard limits found in practice (these need a Container/flexbox rebuild, not padding):
- **Negative section `_margin` is clamped** in Elementor — you cannot pull a section UP
  past the previous one with a negative top margin; it silently does nothing.
- **A multi-row grid built as SEPARATE sections** (e.g. two team rows = two sections)
  has an inter-row gap that section padding can't shrink to match a reference's single
  grid `row-gap` — the row-2 elements stay offset. Build it as ONE section/container with
  a real `row-gap`.
- **Classic-column card width** (25% − gutter) can't hit an arbitrary card width
  (e.g. 306 vs the demo's 312); the 6px shortfall makes a heading wrap an extra line,
  which shifts that card's inner elements (saw +31 on one icon-box description).
So: gap-tuning gets section-tops + single-column flows to ±5; the last residual cluster
is usually one of these structural mismatches.

**BUT — don't jump to a full Container rebuild; an Elementor classic row is ALREADY a
flex container (`.elementor-container`), so CSS overrides fix most "structural" cases:**
- **Exact card width + gap (icon-box 312 not 306):** force the columns —
  `.<row> > .elementor-container{justify-content:space-between} .<row> > .elementor-container
  > .elementor-column{flex:0 0 312px;max-width:312px;width:312px;padding:0}` then put the
  card bg/radius/padding on `.elementor-widget-wrap`. 4×312 in a 1320 box → `space-between`
  yields exactly 24px gaps, 0 outer inset. No rebuild.
- **Clamped negative `_margin`:** Elementor's `_margin` control clamps negatives, but a
  RAW CSS rule in a custom-CSS HTML widget does not — `.<section>{margin-top:-30px}` pulls
  it up; add the same amount to that section's bottom padding to keep the NEXT section put.
- **Two-section grid row gap:** same trick — raw negative `margin-top` on the 2nd-row
  section + matching bottom padding closes the inter-row gap without merging sections.
- **Internal alignment (nav/footer):** measure positions RELATIVE to the section/card
  (`el.left - card.left`, `el.top - cardTop`), not absolute — catches wordmark/hours/menu
  offsets. An image rendering narrower than its set width (681 vs 713) is **column-width
  constrained** — widen the column (`_inline_size`) to fit the natural/target width.
Reserve an actual Container rebuild for when CSS truly can't express it.

## Extractor (paste into browser_evaluate; save result with the `filename` param)
Per-section walker — identifies top-level bands and dumps every heading/text/image/
button/colored-panel with computed styles. Match sections by landmark heading text so
the SAME function runs on both the reference (e.g. Essential Blocks) and the build
(e.g. Elementor):

```js
() => {
  const r2=n=>Math.round(n), cs=el=>getComputedStyle(el), box=el=>el.getBoundingClientRect();
  const hero=[...document.querySelectorAll('h1,h2')].find(e=>/<FIRST HEADING>/.test(e.textContent));
  const foot=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].find(e=>/<LAST HEADING>/.test(e.textContent));
  const anc=el=>{const a=[];while(el){a.push(el);el=el.parentElement;}return a;};
  const fa=new Set(anc(foot)); const root=anc(hero).find(e=>fa.has(e));
  const secs=[...root.children].filter(c=>box(c).height>20);
  const sum=(sec)=>({
    bg:cs(sec).backgroundColor, padding:cs(sec).padding, width:r2(box(sec).width),
    headings:[...sec.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h=>({t:h.textContent.trim().slice(0,30),tag:h.tagName,size:cs(h).fontSize,weight:cs(h).fontWeight,color:cs(h).color,lh:cs(h).lineHeight,ls:cs(h).letterSpacing,align:cs(h).textAlign})),
    texts:[...sec.querySelectorAll('p')].slice(0,2).map(p=>({size:cs(p).fontSize,color:cs(p).color,lh:cs(p).lineHeight})),
    imgs:[...sec.querySelectorAll('img')].map(i=>({w:r2(box(i).width),h:r2(box(i).height),radius:cs(i).borderRadius,filter:cs(i).filter,left:r2(box(i).left)})),
    btns:[...sec.querySelectorAll('a,button')].filter(a=>box(a).width>40&&cs(a).backgroundColor!=='rgba(0, 0, 0, 0)').map(a=>({t:a.textContent.trim(),bg:cs(a).backgroundColor,padding:cs(a).padding,radius:cs(a).borderRadius,size:cs(a).fontSize})),
    panels:[...sec.querySelectorAll('*')].filter(e=>{const b=cs(e).backgroundColor;const bb=box(e);return b!=='rgba(0, 0, 0, 0)'&&b!=='rgb(255, 255, 255)'&&bb.width>100&&bb.height>40;}).slice(0,5).map(e=>({bg:cs(e).backgroundColor,w:r2(box(e).width),padding:cs(e).padding,radius:cs(e).borderRadius})),
  });
  // image gaps within a section: imgs[i].left - (imgs[i-1].left + imgs[i-1].w)
  return {content_width:r2(box(hero).width), vw:innerWidth, sections:secs.map(sum)};
}
```
For overflow / "scrollbar in the middle, content left-aligned": check
`document.documentElement.scrollWidth - clientWidth`. Headless uses overlay scrollbars
so it WON'T reproduce a `100vw`-vs-scrollbar bug — scan for `100vw`/stretched sections
and add `html,body{overflow-x:clip}` defensively.

## Critical gotchas (learned the hard way)
- **Decode race ≠ missing image.** Full-page headless screenshots often render a blank
  band for large remote images (e.g. 100KB webp) even though they're fine. Verify image
  render via DOM (`img.complete`, `naturalWidth`, `getBoundingClientRect`), not the
  screenshot. Forcing eager loading does NOT fix it (it's decode timing). See memory
  note `headless-screenshot-image-decode-race`.
- **Match viewport width** between reference and build before comparing geometry.
- **Playwright MCP `browser_take_screenshot` writes to cwd (repo root)** — pass a
  `tmp/...` filename.
- **NEVER `await` an unbounded `img.decode()`.** Calling it on a `loading="lazy"`
  off-screen image (no `currentSrc` yet) returns a promise that never settles, so
  `Promise.all(images.map(i=>i.decode()))` hangs the `evaluate` forever (a real
  ~40-min deadlock). See `headless-screenshot-image-decode-race`.

## Two complementary regression tools — pick by what you're comparing
1. **Computed-style fingerprint (THIS skill)** — for *cross-implementation* parity
   (e.g. Essential-Blocks demo vs an Elementor rebuild). Pixel-diff is WRONG here:
   different DOM/fonts/antialiasing/heights → ~all-red false positives. Compare
   *semantics* (sizes, gaps, colors, radii) instead.
2. **Pixel-diff (pixelmatch / odiff / Playwright `toHaveScreenshot`)** — for guarding
   the *same* page against unintended visual drift across edits. Requires a
   **deterministic** capture, which on image-heavy pages means force-loading lazy
   images + a bounded wait:
   ```js
   async () => {                       // deterministic full-page pre-capture
     document.querySelectorAll('img[loading="lazy"]').forEach(i=>{i.loading='eager';i.src=i.src;});
     await new Promise(r=>setTimeout(r,1500));
     const decodes=[...document.images].map(i=>i.decode().catch(()=>{}));
     await Promise.race([Promise.all(decodes), new Promise(r=>setTimeout(r,8000))]); // hard cap
   }
   ```
   Then `npx pixelmatch baseline.png current.png diff.png 0.1` (or a pngjs+pixelmatch
   node script) — same dimensions required, so keep viewport + full-load consistent.

## A local fix can cause a global cascade — measure BELOW before you "fix" a drift
The most expensive trap of the whole pass. A single element reads N px too low (e.g.
team **role** sits +11 below the demo). The reflex is to pull it up with a negative
`_margin`. DON'T — in normal document flow, **shrinking any element shrinks its block,
which shifts EVERYTHING below it by the same amount.** Pulling 8 role widgets up 7px
each (2 grid rows) stole ~14px of page height and dragged the next 6 sections
(Designed-for-Safety, the 4-col, the footer, copyright) from ~aligned (±2px) to
**-15px**. Net result: traded **1** outlier (+11) for **10** (-15). Body median went
**2px → 6px**. The fix made it worse.

Decision rule before touching any single-element drift:
1. **Measure the elements directly below it first.** If they are already aligned to the
   demo (±a few px), the demo's below-content position is ground truth — you must NOT
   move it. A local margin change that moves the block edge is then *forbidden*; accept
   the in-block drift as a residual.
2. Only "fix" a drift by changing block height when the content below is ALSO off by the
   same sign/amount (the whole region is shifted — then it's a real spacing bug).
3. To re-seat an element *inside* a block without moving the block edge, compensate:
   move it up AND add equal space elsewhere in the same block (or absorb into internal
   padding that leaves the outer height unchanged). A bare negative margin never does this.
4. The verdict signature: after the edit, re-run the gap pass top-to-bottom. A *cluster
   of new same-signed outliers below the edit point* ALWAYS means "you changed a height,"
   never "you fixed an element." Revert immediately.

## Header/nav is a known approximation — exclude it from the body-fidelity metric
The reference's nav and the rebuild's nav are usually different DOM (sticky vs static,
different height). Matching element-text across them yields huge bogus deltas (here:
nav links -150 to -322px because the demo measured them lower in a taller header). These
are NOT body drift. Keep a `nav` key-set and report **body-only** median/mean separately
from the raw number, and state the header approximation explicitly in the final report.

## Elementor build notes (what needs CSS vs native)
Native controls (bake into `_elementor_data`): typography, colors, button styles,
image border-radius (incl. per-corner like `20px 0`), column bg/padding. Genuinely
NOT native in classic sections (use a small custom-CSS HTML widget, keyed by
`.elementor-element-<id>`): per-card gap WITH a per-card background, image
`filter:grayscale` + `:hover`, containing a full-bleed section bg to a centered rounded
card. A fully-native build of those needs an Elementor **Container (flexbox)** rebuild.
Inject Elementor page data + meta via the `elementor-page-data-injection-recipe` memory note.
