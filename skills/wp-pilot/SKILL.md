# wp-pilot

Drive the **real WordPress admin** programmatically — Gutenberg, Elementor, Customizer, plugin admin screens — by piloting a headless browser with admin credentials and running the same JS APIs the editor uses. Use when authoring/configuring content needs to be byte-perfect and editor-safe, not faked from PHP.

Use when the user asks to:

**Author mode** — build content
- Create / design / build a page (any block layout, any widgets)
- Author Elementor pages, widgets, templates
- Configure Customizer settings, theme.json, site editor
- Set up WooCommerce products, BetterDocs articles, anything with editor-only JS

**Test mode** — exercise admin flows
- Test a plugin's dashboard / settings page end-to-end ("does Cinematic Preview save correctly when toggled?")
- Walk through onboarding wizards, upsell modals, license activation flows
- Verify Pro vs free gating ("click the Pro toggle on free — does the upsell fire?")
- Reproduce a UI bug live ("the save button greys out after clicking once")
- Smoke-test before a release — open every settings tab, save, confirm no JS errors

**Verify mode** — see / inspect what's actually rendered
- Visually verify rendered output (screenshots, computed styles)
- Inspect editor state for debugging ("what attributes does Gutenberg actually see?")
- Capture network calls a settings page makes when saved

For stable regression suites (not ad-hoc QA), see `/Applications/Workspace/GitHub/embedpress-playwright-automation/` instead — that's the durable test repo.

Skip and prefer wp-cli / PHP for:
- Bulk operations (>50 items — Playwright would take hours)
- Core blocks with flat schemas (paragraph, heading, image, columns, cover, buttons, list) — PHP authoring is fine and 50× faster
- Anything purely server-side (post meta, options, user creation, plugin install)

---

## Why this beats hand-authoring markup from PHP

Some blocks/widgets only behave correctly when their JS `save()` function or live-preview pipeline runs. Examples:
- Gutenberg blocks with non-trivial `save.js` (EmbedPress, EAEL Lite, Stackable)
- Blocks with catch-all deprecation entries that strip attributes from PHP-authored void blocks
- Elementor widgets whose `data-*` attributes are built by JS, not the server widget render
- Anything that emits a JSON payload via JS helpers (`getPlayerOptions()`, `getCarouselOptions()`, etc.)

For those, only the JS path produces correct output. Hand-replicating it in PHP is brittle and drifts every release. wp-pilot lets the **real editor** produce the content, then we save it.

---

## The basic shape

Every wp-pilot script follows the same skeleton:

```js
const { chromium } = require('<playwright path>');
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();

  // 1. Headless admin login (separate cookie jar — does NOT touch user's browser)
  await page.goto('http://localhost:8188/wp-login.php');
  await page.fill('#user_login', 'admin');
  await page.fill('#user_pass', 'admin');
  await page.click('#wp-submit');
  await page.waitForLoadState('domcontentloaded');

  // 2. Land on a page that loads the JS surface you need
  //    (editor, Elementor, Customizer, etc.) — see recipes/
  await page.goto('http://localhost:8188/wp-admin/<surface>', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(8000); // editor mount, scripts warm-up

  // 3. Drive the real JS APIs in-page
  const result = await page.evaluate(async (input) => {
    // wp.blocks, wp.data, elementor, wp.customize — whatever applies
    return /* ... */;
  }, inputData);

  await browser.close();
})();
```

Run with: `<node-path> /tmp/your-script.js`

---

## Decision tree

```
Create / configure something in WordPress?
│
├── Does it need a JS API to produce correct output?
│   │
│   ├── YES — Gutenberg blocks with stateful save, Elementor widgets,
│   │         Customizer, anything that runs JS at save time
│   │   → wp-pilot
│   │
│   └── NO — post meta, options, simple core blocks, taxonomies,
│            user setup, plugin install
│       → wp-cli / PHP
│
├── Bulk operation (>50 items)?
│   → wp-cli always (loop in PHP)
│
└── Need to verify what the user actually sees?
    → wp-pilot screenshot
```

When in doubt: try wp-cli first. If the output doesn't match what the editor would produce (recovery prompts, missing attributes, broken previews), switch to wp-pilot.

---

## Common JS surfaces to drive

| Surface | URL to land on | Key API |
|---|---|---|
| Gutenberg block editor | `/wp-admin/post.php?post=<id>&action=edit` | `wp.blocks.createBlock`, `wp.blocks.serialize`, `wp.data.dispatch('core/block-editor')` |
| Site Editor | `/wp-admin/site-editor.php` | `wp.data.dispatch('core').saveSiteSettings`, theme.json mutations |
| Elementor editor | `/wp-admin/post.php?post=<id>&action=elementor` | `elementor.documents`, `elementor.helpers`, `$e.run('document/elements/create')` |
| Customizer | `/wp-admin/customize.php` | `wp.customize('<setting_id>').set(...)`, `wp.customize.previewer.save()` |
| WooCommerce product editor | `/wp-admin/post.php?post=<id>&action=edit` (product) | `wp.data.dispatch` + product blocks API |
| Plugin admin screens (EmbedPress, BetterDocs, EAEL settings) | the plugin's settings page | Each plugin's own `window.<plugin>Data` namespace |

REST is always a fallback — once you have admin cookies, `wp.apiFetch({ path: ..., method: ..., data: ... })` covers anything REST-accessible.

---

## Capability map — what wp-pilot can actually do

Anything an admin user can do in `wp-admin`, plus arbitrary JS in the page context:

- **Create pages with any blocks** — `wp.blocks.createBlock(name, attrs)` → `wp.blocks.serialize([...])` → POST to `/wp/v2/pages/<id>`
- **Author Elementor pages** — build the `_elementor_data` JSON via in-editor APIs, save through REST
- **Configure Customizer** — drive `wp.customize` setters, trigger publish
- **Configure plugin admin screens** — fill forms, click save, verify response
- **Set up WooCommerce** — products, categories, attributes, shipping zones, payment gateways
- **Build menus / patterns / reusable blocks** — same pattern, different APIs
- **Visual QA** — screenshot any URL, anonymous or authenticated, any viewport
- **Inspect editor state** — `wp.data.select('core/block-editor').getBlocks()` to debug what attributes the editor actually parsed

---

## Testing plugin dashboards — pattern

For test/QA mode, don't use `evaluate()` for everything — use Playwright's real interaction APIs (`page.click`, `page.fill`, `page.locator`, `page.waitForResponse`) so you exercise the actual user code paths. `evaluate()` is for in-page state inspection only.

Skeleton:

```js
const { runInEditor } = require('../lib/runner.js');

// runInEditor exposes the `page` object via opts.onPage(page) if needed,
// OR write a one-off script directly with the runner's login boilerplate
// inlined — both forms in recipes/.
```

Three patterns you'll reach for repeatedly:

**1. Click → save → assert response.** The most common QA flow.
```js
await page.click('button.save-settings');
const resp = await page.waitForResponse((r) =>
    r.url().includes('/wp-admin/admin-ajax.php') && r.status() === 200
);
const body = await resp.json();
// assert body.success / body.data / etc.
```

**2. Toggle a setting → reload → confirm persisted.** Catches save-but-doesn't-stick bugs.
```js
await page.locator('label:has-text("Cinematic Preview")').click();
await page.click('.save-button');
await page.waitForLoadState('networkidle');
await page.reload();
const checked = await page.locator('input[name="cinematic_preview"]').isChecked();
if (!checked) throw new Error('Setting did not persist after reload');
```

**3. Capture upsell / modal behavior on free.** Pro-gating verification.
```js
// Pro plugin off (do this server-side first via wp plugin deactivate)
await page.locator('[data-pro-feature="cinematic_preview"]').click();
const modal = await page.waitForSelector('.embedpress-pro-modal', { timeout: 3000 });
const title = await modal.textContent();
// assert it contains "Upgrade" or whichever expected copy
```

For complex flows, add a recipe file rather than re-deriving each time. See `recipes/dashboard-flow.js` for the full skeleton.

---

## Recipes (in `recipes/`)

Pre-built script templates for the common cases — copy + tweak the body:

**Authoring**
- [`gutenberg-page.js`](recipes/gutenberg-page.js) — author a page with Gutenberg blocks via `wp.blocks.serialize()`
- [`elementor-page.js`](recipes/elementor-page.js) — author an Elementor page (sections / columns / widgets)

**Testing / interaction**
- [`dashboard-flow.js`](recipes/dashboard-flow.js) — click through a plugin's admin screen, save, assert response, reload, verify persistence
- [`pro-gating.js`](recipes/pro-gating.js) — deactivate Pro server-side, drive the free UI, assert upsell modal fires on Pro-only controls

**Verification**
- [`screenshot.js`](recipes/screenshot.js) — frontend or admin screenshot at any viewport
- [`inspect-editor.js`](recipes/inspect-editor.js) — dump parsed block attributes for debugging

---

## Limits

- **~5-10s startup per session** (login + editor mount). Batch operations into one session.
- **Each spawn is cold** — no cookie reuse unless you persist `storageState` (see `lib/runner.js`).
- **`networkidle` is unreliable on busy WP installs** — use `domcontentloaded` + a short `waitForTimeout` instead.
- **Headless ≠ your browser** — different fonts, no extensions. Usually irrelevant; matters for pixel-perfect QA.
- **Doesn't share session with your real browser** — separate cookie jar by design. You can work in `wp-admin` simultaneously without conflict (other than the WP post-lock warning if both edit the same post).

---

## Dependencies

Playwright + a working Node. The sandbox doesn't ship its own copy — discover one via:

```bash
# Whichever exists:
find /Applications/Workspace/GitHub -path '*/node_modules/playwright/package.json' -not -path '*/node_modules/*/node_modules/*' 2>/dev/null | head -1
# or use a globally-installed one
which playwright
```

EmbedPress's repo currently has one at `/Applications/Workspace/GitHub/embedpress/node_modules/playwright` — recipes reference it but accept overrides.

---

## When this skill is the wrong tool

- Heavy bulk authoring (1000+ pages) — write PHP, use wp-cli.
- Pure data import (CSV → posts) — wp-cli + a PHP loop.
- Anything where the JS path doesn't add value — don't pay the 5-10s tax for no reason.

If the user asks "just create a hero section + 3 features + a CTA" with all core blocks, that's pure PHP — wp-cli post create with block markup, done in 1s.
