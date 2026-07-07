# Fetching a Templately template's AUTHORED source JSON (both engines)

Cross-plugin runtime finding, used by the **design-fidelity-diff** skill. When the reference
design is a Templately template, the exact authored JSON — Elementor `_elementor_data` AND
Gutenberg block markup — is downloadable via the Templately plugin's GraphQL API. That is a
richer ground truth than reverse-engineering the rendered page (exact widget types, authored
control values, exact copy, section tree, and the cross-engine widget map). Build from it; still
`specextract` the live preview for the geometric DesignSpec you gate against.

## Endpoint & auth
- Everything is a GraphQL POST to **`https://app.templately.com/api/plugin`** (prod).
  Body: `{"query":"<graphql>"}`. Headers: `Content-Type: application/json`,
  `x-templately-url: https://<site>/`, `x-templately-version: 3.5.0`.
- **Catalog browse (`packs`) is PUBLIC** — no api_key, url gate not enforced.
- **Content download (`itemContent`) is gated**: needs a valid api_key AND the calling
  `x-templately-url` to be a site CONNECTED to that key's account. Pro items need an account that
  OWNS the template.
- Keys live in env: `TEMPLATELY_API_KEY` (prod, a `lifetime-five-hundred-site` Pro account that
  owns the exclusive packs), `TEMPLATELY_API_KEY_FREE` (prod, free plan), and `*_DEV` variants for
  the **dev** server (`app.templately.dev`). **A prod key is "Invalid API key" on the dev server
  and vice-versa** — match key to server.
- **Gotcha:** the templately plugin running inside a dev sandbox instance defaults its `Http` to
  the DEV server, so calling `itemContent` via `wp_eval_live` with the prod key returns
  "Unauthorized request detected." Drive the download with `curl` against prod explicitly (or force
  the plugin's prod mode). Never print the key — expand `$TEMPLATELY_API_KEY` in-shell.

## The 4 steps (worked example: FlexiGency home/landing page)
```
# 1. pack id per engine (public)                              EL and GB are SEPARATE packs
{packs(search:"flexigency", platform:"elementor"){data{id name slug live_url}}}   # -> 569
{packs(search:"flexigency", platform:"gutenberg"){data{id name slug live_url}}}   # -> 572
# 2. list a pack's pages -> the item id you want
{packs(id:569){data{items{id name type slug live_url}}}}   # 6136 = FlexiGency Landing Page (EL)
{packs(id:572){data{items{id name type slug}}}}            # 6190 = Flexigency Landing Page GB
# 3. connect a site to the key ONCE (registers the site; idempotent for an already-connected url)
mutation{connectWithApiKey(api_key:"$KEY", site_url:"https://<connected>.tst/", ip:"127.0.0.1"){status message user{plan}}}
# 4. download the authored JSON (send the SAME connected url as x-templately-url)
{itemContent(api_key:"$KEY", id:6136){status message data}}
```
- `itemContent.data` is a JSON STRING. EL → `{content:<_elementor_data array>, page_settings,
  version, title, type, template_type}`. GB → `{content:<block-markup string>, __file, title,
  syncStatus, type, template_type}`.
- Other useful queries in the plugin: `myCloudInsert(api_key, file_id, file_type)` (a user's saved
  cloud item), `myItems` (the account's cloud), `v2/import/pack/<id>` REST (the whole pack as a ZIP,
  `Authorization: Bearer <key>`, also site-gated).

## Cross-engine widget map (verified 1:1 on FlexiGency; EAAL ⇄ Essential Blocks)
`container`⇄`row|column|wrapper` · `heading`/`text-editor`⇄`advanced-heading` ·
`image`⇄`advanced-image` · `eael-info-box`⇄`infobox` · `button`⇄`button` ·
`eael-testimonial`⇄`testimonial` · `eael-counter`/`counter`⇄`number-counter` ·
`icon-list`⇄`feature-list` · `image-gallery`⇄`image-gallery` · `form`⇄`form`(+`form-email-field`) ·
`eael-post-carousel`⇄`post-carousel`.

Fixtures committed: `tools/dfdiff/examples/flexigency-{el,gb}-source.json` +
`flexigency-inventory.json` (per-section widget counts = the completeness gate). More pack items for
re-fetch: EL home 6136 / GB home 6190; EL Service 6137 / GB Service 6191 (same 4-section page each).

## `itemContent.content` is NOT self-contained (proven on the Service page)
The downloaded `content` references the Elementor **Global Kit** (`__globals__` colour IDs like
`globals/colors?id=c1f2ab8`), named fonts, and custom CSS — none of which travel with it. Inject
`content` alone (raw `_elementor_data`) and the page renders with the WRONG palette (kit-default
light-blue/green headings instead of near-black), fonts fall back to **Roboto**, and it inherits the
kit's default **content-width 1240** (design was 1280) — box geometry matches, identity is gone.
Templately's real import runs the Customizer (kit/globals) + Dependencies (fonts) + CustomCSS runners
alongside content. So building from source needs: import/set the kit global colours + typography,
enqueue the named webfonts, apply custom CSS — then gate APPEARANCE, not just boxes.

Also: **EL and GB authored versions diverge** (width 1240 vs 1280, some copy — pricing CTA "Get
Started" vs "Choose Plan" — "Popular" as heading vs text, ±20–63px section heights). Gate each build
against its own engine's preview, never cross-engine.

Wholesale-inject fixup: sideload `demo.assets.templately.com` images to local (they time out
in-container; decode the JSON array to rewrite URLs, don't regex the `\/`-escaped raw string), then
`delete_post_meta(id,'_elementor_css')` + `\Elementor\Core\Files\CSS\Post::create(id)->update()`.

## GB->EL hand-conversion, worked (Service page)
Built the GB Service page in Elementor from the GB source (content + the GB render geometry), native
widgets via the widget map -> `flexigency-service-gb2el` (page 55 in the -figma instance). Result:
leaf-widget inventory IDENTICAL to the authored EL (9 image / 7 info-box / 5 heading / 3 pricing / 1
breadcrumbs / text-editor / form) — the map-driven conversion is sound. Three conversion gotchas
(now in DESIGNSPEC.md): (a) RESOLVE global colours (`__globals__` / `var(--eb-global-*)`) to explicit
hex from the render — carrying them forward breaks colours without the kit; 0-globals build rendered
correct, 31-globals inject went light-blue. (b) block->widget drops DECOR/background layers (the CTA
`flexigency-subscripton-cta-obj-img-01.png` is a container `background_image`, not a content block) —
walk wrapper backgrounds too. (c) `flex_align_items:center` on a column shrinks children to
content-width — center text via the heading's own `align`, keep the container at stretch.

## BackstopJS caught what specdiff/specgate did not: per-CARD background parity
`sb specgate` on `flexigency-service-gb2el` was clean on section-level background parity (gradient
hero, white body sections all matched) — but `sb vrdiff` against the live GB reference showed 33%
mismatch, with every one of the 7 info-box cards, all 3 pricing cards, and both pill badges rendering
solid magenta in the overlay. Cause: DesignSpec's `bgOwner` is captured per TOP-LEVEL SECTION only;
none of the reference's REPEATED-ITEM backgrounds are section-level, so nothing failed numerically.
Colours sampled from the reference screenshot (`PIL.Image.getpixel`, no MCP tool for this — installed
Pillow in a venv since the system Python is externally-managed): info-box cards alternate
`#F5F1FF`(lavender)/`#FEEBFF`(pink)/`#FFF6EB`(cream)/`#F2FBEE`(mint)/`#EDF7FA`(lightblue) in a fixed
per-card sequence; the 3 pricing cards sit in an outer `#F4F4FF` panel; "Popular"/"Solutions" are
rotated pill badges on `#CB8FF3` (violet); the CTA panel background is `#091439` (navy) with one
highlighted word in `#C2F250` (lime) inside the headline. **Lesson: always run the visual pass
(`sb vrdiff`) on a design with repeated cards/badges even after a green `specgate`** — this class of
defect is structurally invisible to section-scoped geometry/background gates. See SKILL.md gate 6b.

**Fix validated by re-running `sb vrdiff`:** added `background_color`+`padding`+`border_radius` to
each card's own OUTER container (the one wrapping text+image, not the text-only inner one — the
reference's pastel panel covers the whole card), an outer `#F4F4FF` wrapper container around the 3
pricing cards, a `.gbel-badge` custom-CSS pill (violet bg, italic, `rotate(-4deg)`) for "Popular"/
"Solutions", and a navy `background_color` + lime-highlighted `<span>` in the heading `title` HTML
for the CTA. Mismatch dropped **33.33% → 24.90%**; every card/pricing/CTA panel now shows as a
CLEAN OUTLINE in the diff overlay (not a solid fill) — background parity is fixed. Remaining 24.90%
is the known residual: missing nav+footer (template-level chrome, out of this page-content build's
scope) and cumulative text-doubling from sections still ~715px short in total (a Phase-4
section-height fix-by-cause pass, not a background defect).
**Pill-badge gotcha:** a `heading` widget's own tag is block-level, so a card's default cross-axis
`stretch` in a flex container makes a `display:inline-block` CSS pill still render full-width (a
stretched bar, not a snug pill). Fix with the native flex-child control `_flex_align_self:
"flex-start"` on the widget (shrinks the wrapper) PLUS `width:fit-content!important` in the custom
CSS (shrinks the tag itself) — either alone was insufficient.

## Fixing the height drift (Service page): 4/4 sections converged to ±12px
Per-section height table before → after this pass: Services We Provide +10→+10 (untouched, already
fine), Brand Strategy Development +72→+12, Flexible Pricing Plan −56→−2, Solutions/CTA −153→+6.
Three distinct root causes, each verified via `sb specdiff` re-runs, not guessed:

1. **Elementor's native `form` widget is Pro-only and renders COMPLETELY EMPTY when Pro isn't
   active — silently, no error, `<div class="elementor-widget-container"></div>`.** This alone
   accounted for most of the CTA section's −153px deficit (a `getBoundingClientRect` on the form
   node showed `height:0`). None of EAAL's `eael-*-form` widgets help either — they're wrappers
   around Contact Form 7 / Fluent Forms / Gravity Forms / WPForms, all ABSENT from this instance,
   so they'd render empty too. Fix: when no form plugin is installed, replicate the VISUAL only
   with native non-form widgets — a `container` (row, `background_color`+`border`+`border_radius`
   matching the reference input chrome) holding a `heading` (placeholder-styled text) and an
   `icon` widget (arrow), not a functional form. This is a legitimate native-widget substitute, not
   an HTML-widget violation — check what form-handling plugins are actually active before assuming
   the native/Pro form widget will render.
2. **A "same-height featured card with a badge floating above" (the classic pricing-table
   "Popular" pattern) is a genuine overlap case for `_position:absolute`, not a stacking layout.**
   The reference's Standard/Premium/Enterprise "Choose Plan" buttons sit at the IDENTICAL `top`
   (2727 in all three) — the Popular badge + decorative image are positioned ABOVE/overlapping the
   Premium card, outside normal flow. A naive column stack (image, then badge, then pricing table)
   pushes the Premium column ~195px lower than its siblings and un-aligns every button row. Fix:
   put the decor image + badge as `_position:absolute` children (with negative `_offset_y`) of a
   `position:"relative"` featured container, alongside the pricing-table widget in NORMAL flow —
   this is exactly the skill's documented overlap case, just easy to miss when translating a
   design that visually reads as "stacked."
3. **A widget can have NO exposed control for its own internal spacing** — `eael-pricing-table`
   exposes ~410 controls but zero for feature-list item padding/gap; its default rendering is
   markedly more compact than the reference's premium/airy card (title→button span 354px vs
   reference's 587px). When Phase-0 control discovery turns up nothing for the specific internal
   rhythm you need, the pragmatic native lever is the widget's own COMMON `_padding` (top/bottom) —
   it won't reproduce the exact internal proportions (title/price/feature gaps stay compact,
   whitespace lands at the card's outer edges instead) but converges overall card height
   correctly; iterate the padding value against `sb specdiff`'s section-height number until it
   converges (verified: 0→50→120px `_padding` took the section from −242 to −2).

**CORRECTED (see extract-web.js@6 below) — a "content-width delta is a measurement artifact"
claim was WRONG and should never have been accepted without finding the actual root cause.** An
earlier pass here hand-waved the −40px content-width delta as "a vertical scrollbar eating pixels"
because `getComputedStyle(innerEl).maxWidth` read `1280px` and that felt like enough proof. It
wasn't: `max-width` is a CSS ceiling, not the element's actual rendered width, and nobody checked
`getBoundingClientRect().width` on the SAME element. When directly challenged to "make sure
container width match" instead of accepting the hand-wave, the real cause fell out immediately:
`extract-web.js`'s `contentMaxWidth` used a class-name selector
(`.e-con-inner,.elementor-container,[class*="container"]`) that matches ONLY Elementor markup —
on the Gutenberg/EB reference nothing matched, so it silently fell back to measuring `sections[0]`
itself (the full-width OUTER wrapper, 1280px) instead of the reference's true, narrower content
wrapper (`.eb-wrapper-inner-blocks`, genuinely 1240px — verified via `getBoundingClientRect`).
**Both engines' real content width was already 1240px this whole time** — the "defect" was a
one-sided extraction bug, not a rendering difference. Fixed properly in `extract-web.js@6`: a
GEOMETRIC content-wrapper finder (descend single-child wrapper chains from the section root until
width stops matching the section) that needs no class-name knowledge of either engine, used for
both `page.contentMaxWidth` and each section's own `contentWidth`. **Lesson: "the computed style
looks right" is not the same claim as "the rendered width matches" — check
`getBoundingClientRect().width` on BOTH sides with the SAME method before calling ANY delta a
measurement artifact, and never accept your own earlier explanation without re-deriving it when
pushed on.**

**Section-height convergence does not by itself move the overall `sb vrdiff` mismatch % much, and
that's expected — check the RIGHT metric.** Fixing all 4 sections to ±12px left the overall pixel
mismatch roughly flat (24.90% → 26.13%, even ticking up slightly) because out-of-scope/inherent
diffs (missing nav+footer chrome, the sampled 3D-render images differing pixel-for-pixel from the
reference's own renders) dominate the raw percentage regardless of internal alignment quality. The
real proof of a fixed height-drift is the qualitative overlay: doubled/ghosted text stays LOCAL to
each section (tens of px) instead of fanning wider and wider down the page (hundreds of px,
cumulative) — compare the per-section height TABLE before/after, not the single mismatch %, when
judging whether a height-drift fix worked.

## Appearance-defect pass: 84 findings sat unread behind `| head -20` (process gap, not a tool gap)
`sb specdiff` was computing button color/font-family/text-transform/gradient defects the entire
time — I just never read past the truncated CLI output while chasing height numbers (findings rank
content-width/height/position FIRST, appearance LAST). Full `--json` read surfaced 104 appearance
findings. Root content cause: typography was set on the hand-built section-title headings but never
on sub-elements INSIDE EAAL widgets (info-box button/text, pricing-table title/button), which ship
a hardcoded default font (Manrope) that doesn't inherit page-level type. Fixed via:
- **Two widget control-key traps, both requiring PRIMED `get_controls()`** (not just Elementor-core
  widgets — a 3rd-party widget's entire STYLE tab can be missing unprimed: `eael-pricing-table` went
  410→760 keys primed, unlocking ALL radius/typography/button-color controls that read as
  nonexistent before). (1) `eael-info-box`'s "normal state" CONTENT typography key has an unexpected
  `_hover_` segment (`eael_infobox_content_typography_hover_font_family`, not `..._typography_font_family`) —
  used the "obvious" key, it silently no-op'd. (2) `eael_pricing_table_btn` (not `_btn_text`) is the
  actual button-text-content key.
- **A confirmed-correct, correctly-serialized native control can still lose to a widget's bundled
  CSS.** `eael_pricing_table_border_radius`/`_container_padding`/`_background_background` were all
  verified via primed introspection and correctly present in `_elementor_data`, yet computed style
  kept showing the plugin's own default (4px radius, transparent bg) — Elementor's generated CSS
  never emitted a competing rule. Fix: `_css_classes` + `wp_update_custom_css_post()` (`!important`,
  targeting the widget's own rendered class) — the skill's documented fallback, reached after two
  failed re-verification rounds rather than continuing to guess keys.
- **Elementor's native lazy-load strips `background-image` (wildcard `*`, `!important`) on any
  below-the-fold container until `.e-lazyloaded` lands** — this made a correctly-authored gradient
  override look broken in ad-hoc `getComputedStyle` checks (showed `none`) until dwell-scrolling
  first, exactly like the skill's existing lazy-`<img>` corollary but for CSS backgrounds too.
  **Real regression caught this way:** the fallback I first wrote paired the gradient override with
  `background-color:transparent`, which made pricing-button TEXT INVISIBLE (white-on-white card)
  in the pre-scroll state — a real visitor risk, not just a measurement artifact. Fixed by giving
  the override an explicit SOLID `background-color` matching the gradient's dark stop (unaffected by
  the lazy-load rule, which only ever nulls `background-image`).
- **The `flex_align_items:center` shrink-to-content bug (already documented) was the single
  biggest position defect on the page** — fixing the two affected containers (hero + pricing-title
  wrappers) from `center`→`stretch` dropped page-wide `dLeft` max from 363px to 139px in one change,
  bigger than every other position defect combined. Total defects 263→197 across this pass.

## Vision pass caught a WRONG-CONTENT bug that specdiff structurally could not see — fixed the tool
User sent screenshots and called the pricing section "totally broken": prices showed a fake
sale pattern (`$99 $89`) instead of the reference's plain `$49`, and the feature list was 5 items
of made-up copy ("Unlimited calls", "Free hosting"...) instead of the reference's real 6 items
("Customization Options", "Responsive Design"...). Root cause: I built `pricing-content.json` from
`service-el-src.json` (the AUTHORED **EL** DEMO content) instead of the GB source — on a **GB→EL
conversion task**, the content must come from the GB block markup, not EL's own placeholder data
(same class of mistake as the earlier-documented "EL and GB authored versions diverge," but this
time I used the wrong one as my source instead of just gating against the wrong one).
**Why `sb specdiff` reported clean the whole time:** the price is a bare `<div class=
"eael-pricing-tag">` and each feature an `<li>` — both OUTSIDE `extract-web.js`'s v1-v4 element
scan (`h1-h6,p,a,button,img,input`). The content wasn't mismatched-and-ignored, it was **invisible
to the extractor**, so there was nothing for `elem_key()` to compare. Fixed the TOOL, not just the
content: `extract-web.js@5` adds `li` + a leaf-node `div`/`span` rule (zero child elements + own
text, excluding descendants of an already-tag-matched ancestor to avoid double-counting a button's
inner label span). Verified: element count on this page went 43→71 (ref); re-extracting after the
fix now surfaces real min-max text diffs on prices/features that v1-v4 would have silently passed.
See SKILL.md gate 6c + DESIGNSPEC.md "Diff contract" for the full writeup.

Two more confirmed-then-fixed bugs from the same vision pass, both process gotchas rather than
new code defects:
- **A decorative accent image rendered as a soft, wrong-looking blur** — not a broken asset (the
  downloaded PNG, opened directly, was a normal soft-gradient graphic) but a **stale re-injection**:
  my Python generator always emits the REMOTE `demo.assets.templately.com` URL for every image
  (it has no memory of a prior sideload), so a later "just fixing CSS" re-injection that skipped
  the sideload/rewrite step silently reverted that image back to the slow CDN, and the screenshot's
  decode-race then caught it mid-fetch. **Rule going forward: every re-injection of a
  regenerated JSON re-runs the full sideload+rewrite, not just the first one.**
- **The same decorative image also visually "floated" above its card as a stray patch** — the
  absolute-positioned decor had too large a negative `offset_y` (-110px), escaping the pricing
  panel's own rounded-corner visual boundary into the white space above it. Fixed by both reducing
  the offset AND right-sizing the image (150→110px) to read as a small contained corner-accent
  behind the "Popular" badge, matching the reference's actual (much subtler) treatment.
- **A vision-reported "text/image overlap" inside a card turned out to be a `vrdiff`/BackstopJS
  capture artifact, not a real bug** — direct `getBoundingClientRect` measurement after a proper
  dwell-scroll + `decode()` wait showed clean, non-overlapping boxes (image 869–1064px, heading
  1134–1192px, paragraph 1218–1294px). `sb vrdiff`'s default settle delay isn't always enough for
  a page with many/slow images; verify an apparent layout defect with a fresh DOM measurement
  before trusting a scrubber screenshot alone.
- **`eael-pricing-table`'s subtitle field is conditionally gated to `style-2`** — setting
  `eael_pricing_table_sub_title` under the default `style-1` silently no-ops (verified via the
  control's own `"condition":{"eael_pricing_table_style":["style-2"]}`). Switching to style-2
  DOES show the subtitle but also adds an unwanted icon-circle header + colored band neither
  reference has — reverted to style-1 and accepted the missing subtitle line as a smaller, bounded
  defect than the two new unwanted elements a style swap would introduce.

## Reset-and-rebuild-from-scratch caught two REAL bugs a live-instance patch loop had masked
No `@install` baseline existed for this instance, so "reset" meant: delete all test pages +
Additional CSS + uploaded assets, regenerate `_elementor_data` fresh from the current Python
generator, and rebuild on a brand-new page id. This is worth doing periodically even without a
user request — a long chain of live in-place patches on the SAME page can accumulate hidden state
(stale custom CSS rules, leftover meta) that masks whether the SCRIPT itself is actually correct
end-to-end. It found two real, independent regressions:
- **A string-replace edit silently dropped an unrelated fix that shared its line.** Removing the
  onsale/sale-price settings from `pricing()` also deleted `_padding=px(120,0,120,0)` — the
  earlier-tuned lever that converged the pricing card height to the reference — because both sat
  in the same multi-kwarg replace block. Section height silently regressed from ~±5px to −185px
  and stayed that way across several edits, because nothing re-verified the FULL settings dict
  after the replace, only that the specific unwanted keys were gone. **Rule: after any
  string-replace on a multi-setting call, re-`grep` the function for every OTHER setting you
  didn't intend to touch, don't just confirm the removed one is gone.**
- **`card_stack()`'s image/text order was backwards for all three row-2 cards, and had been since
  it was first written** — misattributed the GB source's `flexDirection` attribute (read off the
  `infobox` block's OWN attrs) as controlling the SIBLING order between the separate `advanced-image`
  block and the infobox block, when it actually only controls the infobox's OWN internal icon/text
  arrangement — irrelevant to two separate sibling blocks entirely. Real evidence should come from
  the EXTRACTED render geometry (which top comes first), not a plausible-sounding attribute name on
  an unrelated block. This produced dTop residuals of 200-290px for every row-2 element — the
  largest defect class in the whole page — and had been silently absorbed into "known height drift"
  across multiple earlier passes without being root-caused. Fixed by swapping the child order
  (infobox first/top, image second/bottom) to match the reference's actual measured order. dTop
  median dropped 46px→25px in this one fix; overall `vrdiff` mismatch hit its best number of the
  whole exercise (23.41%) after this + the pricing padding restore + the info-box button fix
  (below) — with the remaining ~601px height gap being the already-documented out-of-scope
  nav/footer chrome, not further build defects.
- **The info-box "Learn More" button had the wrong visual treatment entirely — a solid dark button
  box (`#333333` background, 5px/10px padding) where the reference renders a plain transparent
  text link** (`background:rgba(0,0,0,0)`, `padding:0`, navy text, no radius) — caught by the user
  from a cropped screenshot alone, not from any numeric gate (a "button" widget having a filled
  background is not itself flagged as wrong; the appearance gate reports colour mismatches but at
  low severity among 80+ other findings, easy to miss). Fixed via
  `eael_infobox_button_background_color="#33333300"` (EAAL's OWN default IS `#333333`, hence the
  false "it renders fine" impression until directly diffed against the reference) +
  `eael_creative_button_padding=px(0,0,0,0)`, with the same `_css_classes`+Additional-CSS fallback
  pattern as a safety net given prior widgets in this build needed it.

## Hero section driven to true convergence — a worked example of "iterate until 0, not near 0"
Pushed back on twice ("what do you mean not real" / "why not fixed") for accepting a hand-waved
explanation and a 111px position miss without root-causing either. Both turned out fixable. Full
before→after on the Service page hero ("Services We Provide"):
- **Content width: -40px → 0px (PASS).** Real cause was the extractor (see above), not the build.
- **Section height: +10px → -2px.** Root cause: `flex_gap` on the hero container (24px) was
  simply too large — the reference's actual breadcrumb-to-heading gap, measured directly
  (`heading.top - breadcrumbWidget.bottom`), is ~11-13px, not 24. Iterating the gap value against
  `specextract` re-measurements (24→11→13→26, the last two compensating for two OTHER fixes that
  each shrank the breadcrumb widget's own height) converged the heading's `top` to an EXACT match
  (230=230) and section height to -2px (an honest sub-pixel floor — internal math is self-
  consistent: 182 padding-top + 67 heading height + 30 padding-bottom accounts for the whole
  content span with nothing unaccounted for).
- **Breadcrumb "Home"/"current page" horizontal position: up to 111px off → 1px.** Two stacked
  causes: (1) the "current page" trail label was pulling the WordPress page's own (verbose, debug-
  named) title instead of a short label — renaming the page fixed the width of that segment; (2)
  the SEPARATOR icon's default spacing (`eael_separator_spacing`, default 10px) + size
  (`separator_size`, default 15px) rendered ~12px wider than the reference's, shifting the
  CENTERED trail's start point by half that (6px) — tightened to `eael_separator_spacing=4,
  separator_size=12` to close it.
- **Breadcrumb vertical text position: 6-8px off → 1-3px.** The visible `<a>`/text sits inside
  `.eael-breadcrumbs__content`, which carries an UNEXPOSED internal default `padding:5px 15px` —
  no control surfaces it, and the widget's own `breadcrumb_typography_line_height` control does
  NOT affect this (tried it first; zero effect, confirming the offset comes from the wrapper's
  padding, not the text's line-height). Fixed via a scoped CSS override
  (`.eael-breadcrumbs__content{padding-top:0!important;padding-bottom:0!important}`) — after which
  the wrapper shrank, so its down-stream contribution to section height had to be re-compensated
  in `flex_gap` again (the "local fix cascades" corollary applies even to fixing an offset, not
  just moving an element).
- **Breadcrumb typography/color: totally wrong (Manrope, washed-out gray) → exact.** Set
  `breadcrumb_typography_*` + `breadcrumb_link_color`/`breadcrumb_text_color` — the widget's
  default styling was never touched before, same class of gap as the info-box button.

**Process takeaway: every one of these had a findable, fixable root cause. None were an
irreducible cross-engine floor until proven so by exhausting the exposed controls AND a scoped
CSS override AND re-verifying the numbers moved.** "Not a real defect" and "can't be fixed
further" are conclusions to ARRIVE AT after this process, not assumptions to open with.

## Remaining sections driven the same way — key findings applicable beyond this build
Continued the same rigor into Brand Strategy Development (cards), Flexible Pricing Plan, and
Solutions/CTA. Appearance findings dropped 75→8 page-wide; Brand Strategy height −34px→−19px;
dLeft median 22.5px→17px. New, generalizable findings:

- **Measure the reference's ACTUAL card-panel rectangles via `getBoundingClientRect` on elements
  matched by background COLOR, not by reverse-engineering from text positions.** Reverse-
  engineering card width/gap from two text elements' left positions produces WRONG numbers if you
  don't already know the padding — an early estimate of "64px gap between cards" was actually
  text-to-text distance including padding on both sides; the REAL card-to-card gap (measured
  directly off the pastel background panels) was a uniform 24px everywhere. When a repeated card
  grid's exact gap/width isn't obvious from element positions alone, filter for the panel's own
  background-color and measure ITS rect directly — don't infer from children.
- **The authored EL source (when you have it, e.g. from `itemContent`) is a goldmine for exact
  padding/margin/width-percentage values that are otherwise unmeasurable from computed style**
  (Elementor's own container padding often can't be reverse-engineered to the pixel from rendered
  boxes alone, because of the "stacked nested-container default padding" gotcha polluting the
  numbers). Cross-referencing authored-EL padding against direct GB-reference panel measurements
  let several per-card paddings converge to exact values (e.g. `40px` left / `0px` right / `44px`
  top on most row1/row3 cards) that pure pixel-reverse-engineering alone would have taken many more
  iterations to find.
- **A DIFFERENT font-size between visually-similar repeated items is easy to miss without
  checking EVERY one.** Row1/row3 card titles use `fs:26`; row2 (the 3-card stacked layout) uses
  `fs:32` — a real, previously-unnoticed difference that explained a −91px heading-height residual
  once found (verified via direct `getComputedStyle` on multiple cards, not assumed uniform from
  one sample).
- **A repeated-item's own `padding`-driving-height finding may be implemented as a `margin`
  control, which does NOT count toward `getBoundingClientRect` height the way reference's CSS
  `padding` does.** Setting `eael_infobox_title_margin` moved the title's start position (margin
  affects layout/siblings) but did NOT inflate the title's OWN measured height to match a
  reference whose extra height came from real `padding` — no matching padding control existed on
  the widget at all. When a control is margin-only and the reference's own inflation is
  padding-based, accept the residual rather than force a mismatch between the two mechanisms; the
  CSS override escape hatch is the only way to truly match if the gap matters more than the risk.
- **A "wrong color" appearance finding can be a FALSE POSITIVE when a heading's actual visible
  text lives in child SPANS with their own color, not the outer heading tag.** A CTA headline
  showed `color:rgb(17,17,17)` (near-black) via `getComputedStyle` on the `<h2>` itself — but
  `document.elementFromPoint()` on the actual glyph pixels landed on a `<span class="first-title">`
  with its OWN `color:rgb(255,255,255)` (white), matching the visibly-rendered page exactly. EB's
  "advanced-heading" (and similar rich-text widgets) commonly wrap ALL visible text in per-run
  spans for word-level color/highlight control (the same pattern used for a lime-highlighted word
  elsewhere on this page) — the outer tag's own `color` is a NEVER-RENDERED fallback in that case.
  **Before trusting a heading's computed color, sample the color at the actual glyph position via
  `elementFromPoint`, not just `getComputedStyle` on the outer tag** — this is a real gap in
  `extract-web.js`'s `elemSpec()` (it always reads the outer element's own color) worth fixing if
  this pattern recurs.
- **Nested nearby container default padding (the "~10px stacks per level" gotcha) can appear at
  MULTIPLE unrelated points in a build** — not just the widely-known column/section case. Found
  and zeroed it on: the info-box/image inner wrapper columns inside each card, the row containers
  (rowA/B/C) themselves, AND a CTA text-column wrapper — each a separate, independent instance of
  the same root cause, each silently adding ~10-20px of unintended offset until explicitly zeroed.
