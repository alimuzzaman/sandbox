/**
 * wp-pilot runner — boilerplate for headless admin sessions.
 *
 * Usage from a recipe / one-off script:
 *
 *   const { runInEditor } = require('<absolute-path-to>/runner.js');
 *
 *   const result = await runInEditor({
 *     // Where to land. Defaults to a clean dashboard.
 *     url: 'http://localhost:8188/wp-admin/post.php?post=123&action=edit',
 *     // Code to run inside the page (gets passed `input` as its argument).
 *     evaluate: async (input) => {
 *       const blocks = [
 *         wp.blocks.createBlock('core/heading', { content: input.title }),
 *       ];
 *       const html = wp.blocks.serialize(blocks);
 *       await wp.apiFetch({ path: `/wp/v2/pages/${input.postId}`, method: 'POST', data: { content: html } });
 *       return { ok: true, length: html.length };
 *     },
 *     input: { postId: 123, title: 'Hello' },
 *   });
 *
 *   console.log(result); // { ok: true, length: 47 }
 */

const PLAYWRIGHT_CANDIDATES = [
    '/Applications/Workspace/GitHub/embedpress/node_modules/playwright',
    '/Applications/Workspace/GitHub/embedpress-pro/node_modules/playwright',
    '/Applications/Workspace/GitHub/embedpress-playwright-automation/node_modules/playwright',
];

function requirePlaywright() {
    for (const p of PLAYWRIGHT_CANDIDATES) {
        try { return require(p); } catch (e) { /* try next */ }
    }
    try { return require('playwright'); } catch (e) {
        throw new Error('Playwright not found. Install it in one of: ' + PLAYWRIGHT_CANDIDATES.join(', '));
    }
}

const DEFAULTS = {
    site: process.env.WP_SITE || 'http://localhost:8188',
    user: process.env.WP_USER || 'admin',
    pass: process.env.WP_PASS || 'admin',
    viewport: { width: 1400, height: 900 },
    settleMs: 8000, // editor mount + script warm-up
};

async function runInEditor(opts) {
    const { chromium } = requirePlaywright();
    const settings = { ...DEFAULTS, ...opts };

    const browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: settings.viewport });
    const page = await ctx.newPage();

    try {
        // Login
        await page.goto(`${settings.site}/wp-login.php`);
        await page.fill('#user_login', settings.user);
        await page.fill('#user_pass', settings.pass);
        await page.click('#wp-submit');
        await page.waitForLoadState('domcontentloaded');

        // Land on the requested admin URL (defaults to dashboard)
        const url = settings.url || `${settings.site}/wp-admin/`;
        await page.goto(url, { waitUntil: 'domcontentloaded' });

        // Editor / Elementor / Customizer need a beat to wire up their JS surface.
        // Caller can override with settleMs: 0 for plain admin pages.
        if (settings.settleMs > 0) await page.waitForTimeout(settings.settleMs);

        // Two modes:
        //   - evaluate(): run code inside the page context (good for wp.* API calls)
        //   - drive(page, input): receives the Playwright page for click/fill/assert flows
        // Recipes can use either; testing/interaction recipes prefer drive().
        let result;
        if (typeof settings.drive === 'function') {
            result = await settings.drive(page, settings.input);
        } else if (typeof settings.evaluate === 'function') {
            result = await page.evaluate(settings.evaluate, settings.input);
        } else {
            throw new Error('runInEditor: pass evaluate() or drive() function');
        }

        // Optional: screenshot
        if (settings.screenshot) {
            await page.screenshot({ path: settings.screenshot, fullPage: !!settings.fullPage });
        }

        return result;
    } finally {
        await browser.close();
    }
}

/**
 * Anonymous frontend visit — no login, no admin, just an unauthenticated reader.
 * Useful for verifying public output, taking visual QA shots.
 */
async function runOnFrontend(opts) {
    const { chromium } = requirePlaywright();
    const settings = { ...DEFAULTS, ...opts };

    const browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: settings.viewport });
    const page = await ctx.newPage();

    try {
        await page.goto(settings.url, { waitUntil: settings.waitUntil || 'networkidle' });
        if (settings.settleMs) await page.waitForTimeout(settings.settleMs);
        let result = null;
        if (typeof settings.drive === 'function') {
            result = await settings.drive(page, settings.input);
        } else if (typeof settings.evaluate === 'function') {
            result = await page.evaluate(settings.evaluate, settings.input);
        }
        if (settings.screenshot) {
            await page.screenshot({ path: settings.screenshot, fullPage: !!settings.fullPage });
        }
        return result;
    } finally {
        await browser.close();
    }
}

module.exports = { runInEditor, runOnFrontend, requirePlaywright };
