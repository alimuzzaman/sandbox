# wp-pilot gotchas

Curated knowledge that **cannot be auto-extracted** from widget/block source. Emergent behaviors, plugin interactions, postmeta requirements, Pro-vs-Free quirks. Read before step 5 of the PLAN-FIRST workflow.

**Append-only**: every time you hit a non-obvious trap, add an entry. The cost of writing it down once is tiny compared to re-discovering it.

---

## Page template

### `_wp_page_template = elementor_canvas` is required for full-width Elementor pages

Setting `_elementor_data` postmeta alone isn't enough — the page still renders inside the theme's content container (narrow, with site header/footer). To get a true full-width Elementor canvas you must also set the page template:

```bash
./wp-sandbox wp post meta update <id> _wp_page_template elementor_canvas
```

Setting this via REST `meta:` field in `wp.apiFetch` **does not work** unless the meta key is `show_in_rest`-registered. Use wp-cli or a `wp_update_post_meta` PHP call.

Other valid values: `elementor_header_footer` (keep theme header/footer, content full-width).

---

## Image + section background image fields

### Both need `{id, url}` — id alone silently fails

Elementor's Image widget and section/column `background_image` controls expect an object with **both** keys:

```js
image: { id: 2271, url: 'http://localhost:8188/wp-content/uploads/2026/05/logo.svg' }
```

If you pass `{ id: 2271 }` only, the widget renders empty / the section shows no background. Cause: Elementor's frontend renderer reads `url` directly, not `id` — `id` is only used by the editor inspector to display the file picker.

**Practical fix in recipes**: after composing the elements tree, walk it and patch every `image` / `background_image` to fill in `url` via `wp_get_attachment_url($id)`. The `figma-to-page.js` recipe does this automatically.

---

## SVG uploads

### Need a mu-plugin permitting the mime type

WordPress core rejects SVGs by default. To enable in the sandbox:

```php
// runtime/wp/wp-content/mu-plugins/allow-svg.php
add_filter('upload_mimes', function ($m) { $m['svg'] = 'image/svg+xml'; return $m; });
add_filter('wp_check_filetype_and_ext', function ($d, $f, $name, $m) {
    if (substr($name, -4) === '.svg') { $d['ext']='svg'; $d['type']='image/svg+xml'; $d['proper_filename']=$name; }
    return $d;
}, 10, 4);
```

Also: SVG files saved with a `.png` extension by the Figma asset endpoint will fail mime detection. Rename to `.svg` before uploading.

---

## Custom fonts in Elementor

### Need both a stylesheet enqueue AND Elementor font filter registration

Just loading a font CSS file isn't enough — Elementor's font dropdown won't pick it up. Both:

```php
// 1. Enqueue the font CSS
add_action('wp_enqueue_scripts', function () {
    wp_enqueue_style('shop-co-satoshi', 'https://api.fontshare.com/v2/css?f[]=satoshi@300,400,500,700,900&display=swap', [], null);
});

// 2. Register with Elementor so the dropdown lists it AND widgets can select it
add_filter('elementor/fonts/additional_fonts', function ($fonts) {
    $fonts['Satoshi'] = 'system';
    return $fonts;
});

// 3. Same enqueue on Elementor's preview iframe
add_action('elementor/preview/enqueue_styles', function () {
    wp_enqueue_style('shop-co-satoshi', 'https://api.fontshare.com/v2/css?...', [], null);
});
```

Without step 2, the typography control's font_family setting will store your font name but Elementor won't load the right file in the editor preview.

---

## Asset paths between containers

### wpcli container can't see arbitrary host paths

The wpcli docker service has limited mounts (`runtime/wp:/var/www/html` + `runtime/seeds` + the plugin sources bind-mount). It **cannot** see `/Users/...`, `/tmp/...`, or arbitrary host directories.

To install a plugin from a host zip or import host images: copy the file into `runtime/wp/` first, then reference it as `/var/www/html/<filename>` inside the container.

```bash
cp ~/Downloads/plugin.zip /Applications/Workspace/GitHub/sandbox/runtime/wp/plugin.zip
./wp-sandbox wp plugin install /var/www/html/plugin.zip --activate
```

---

## EmbedPress Gutenberg block authoring from PHP

### Empty inner content triggers a deprecation that strips all attributes

If you author `<!-- wp:embedpress/embedpress {json} /-->` (self-closing void block) or `<!-- wp:embedpress/embedpress {json} --><!-- /wp:embedpress -->` (empty inner content), Gutenberg's catch-all deprecation 0 fires (`isEligible: () => true`) and strips every parsed attribute except `className` / `clientId` / `lock` / `metadata`.

**Fix**: include non-empty inner content matching the basic figure shape:

```html
<!-- wp:embedpress/embedpress {"url":"...","embedHTML":"<iframe>...</iframe>","editingURL":false} -->
<figure class="wp-block-embedpress-embedpress"><div class="gutenberg-block-wraper"><div class="ep-embed-content-wraper"><iframe>...</iframe></div></div></figure>
<!-- /wp:embedpress/embedpress -->
```

The block opens with an "Attempt Block Recovery" notice on first edit (because inner content isn't byte-identical to current `save.js` output) — one click recovers it. **Better**: use `wp.blocks.serialize()` via wp-pilot to get byte-perfect output and avoid the recovery prompt entirely. See `recipes/gutenberg-page.js`.

### `[embed]` shortcode is intercepted by WordPress core

`[embed cinematic_preview="yes" ...]url[/embed]` inside a Gutenberg shortcode block gets handled by WP core's `WP_Embed::run_shortcode()` before EmbedPress's handler runs — the cinematic_preview wrapper never gets applied.

**Fix**: use the `[embedpress]` shortcode alias instead (also registered by `Shortcode::register()`).

---

## Essential Addons widgets

### Real setting key names (not what they look like)

Discovered via `add_control()` calls in the EA Lite + EA Pro source. Always check `runtime/cache/widgets.json` for the current list; this is a quick reference for the most common.

| Widget | Critical key | Notes |
|---|---|---|
| `eael-creative-button` | `creative_button_text` | not `eael_creative_button_text`. Also: `creative_button_secondary_text`, `creative_button_link_url`, `creative_button_effect`. Colors use `eael_creative_button_background_color` etc. |
| `eael-counter` | `ending_number` (string), `counter_title`, `number_suffix` | Pro widget. Eael Lite has `eael-counter-up` with different keys. |
| `eael-dual-color-header` | `eael_dch_first_title`, `eael_dch_last_title` | Set `eael_dch_separator_position: 'none'` if you don't want the icon. |
| `eael-content-ticker` | `eael_ticker_type` | Lite default is `'dynamic'` (pulls from post types). `'custom'` mode for free-form text is **Pro-only**. If you need a simple promo banner, use a core heading widget — not the ticker. |
| `eael-logo-carousel` | `carousel_slides` (repeater) | Each item: `logo_carousel_slide: {id, url}`, `logo_title`, `hide_logo_title: 'yes'`. JS init may fail when the page is authored via REST + never opened in the Elementor editor — open + save once to wire up swiper. |
| `eael-testimonial-slider` | `carousel_slides` (repeater) | Each item: `eael_testimonial_description`, `eael_testimonial_company_title` (this is the NAME, not the company), `eael_testimonial_image`. |
| `eael-mailchimp` | `eael_mailchimp_lists` | **Renders an error UI without an API key configured.** For demos, use a different widget or set a fake API key in EA settings. |
| `eael-product-grid` / `eicon-woocommerce` | `eael_product_grid_product_filter: 'manual'` + `eael_product_grid_products_in: [ids...]` | Tag filtering via `eael_product_grid_tags` is unreliable — use manual selection for predictable demos. Setting widget slug is literally `eicon-woocommerce` (yes, looks like an icon name). |
| `eael-flip-box` | `eael_flipbox_img: {id, url}`, `eael_flipbox_front_title`, `eael_flipbox_back_title` | Set `eael_flipbox_button_show: 'no'` to drop the back-face button. Defaults to short height — set `eael_flipbox_height` explicitly. |

### EA Lite "Counter" widget is `eael-counter-up`; EA Pro adds `eael-counter`

They're separate widgets with different setting keys. Don't mix them.

---

## WooCommerce setup for EA product widgets

### Manual product selection beats tag/category filters

The cleanest way to display a specific set of products in `eael-product-grid` is `manual` filter mode:

```js
{
    eael_product_grid_product_filter: 'manual',
    eael_product_grid_products_in: ['2260', '2261', '2262', '2263'],  // string IDs
    eael_product_grid_products_count: '4',
}
```

Tag/category filters depend on slugs being attached correctly and can return empty/wrong results without obvious errors.

### Products need price meta to display correctly

For each product:
```php
update_post_meta($pid, '_regular_price', '120');
update_post_meta($pid, '_price', '120');
update_post_meta($pid, '_stock_status', 'instock');
wp_set_object_terms($pid, 'simple', 'product_type');
```

Missing `_price` makes the product render with no price label.

---

## REST-side authoring

### `meta:` field in `wp.apiFetch` only writes meta keys that are `show_in_rest`-registered

For Elementor's meta keys (`_elementor_data`, `_elementor_edit_mode`, `_elementor_template_type`), this works because Elementor registers them. For `_wp_page_template`, it doesn't — use wp-cli or `update_post_meta` directly.

### Elementor needs the editor to be opened once for CSS to regenerate

After writing `_elementor_data` via REST, the page renders with Elementor's default styles only. To trigger per-post CSS generation, open the editor headlessly (no manual save needed — just landing on it triggers regeneration):

```js
await runInEditor({
    url: `http://localhost:8188/wp-admin/post.php?post=${id}&action=elementor`,
    settleMs: 12000,
    evaluate: () => 'ok',
});
```

For triple safety, also run `$e.run('document/save/default')` in the same session.

---

## Headless screenshot quirks

### `networkidle` times out on busy WP installs

Use `domcontentloaded` + a `waitForTimeout` of 3-5s instead.

### Scroll-triggered animations need a scroll loop

Counter widgets, parallax bgs, animated SVGs — all use IntersectionObserver and don't fire in a static fullPage screenshot. Add a scroll-through:

```js
await page.evaluate(async () => {
    for (let y = 0; y < document.body.scrollHeight; y += 600) {
        window.scrollTo(0, y);
        await new Promise(r => setTimeout(r, 300));
    }
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 1500));
});
```

Then screenshot. The runner's `runOnFrontend` accepts a `drive` callback for exactly this.

---

## Figma asset URLs are short-lived

### MCP asset URLs expire after 7 days

The URLs returned by `mcp__figma__get_design_context` (`https://www.figma.com/api/mcp/asset/...`) are signed and expire. Always download the assets to local files in step 4 (ASSETS) — don't reference the Figma URLs directly in the built page.

```bash
curl -sS "<figma-asset-url>" -o /tmp/assets/<name>.png
# then: wp media import into WP, capture the IDs
```

---

## When to add an entry here

Anything that meets one of these tests:
- I just spent >10 minutes figuring out a widget setting that wasn't documented.
- A widget rendered defaults despite my settings being syntactically valid.
- A behavior only fires with Pro, only with Free, or only with both active.
- A postmeta or filter has to be set in a specific way unrelated to the widget itself.
- Two plugins interact in a non-obvious way (the `[embed]` shortcode collision is the classic).

Format:
```markdown
## <Topic>

### <Specific behavior / one-line summary>

What happens, why, the fix. Include code where applicable. Keep entries 5–15 lines — concise but enough to reproduce.
```

The point isn't completeness — it's saving the next session from re-learning.
