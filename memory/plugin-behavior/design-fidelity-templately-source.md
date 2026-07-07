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

**Content-width delta can be a MEASUREMENT ARTIFACT, not a real defect — verify via computed style
before re-tuning settings.** `sb specdiff` reported content-width 1240 vs reference 1280 throughout
this build, but `getComputedStyle(innerEl).maxWidth` on the live page showed exactly `1280px` — the
`boxed_width` setting WAS correct. At the exact 1280px viewport `specextract` uses, a vertical
scrollbar eats ~40-55px of the visible content width before layout, so the extractor measures a
narrower box than the CSS actually specifies. Don't chase a content-width delta by changing
`boxed_width` again if you've already confirmed the computed `max-width` matches the reference —
you'd be "fixing" a correct setting.

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
