/**
 * Recipe: per-section visual diff between WP frontend and a reference image,
 * with a structured slot for applying targeted fixes.
 *
 * Workflow:
 *   1. Capture the WP page at a specified viewport.
 *   2. Optionally capture the Figma node screenshot (paste the URL).
 *   3. For each named section in CHECKS below, crop the WP shot to that
 *      section's bounding rect (selector) and save it.
 *   4. The author (you / Claude) reads the cropped shots next to the Figma
 *      reference and decides what's MATCH / GAP / BROKEN.
 *   5. For each GAP/BROKEN, write a targeted patch (see PATCHES below) that
 *      mutates only the affected widget's settings, then re-runs.
 *
 * This recipe deliberately does NOT try to do automatic pixel-diff — that
 * produces high false-positive rates on type rendering / antialiasing.
 * It produces the artifacts so a human or Claude can compare structurally.
 */

const { runInEditor, runOnFrontend } = require('../lib/runner.js');
const fs = require('fs');

// ============================================================
// Configure these for your page.
// ============================================================
const POST_ID = process.env.POST_ID ? parseInt(process.env.POST_ID, 10) : 2237;
const SITE = 'http://localhost:8188';
const VIEWPORT = { width: 1440, height: 900 };
const OUT_DIR = '/tmp/verify-shots';

// Sections you want isolated screenshots of. Add a CSS selector that uniquely
// identifies each section on the rendered page. Adjust to match the Elementor
// section IDs (data-id attributes) or your own anchors.
const CHECKS = [
    // { name: 'promo-bar',     selector: '.elementor-section:nth-child(1)' },
    // { name: 'header',        selector: '.elementor-section:nth-child(2)' },
    // { name: 'hero',          selector: '.elementor-section:nth-child(3)' },
    // { name: 'stats',         selector: '.elementor-section:nth-child(4)' },
    // { name: 'brands',        selector: '.elementor-section:nth-child(5)' },
    // { name: 'new-arrivals',  selector: '.elementor-section:nth-child(6)' },
    // { name: 'top-selling',   selector: '.elementor-section:nth-child(10)' },
    // { name: 'dress-style',   selector: '.elementor-section:nth-child(13)' },
    // { name: 'testimonials',  selector: '.elementor-section:nth-child(17)' },
    // { name: 'newsletter',    selector: '.elementor-section:nth-child(18)' },
    // { name: 'footer',        selector: '.elementor-section:nth-child(19)' },
];

// Patches to apply BEFORE re-screenshotting. Each patch is a function that
// receives the parsed _elementor_data tree and mutates the specific widget.
// Add patches incrementally as you identify GAP / BROKEN sections.
//
// Example: change hero heading size
//   {
//     name: 'hero heading size',
//     apply: (data) => {
//       const hero = findSection(data, 2);
//       const h1 = findWidget(hero, 'heading');
//       h1.settings.typography_font_size = { unit: 'px', size: 72 };
//     },
//   }
const PATCHES = [
    // { name: '...', apply: (data) => { /* mutate data */ } },
];

// ============================================================
// Executor — usually doesn't need editing.
// ============================================================

const findById = (data, id) => {
    for (const el of data) {
        if (el.id === id) return el;
        if (el.elements) { const r = findById(el.elements, id); if (r) return r; }
    }
    return null;
};
const findSection = (data, oneBasedIndex) => {
    const top = data.filter(e => e.elType === 'section');
    return top[oneBasedIndex - 1] || null;
};
const findWidget = (root, widgetType) => {
    if (root.elType === 'widget' && root.widgetType === widgetType) return root;
    for (const c of root.elements || []) {
        const r = findWidget(c, widgetType);
        if (r) return r;
    }
    return null;
};

(async () => {
    fs.mkdirSync(OUT_DIR, { recursive: true });

    // 1. Apply any patches first
    if (PATCHES.length > 0) {
        console.log(`=== Applying ${PATCHES.length} patches ===`);
        await runInEditor({
            url: `${SITE}/wp-admin/`,
            settleMs: 0,
            input: { postId: POST_ID, patches: PATCHES.map(p => p.name) },
            evaluate: async ({ postId }) => {
                const page = await wp.apiFetch({ path: `/wp/v2/pages/${postId}?context=edit&_fields=meta` });
                const data = JSON.parse(page.meta._elementor_data);
                // patches are applied client-side in the next runInEditor — we don't ship them as JS strings
                window.__wp_pilot_data = data;
                return { ok: true };
            },
        });
        // re-fetch + mutate locally + write back
        const data = await runInEditor({
            url: `${SITE}/wp-admin/`,
            settleMs: 0,
            input: { postId: POST_ID },
            evaluate: async ({ postId }) => {
                const page = await wp.apiFetch({ path: `/wp/v2/pages/${postId}?context=edit&_fields=meta` });
                return JSON.parse(page.meta._elementor_data);
            },
        });
        for (const patch of PATCHES) {
            try { patch.apply(data); console.log(`  ✓ ${patch.name}`); }
            catch (e) { console.error(`  ✗ ${patch.name}: ${e.message}`); }
        }
        await runInEditor({
            url: `${SITE}/wp-admin/`,
            settleMs: 0,
            input: { postId: POST_ID, data },
            evaluate: async ({ postId, data }) => {
                await wp.apiFetch({
                    path: `/wp/v2/pages/${postId}`,
                    method: 'POST',
                    data: { meta: { _elementor_data: JSON.stringify(data) } },
                });
                return { ok: true };
            },
        });
        // Regen Elementor CSS
        await runInEditor({
            url: `${SITE}/wp-admin/post.php?post=${POST_ID}&action=elementor`,
            settleMs: 12000,
            drive: async (page) => {
                await page.evaluate(async () => {
                    if (window.$e && $e.run) { try { await $e.run('document/save/default'); } catch (e) {} }
                });
                await page.waitForTimeout(3000);
            },
        });
    }

    // 2. Full page screenshot
    console.log('=== Capturing full page ===');
    const fullPath = `${OUT_DIR}/${POST_ID}-full.png`;
    await runOnFrontend({
        url: `${SITE}/?page_id=${POST_ID}`,
        viewport: VIEWPORT,
        screenshot: fullPath,
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
    console.log(`  ${fullPath}`);

    // 3. Per-section crops
    if (CHECKS.length > 0) {
        console.log(`=== Capturing ${CHECKS.length} section crops ===`);
        await runOnFrontend({
            url: `${SITE}/?page_id=${POST_ID}`,
            viewport: VIEWPORT,
            settleMs: 3000,
            waitUntil: 'domcontentloaded',
            drive: async (page) => {
                for (const check of CHECKS) {
                    try {
                        const locator = page.locator(check.selector).first();
                        await locator.scrollIntoViewIfNeeded();
                        await page.waitForTimeout(500);
                        const path = `${OUT_DIR}/${POST_ID}-${check.name}.png`;
                        await locator.screenshot({ path });
                        console.log(`  ✓ ${check.name}: ${path}`);
                    } catch (e) {
                        console.warn(`  ✗ ${check.name}: ${e.message}`);
                    }
                }
            },
        });
    }

    console.log(`\n=== DONE ===`);
    console.log(`Open ${OUT_DIR}/ and compare against the Figma reference.`);
    console.log(`For each GAP/BROKEN section, write a patch in the PATCHES array above and re-run.`);
})();
