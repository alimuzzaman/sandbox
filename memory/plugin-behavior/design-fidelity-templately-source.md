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
