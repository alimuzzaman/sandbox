/**
 * Recipe: test a plugin's admin dashboard end-to-end.
 *
 * Pattern: navigate → toggle → save → assert response → reload → confirm
 * persistence. Each step is checked; failure throws with a clear message so
 * Claude can report exactly which step broke.
 *
 * Customize the FLOW below for the dashboard you're testing.
 */

const { runInEditor } = require('../lib/runner.js');

// ---- Customize -----------------------------------------------------------
const DASHBOARD_URL = 'http://localhost:8188/wp-admin/admin.php?page=embedpress';

// Each step: { name, do(page, ctx), expect(page, ctx)? }
// `do` runs the interaction. `expect` (optional) asserts state — throw on fail.
const FLOW = [
    {
        name: 'Open Settings tab',
        do: async (page) => {
            await page.click('a:has-text("Settings"), [data-tab="settings"]');
            await page.waitForTimeout(500);
        },
        expect: async (page) => {
            const visible = await page.locator('.embedpress-settings, [data-panel="settings"]').first().isVisible();
            if (!visible) throw new Error('Settings panel did not render');
        },
    },
    {
        name: 'Toggle Cinematic Preview ON',
        do: async (page) => {
            // Adjust selector to the actual control id/name in your plugin
            const toggle = page.locator('input[name*="cinematic_preview"]').first();
            if (!(await toggle.isChecked())) await toggle.click();
        },
    },
    {
        name: 'Save and capture AJAX response',
        do: async (page, ctx) => {
            const respPromise = page.waitForResponse((r) =>
                r.url().includes('admin-ajax.php') || r.url().includes('/wp-json/')
            );
            await page.click('button:has-text("Save"), .save-settings');
            const resp = await respPromise;
            ctx.lastResponseStatus = resp.status();
            try { ctx.lastResponseBody = await resp.json(); } catch { ctx.lastResponseBody = await resp.text(); }
        },
        expect: async (page, ctx) => {
            if (ctx.lastResponseStatus >= 400) {
                throw new Error(`Save returned ${ctx.lastResponseStatus}: ${JSON.stringify(ctx.lastResponseBody).slice(0, 200)}`);
            }
        },
    },
    {
        name: 'Reload and verify the toggle persisted',
        do: async (page) => {
            await page.reload({ waitUntil: 'domcontentloaded' });
            await page.waitForTimeout(2000);
            await page.click('a:has-text("Settings"), [data-tab="settings"]').catch(() => {});
        },
        expect: async (page) => {
            const checked = await page.locator('input[name*="cinematic_preview"]').first().isChecked();
            if (!checked) throw new Error('Cinematic Preview toggle did not persist after reload');
        },
    },
];
// --------------------------------------------------------------------------

(async () => {
    const result = await runInEditor({
        url: DASHBOARD_URL,
        settleMs: 3000,
        drive: async (page) => {
            const log = [];
            const ctx = {};
            for (const step of FLOW) {
                try {
                    await step.do(page, ctx);
                    if (step.expect) await step.expect(page, ctx);
                    log.push({ step: step.name, ok: true });
                } catch (e) {
                    log.push({ step: step.name, ok: false, error: e.message });
                    // Optional: screenshot on failure for debugging
                    await page.screenshot({ path: `/tmp/wp-pilot-fail-${Date.now()}.png`, fullPage: true });
                    break;
                }
            }
            return { passed: log.every((s) => s.ok), log };
        },
    });
    console.log(JSON.stringify(result, null, 2));
    if (!result.passed) process.exit(1);
})();
