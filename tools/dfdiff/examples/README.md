# dfdiff examples — golden DesignSpec + Templately-source fixtures

Real reference-of-record fixtures for the design-fidelity diff/gate CLI and for a rebuild.
Two kinds live here:
- **`*-ref.json`** — a DesignSpec v1 (extract-web.js output): rendered geometry, the thing you
  gate the build against numerically.
- **`*-source.json` / `*-inventory.json`** — the AUTHORED template JSON pulled from the Templately
  API (structure, control values, exact copy, widget/block types). This is the build RECIPE and
  the completeness checklist — a richer ground truth than the rendered DOM. See the "Templately
  source" adapter in `../../.claude/skills/design-fidelity-diff/DESIGNSPEC.md`.

## flexigency — the running example (Templately pack: FlexiGency, Multipurpose Agency)

Same design shipped in BOTH engines; every fixture below is the HOME/landing page. **The two
engines render at DIFFERENT preview hosts** — pair each authored source with its own rendered
geometry when gating:
- **Gutenberg** render → `https://agency.blocks.templately.com/flexigency/` (Essential Blocks;
  `eb-parent-wrapper`) — this is what `flexigency-ref.json` was extracted from.
- **Elementor** render → `https://agency.elementor.templately.com/flexigency/` (EAAL;
  `elementor-widget`) — no `-ref` extracted yet; `specextract` it if you gate an Elementor build.

| File | What | Provenance |
|---|---|---|
| `flexigency-ref.json` | DesignSpec v1 — **Gutenberg** rendered geometry (10 sections, 212 elements, page 9562px, contentMaxWidth 1240) | `sb specextract https://agency.blocks.templately.com/flexigency/ …` (extract-web.js@4) |
| `flexigency-el-source.json` | Authored **Elementor** `_elementor_data` (container/flexbox, EAAL widgets) | Templately API `itemContent` — EL pack **569**, item **6136** |
| `flexigency-gb-source.json` | Authored **Gutenberg** block markup (Essential Blocks) — the source that renders `flexigency-ref.json` | Templately API `itemContent` — GB pack **572**, item **6190** |
| `flexigency-inventory.json` | Per-section authored widget counts + the EL↔GB widget map — the **completeness gate** checklist | derived from the two source files |

- The `-ref` extraction is stable: two independent runs diff to **0 defects** (dLeft max 1px).
- 10 sections · 212 elements · 4 fonts (Inter Tight 400/500/600, Arial) · 37 elements carry
  `text-transform:capitalize`; per-section gradients + decor captured.
- **Known limitation — section 10 ("Explore Digital Insights") is a horizontally-scrolling blog
  carousel** (`eael-post-carousel` ↔ `essential-blocks/post-carousel`); its slide offset is
  autoplay-driven, so 15 elements read negative `left` at capture time. Don't gate section 10 on
  exact `left`/`dLeft`; gate height/content/asset presence (same class as the nav).

### The EL↔GB widget map (verified 1:1 on this design)

The same design maps deterministically between engines — the cross-engine build table:

| Elementor (EAAL) | Gutenberg (Essential Blocks) |
|---|---|
| `container` | `essential-blocks/row` \| `wrapper` \| `column` |
| `heading`, `text-editor` | `essential-blocks/advanced-heading` |
| `image` | `essential-blocks/advanced-image` |
| `eael-info-box` | `essential-blocks/infobox` |
| `button` | `essential-blocks/button` |
| `eael-testimonial` | `essential-blocks/testimonial` |
| `eael-counter`, `counter` | `essential-blocks/number-counter` |
| `icon-list` | `essential-blocks/feature-list` |
| `image-gallery` | `essential-blocks/image-gallery` |
| `form` | `essential-blocks/form` (+ `form-email-field`) |
| `eael-post-carousel` | `essential-blocks/post-carousel` |

### How the source was fetched (Templately API, no browser)

Public catalog browse needs no key; the content download needs a Pro-owning account + a connected
site url. Full recipe + gotchas in `memory/plugin-behavior/design-fidelity-templately-source.md`.

```
# 1. find the pack id for each engine (public, no key)   POST https://app.templately.com/api/plugin
  {packs(search:"flexigency", platform:"elementor"){data{id name slug live_url}}}   # -> 569
  {packs(search:"flexigency", platform:"gutenberg"){data{id name slug live_url}}}   # -> 572
# 2. list the pack's pages -> the home item id
  {packs(id:569){data{items{id name type slug}}}}                                    # -> 6136 (EL home)
  {packs(id:572){data{items{id name type slug}}}}                                    # -> 6190 (GB home)
# 3. connect a site once, then download the authored JSON (Pro item, key from $TEMPLATELY_API_KEY)
  mutation{connectWithApiKey(api_key:"…", site_url:"https://<connected>.tst/", ip:"127.0.0.1"){status}}
  {itemContent(api_key:"…", id:6136){status message data}}   # data = {content, page_settings, …}
```

### Use it

Gate a rendered build of flexigency against the geometric reference:

```
sb specextract <your-build-url> --out build.json          # e.g. --login for a wp-admin preview
sb specdiff tools/dfdiff/examples/flexigency-ref.json build.json   # ranked defect report
sb specgate tools/dfdiff/examples/flexigency-ref.json build.json   # PASS/FAIL done-gate
```

Build FROM the authored source (`flexigency-el-source.json` / `-gb-source.json`) and check
completeness against `flexigency-inventory.json` — every section present and every listed widget
instance placed (mapped cross-engine). Refresh a fixture by re-running its command above and
committing the diff.
