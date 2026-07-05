/**
 * Recipe: author an Elementor page.
 *
 * Two paths — pick based on the widget:
 *
 *  A) Pure data path (FAST, no editor needed):
 *     - Write `_elementor_data` postmeta directly via REST
 *     - Works for widgets whose render is fully driven by stored settings
 *       (most native widgets, EmbedPress Elementor widget, EAEL Lite basics)
 *     - 100× faster than driving the editor
 *
 *  B) Editor path (when widget has live-only behaviors or needs CSS regen):
 *     - Open `/wp-admin/post.php?post=<id>&action=elementor`
 *     - Drive `$e.run('document/elements/create', { ... })`
 *     - Save via `elementor.documents.save()` or `$e.run('document/save/default')`
 *     - Slow, but produces output that matches the editor exactly
 *
 * Default: tries path A; if widgets need the editor pipeline, switch.
 */

const { runInEditor } = require('../lib/runner.js');

// ---- Customize -----------------------------------------------------------
const POST_ID = 2216;
const ELEMENTS = [
    {
        elType: 'section',
        settings: {},
        elements: [{
            elType: 'column',
            settings: { _column_size: 100 },
            elements: [
                { elType: 'widget', widgetType: 'heading', settings: { title: 'Hello from wp-pilot (Elementor)', header_size: 'h1' } },
                { elType: 'widget', widgetType: 'text-editor', settings: { editor: '<p>Authored via REST + Elementor data.</p>' } },
            ],
        }],
    },
];
// --------------------------------------------------------------------------

// Path A — pure REST data write. Fast.
(async () => {
    const result = await runInEditor({
        // No editor mount needed — we just need wp.apiFetch with admin cookies
        url: `http://localhost:8188/wp-admin/`,
        settleMs: 0,
        input: { postId: POST_ID, elements: ELEMENTS },
        evaluate: async ({ postId, elements }) => {
            // Assign Elementor IDs (7 hex chars — matches getUniqueId(); an
            // 8-char id desyncs from Elementor's format, see spec 005 T013)
            const assignIds = (arr) => arr.forEach((e) => {
                e.id = e.id || Math.random().toString(16).slice(2, 9);
                if (Array.isArray(e.elements)) assignIds(e.elements);
            });
            assignIds(elements);

            // Update postmeta via REST
            await wp.apiFetch({
                path: `/wp/v2/pages/${postId}`,
                method: 'POST',
                data: {
                    meta: {
                        _elementor_data: JSON.stringify(elements),
                        _elementor_edit_mode: 'builder',
                        _elementor_template_type: 'wp-page',
                    },
                },
            });

            return { ok: true, sectionCount: elements.length };
        },
    });
    console.log(JSON.stringify(result, null, 2));
})();
