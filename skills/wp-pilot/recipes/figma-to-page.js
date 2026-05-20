/**
 * Recipe: full Figma-to-WordPress page build, following the 7-step PLAN-FIRST
 * workflow from SKILL.md.
 *
 * This recipe is a TEMPLATE — it shows the end-to-end shape. Adapt the PLAN
 * object below to your specific design. The flow is the same regardless:
 *
 *   1. INTAKE     — mcp__figma__get_design_context (run separately, paste asset list + plan here)
 *   2. PLAN       — section-by-section widget map (CONFIRM WITH USER before running)
 *   3. INVENTORY  — install/activate plugins
 *   4. ASSETS     — download figma assets, upload to media library
 *   5. BUILD      — author with proper widget keys (look up in runtime/cache/widgets.json)
 *   6. VERIFY     — screenshot side-by-side
 *   7. ITERATE    — see recipes/verify-and-fix.js
 *
 * Read gotchas.md before running this. Especially:
 *   - Page template _wp_page_template = elementor_canvas
 *   - image + background_image both need {id, url}
 *   - SVG mu-plugin if logos are SVG
 *   - Fonts: enqueue + Elementor filter registration
 */

const { runInEditor, runOnFrontend } = require('../lib/runner.js');
const { execSync } = require('child_process');
const path = require('path');

// ============================================================
// 1. INTAKE — fill these from mcp__figma__get_design_context output
// ============================================================
const DESIGN = {
    figma: {
        fileKey: 'ZvzHxbluoDICgxjGCRMv6d',           // from URL
        nodeId:  '20:2',                              // from URL (convert - to :)
    },
    // Asset URLs returned from mcp__figma__get_design_context.
    // Download these to /tmp/assets/, then upload to media library in step 4.
    assets: [
        // { name: 'hero',         url: 'https://www.figma.com/api/mcp/asset/...' },
        // { name: 'logo-versace', url: 'https://www.figma.com/api/mcp/asset/...', svg: true },
    ],
    fonts: [
        // { family: 'Satoshi', cssUrl: 'https://api.fontshare.com/v2/css?f[]=satoshi@400,700&display=swap' },
        // { family: 'Anton',   cssUrl: 'https://fonts.googleapis.com/css2?family=Anton&display=swap' },
    ],
};

// ============================================================
// 2. PLAN — section-by-section. WRITE THIS UP FRONT. CONFIRM WITH USER.
// ============================================================
const PLAN = {
    title: 'Example Landing',
    slug: 'example-landing',
    pageTemplate: 'elementor_canvas',
    pluginsRequired: ['elementor', 'essential-addons-for-elementor-lite', 'essential-addons-elementor', 'woocommerce'],
    sections: [
        // Each entry is a planning record — informational. The actual builder
        // below reads this to choose widgets. Edit before running.
        // { name: 'Promo bar',     widget: 'heading',             plugin: 'elementor-core',   notes: 'core heading not eael-content-ticker (Pro-only for custom content)' },
        // { name: 'Hero',          widget: 'heading + text-editor + eael-creative-button',  plugin: 'eael-lite + core', notes: 'bg image on section' },
        // { name: 'Stats',         widget: 'eael-counter (×3)',   plugin: 'eael-pro' },
        // { name: 'Brands',        widget: 'core image (×5)',     plugin: 'core',             notes: 'eael-logo-carousel needs editor save to wire swiper' },
        // { name: 'New Arrivals',  widget: 'eicon-woocommerce',   plugin: 'eael-lite + wc',   notes: 'product_filter: manual, products_in: [...]' },
        // ... etc
    ],
};

// ============================================================
// 3-7. Below is the executor — usually doesn't need editing per design.
// ============================================================

const POST_ID = process.env.POST_ID ? parseInt(process.env.POST_ID, 10) : null;

(async () => {
    console.log('=== PLAN ===');
    console.log(JSON.stringify(PLAN, null, 2));
    console.log('\n=== READY? Ctrl-C to cancel; otherwise continuing in 3s... ===');
    await new Promise(r => setTimeout(r, 3000));

    // 3. INVENTORY — verify plugins are active
    console.log('=== INVENTORY ===');
    try {
        const out = execSync(`cd /Applications/Workspace/GitHub/sandbox && ./wp-sandbox wp plugin list --status=active --format=csv 2>&1 | tail -20`).toString();
        for (const slug of PLAN.pluginsRequired) {
            if (!out.includes(slug)) console.warn(`  MISSING: ${slug} — activate before continuing`);
            else console.log(`  ✓ ${slug}`);
        }
    } catch (e) { console.error('plugin check failed:', e.message); }

    // 4. ASSETS — caller's responsibility (download via curl, upload via wp media import)
    //    Result: a map of name → wp media id. Pass into the BUILD step.
    const ASSET_IDS = {
        // hero: 2243, logoVersace: 2271, etc — fill in after wp media import returns IDs
    };

    // 5. BUILD — replace this stub with the actual elements composition.
    //    Reference runtime/cache/widgets.json for setting keys.
    //    Reference gotchas.md for traps (especially image id+url patching).
    const ELEMENTS = [
        /* { id: '...', elType: 'section', settings: {...}, elements: [...] } */
    ];

    if (ELEMENTS.length === 0) {
        console.log('=== BUILD: no ELEMENTS defined — fill them in based on the plan, then re-run ===');
        return;
    }

    // Patch image + background_image: every {id} also needs {url}
    const patchUrls = async (elements) => {
        const collected = new Set();
        const walk = (el) => {
            if (el.elType === 'widget' && el.widgetType === 'image' && el.settings?.image?.id) collected.add(el.settings.image.id);
            if (el.settings?.background_image?.id) collected.add(el.settings.background_image.id);
            (el.elements || []).forEach(walk);
        };
        elements.forEach(walk);

        // Look up urls in one batch via REST
        const urls = await runInEditor({
            url: 'http://localhost:8188/wp-admin/',
            settleMs: 0,
            input: { ids: [...collected] },
            evaluate: async ({ ids }) => {
                const result = {};
                for (const id of ids) {
                    const r = await wp.apiFetch({ path: `/wp/v2/media/${id}?_fields=source_url` });
                    result[id] = r.source_url;
                }
                return result;
            },
        });

        const apply = (el) => {
            if (el.elType === 'widget' && el.widgetType === 'image' && el.settings?.image?.id) {
                el.settings.image.url = urls[el.settings.image.id] || el.settings.image.url || '';
            }
            if (el.settings?.background_image?.id) {
                el.settings.background_image.url = urls[el.settings.background_image.id] || '';
            }
            (el.elements || []).forEach(apply);
        };
        elements.forEach(apply);
    };

    await patchUrls(ELEMENTS);

    // Write to a (new or existing) page
    const result = await runInEditor({
        url: 'http://localhost:8188/wp-admin/',
        settleMs: 0,
        input: { plan: PLAN, elements: ELEMENTS, existingId: POST_ID },
        evaluate: async ({ plan, elements, existingId }) => {
            let pid = existingId;
            if (!pid) {
                const created = await wp.apiFetch({
                    path: '/wp/v2/pages',
                    method: 'POST',
                    data: { title: plan.title, slug: plan.slug, status: 'publish' },
                });
                pid = created.id;
            }
            await wp.apiFetch({
                path: `/wp/v2/pages/${pid}`,
                method: 'POST',
                data: {
                    meta: {
                        _elementor_data: JSON.stringify(elements),
                        _elementor_edit_mode: 'builder',
                        _elementor_template_type: 'wp-page',
                    },
                },
            });
            return { pid };
        },
    });

    // Page template via wp-cli (REST meta path doesn't always honor it — gotchas.md)
    try {
        execSync(`cd /Applications/Workspace/GitHub/sandbox && ./wp-sandbox wp post meta update ${result.pid} _wp_page_template ${PLAN.pageTemplate}`).toString();
    } catch (e) { console.warn('page template set failed:', e.message); }

    // Elementor CSS regen — open the editor headlessly once
    await runInEditor({
        url: `http://localhost:8188/wp-admin/post.php?post=${result.pid}&action=elementor`,
        settleMs: 15000,
        drive: async (page) => {
            await page.evaluate(async () => {
                if (window.$e && $e.run) { try { await $e.run('document/save/default'); } catch (e) {} }
            });
            await page.waitForTimeout(3000);
        },
    });

    // 6. VERIFY — screenshot the result. See recipes/verify-and-fix.js for the diff loop.
    await runOnFrontend({
        url: `http://localhost:8188/?page_id=${result.pid}`,
        viewport: { width: 1440, height: 900 },
        screenshot: `/tmp/${PLAN.slug}.png`,
        fullPage: true,
        settleMs: 4000,
        waitUntil: 'domcontentloaded',
        drive: async (page) => {
            await page.evaluate(async () => {
                for (let y = 0; y < document.body.scrollHeight; y += 600) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 300)); }
                window.scrollTo(0, 0); await new Promise(r => setTimeout(r, 1500));
            });
        },
    });

    console.log(`\n=== DONE ===`);
    console.log(`Page ID: ${result.pid}`);
    console.log(`URL:     http://localhost:8188/?page_id=${result.pid}`);
    console.log(`Shot:    /tmp/${PLAN.slug}.png`);
    console.log(`\nNext: open the screenshot next to the Figma reference and run recipes/verify-and-fix.js for per-section diff.`);
})();
