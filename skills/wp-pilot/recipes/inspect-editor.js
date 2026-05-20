/**
 * Recipe: inspect what the Gutenberg editor actually parses from a post.
 *
 * Use when blocks render wrong, the editor shows "Attempt Block Recovery,"
 * or attributes seem missing — this dumps the editor's view of the parsed
 * blocks so you can see exactly which attribute keys survived parsing.
 *
 * Usage: edit POST_ID below, then `node inspect-editor.js`
 */

const { runInEditor } = require('../lib/runner.js');

const POST_ID = 2213;

(async () => {
    const r = await runInEditor({
        url: `http://localhost:8188/wp-admin/post.php?post=${POST_ID}&action=edit`,
        input: { },
        evaluate: () => {
            const blocks = wp.data.select('core/block-editor').getBlocks();
            return blocks.map((b) => ({
                name: b.name,
                isValid: b.isValid,
                validationIssueCount: (b.validationIssues || []).length,
                attrKeys: Object.keys(b.attributes).sort(),
                // sample first 3 string values to spot truncation / encoding issues
                sampleAttrs: Object.fromEntries(
                    Object.entries(b.attributes).slice(0, 8).map(([k, v]) => [
                        k,
                        typeof v === 'string' ? v.slice(0, 60) : v,
                    ])
                ),
            }));
        },
    });
    console.log(JSON.stringify(r, null, 2));
})();
