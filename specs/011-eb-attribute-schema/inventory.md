# Widget/Block Attribute-Schema Inventory

**Generated**: 2026-06-25 · **Instance**: `templately-fsi-rewrite` · via `sandbox/editor-schema`
**EB build**: `.org` essential-blocks with `src/controls` staged from the free checkout (full-fidelity test fixture)

## Summary

| Builder | Items | Notes |
|---|---|---|
| **Essential Blocks** (gutenberg) | **82 blocks** | 39 full · 21 partial · 22 reduced |
| **Elementor** (incl. EA) | **239 widgets** | full control schema (unchanged by spec 011) — 58 are `eael-*` (EA) |

EB fidelity tally: **full 39 / partial 21 / reduced 22**.

---

## Essential Blocks — FULL fidelity (39)

| Block | Attrs | | Block | Attrs |
|---|---|---|---|---|
| post-grid | 2456 | | infobox | 536 |
| accordion | 2378 | | parallax-slider | 511 |
| advanced-navigation | 1604 | | number-counter | 501 |
| advanced-tabs | 1554 | | breadcrumbs | 475 |
| countdown | 1321 | | openverse | 460 |
| timeline | 1242 | | advanced-image | 452 |
| team-member | 1206 | | toggle-content | 442 |
| flipbox | 1054 | | product-price | 412 |
| image-gallery | 1053 | | testimonial | 401 |
| post-carousel | 1030 | | typing-text | 366 |
| nft-gallery | 919 | | product-rating | 337 |
| image-hotspots | 876 | | lottie-animation | 338 |
| add-to-cart | 784 | | notice | 336 |
| advanced-heading | 787 | | row | 316 |
| slider | 707 | | column | 295 |
| table-of-contents | 616 | | dual-button | 143 |
| advanced-video | 560 | | accordion-item | 33 |
| call-to-action | 570 | | button | 21 |
| advanced-image | 452 | | tab | 4 |
| social-share | 473 / social 419 | | | |

## Essential Blocks — PARTIAL fidelity (21) — some generator prefixes unresolved

These return all explicitly-declared attributes (named keys present) but ≥1 generator could not be
expanded — usually because its prefix constant resolves from an import path the resolver doesn't yet
follow. Honest `level: partial` with `unresolved` naming the generators.

| Block | Attrs | unresolved | | Block | Attrs | unresolved |
|---|---|---|---|---|---|---|
| product-images | 603 | 1 | | progress-bar | 100 | 8 |
| product-details | 524 | 1 | | instagram-feed | 100 | 5 |
| taxonomy | 444 | 1 | | feature-list | 97 | 23 |
| post-meta | 402 | 1 | | popup | 86 | 18 |
| text | 347 | 1 | | google-map | 70 | 4 |
| fluent-forms | 230 | 32 | | interactive-promo | 67 | 5 |
| form | 272 | 28 | | image-comparison | 52 | 4 |
| pricing-table | 303 | 20 | | flex-container | 17 | 13 |
| woo-product-grid | 280 | 33 | | icon | 17 | 8 |
| form-text-field | 125 | 10 | | wrapper | 11 | 8 |
| | | | | shape-divider | 5 | 5 |

## Essential Blocks — REDUCED fidelity (22) — ALL `pro-*` blocks

Every reduced block is an **EB Pro** block (`essential-blocks/pro-*`). Their attribute source isn't in
the free `.org` build's `src/blocks`, and the mounted `essential-blocks-pro` build ships neither
`src/blocks/<name>/attributes.js` nor `src/controls`. They return block.json (generic) attributes only,
honestly flagged.

`pro-form-datetime-picker, pro-form-recaptcha, pro-form-multistep-wrapper, pro-form-country-field,
pro-form-phone-field, pro-advanced-search, pro-data-table, pro-timeline-slider, pro-news-ticker,
pro-woo-product-carousel, pro-pricing-column, pro-multicolumn-pricing-table, pro-fancy-chart,
pro-stacked-cards, pro-testimonial-slider, pro-off-canvas, pro-loop-builder, pro-post-template,
pro-loop-pagination, pro-mega-menu, pro-business-hours, pro-animated-wrapper` (all count 3–4).

## Elementor / Essential Addons — 239 widgets (unchanged by spec 011)

Already full fidelity via the live control registry (no source-parsing needed). 58 `eael-*` widgets.
Sample EA control counts: `eael-adv-accordion` 430, `eael-adv-tabs` 394, `eael-advanced-data-table`
326, `eael-better-payment` 293, `eael-betterdocs-category-box` 444, `eael-betterdocs-category-grid` 568.

---

## Actionable findings from the inventory

1. **All 22 reduced = EB Pro blocks.** Full/partial fidelity for Pro requires an EB Pro **source
   checkout** (with `src/blocks/<name>/attributes.js` and ideally `src/controls`) mapped as the active
   plugin, or passed via `source_root`. The mounted `essential-blocks-pro` build here is compiled, so
   Pro blocks degrade honestly to reduced. (Confirms the FR-008 limitation.)

2. **21 partial blocks share one root cause** — a generator's PREFIX constant didn't resolve, so that
   generator is skipped. The named/explicit attributes are still returned. Worth a follow-up: broaden
   constant resolution (e.g. follow re-exported / barrel `index.js` imports and constants defined
   inline rather than as `export const NAME = "..."`). Highest-impact offenders by unresolved count:
   woo-product-grid (33), fluent-forms (32), form (28), feature-list (23), pricing-table (20).

3. **39 full + 21 partial = 60/82 EB blocks return their real named attributes** today (everything
   except Pro). Pre-feature, all 82 returned only ~3 generic keys.
