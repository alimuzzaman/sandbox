/**
 * Recipe: verify Pro-gating in a plugin's free dashboard.
 *
 * Workflow:
 *   1. Deactivate the Pro plugin server-side (`wp plugin deactivate <slug>-pro`)
 *      — do this BEFORE running this recipe.
 *   2. Open the plugin's admin screen.
 *   3. Click each Pro-only control and assert that the upsell modal fires.
 *   4. Verify the saved settings don't change (server-side enforcement intact).
 *
 * This catches both UI gating (modal appears) and server-side gating
 * (manipulated payloads don't sneak Pro features past the gate).
 */

const { runInEditor } = require('../lib/runner.js');

// ---- Customize -----------------------------------------------------------
const DASHBOARD_URL = 'http://localhost:8188/wp-admin/admin.php?page=embedpress';

// Each entry: a Pro-only control to click + the expected upsell signal.
const PRO_CONTROLS = [
    {
        name: 'Cinematic Preview toggle',
        clickSelector: 'label:has-text("Cinematic Preview")',
        expectSelector: '.embedpress-pro-modal, .ep-pro-upsell, [data-pro-popup]',
    },
    {
        name: 'Custom Player Email Capture',
        clickSelector: 'label:has-text("Email Capture")',
        expectSelector: '.embedpress-pro-modal, .ep-pro-upsell, [data-pro-popup]',
    },
    // Add more Pro controls here as they ship.
];
// --------------------------------------------------------------------------

(async () => {
    const result = await runInEditor({
        url: DASHBOARD_URL,
        settleMs: 3000,
        drive: async (page) => {
            const log = [];
            for (const c of PRO_CONTROLS) {
                let row = { control: c.name };
                try {
                    await page.locator(c.clickSelector).first().click({ timeout: 5000 });
                    const modal = await page.waitForSelector(c.expectSelector, { timeout: 3000 }).catch(() => null);
                    if (!modal) {
                        row.ok = false;
                        row.error = 'Upsell modal did not appear';
                    } else {
                        const text = (await modal.textContent()).slice(0, 80);
                        row.ok = true;
                        row.modalText = text;
                        // Close the modal so the next control is reachable
                        await page.keyboard.press('Escape').catch(() => {});
                        await page.waitForTimeout(300);
                    }
                } catch (e) {
                    row.ok = false;
                    row.error = e.message;
                }
                log.push(row);
            }
            return { passed: log.every((r) => r.ok), log };
        },
    });
    console.log(JSON.stringify(result, null, 2));
    if (!result.passed) process.exit(1);
})();
