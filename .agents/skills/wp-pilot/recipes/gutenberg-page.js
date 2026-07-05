/**
 * Recipe: author a Gutenberg page via wp.blocks.serialize().
 *
 * Use when you need editor-safe block markup (any block, any plugin) and
 * can't rely on hand-authored PHP because the block's save.js has logic
 * with no PHP equivalent (EmbedPress, Stackable, complex layouts, etc.).
 *
 * Pattern:
 *   1. Login as admin
 *   2. Land on a real editor URL so wp.blocks + plugin block bundles load
 *   3. Build blocks with wp.blocks.createBlock(name, attrs)
 *   4. wp.blocks.serialize(blocks) → byte-perfect HTML
 *   5. POST to /wp/v2/pages/<id> (or posts/<id>) to persist
 *
 * Customize the BLOCKS array and POST_ID below for your case.
 */

const { runInEditor } = require('../lib/runner.js');

// ---- Customize this -------------------------------------------------------
const POST_ID = 2213; // existing page to overwrite, or omit to create new
const LANDING_URL = `http://localhost:8188/wp-admin/post.php?post=${POST_ID}&action=edit`;

// Block recipes — each entry is { name, attrs, innerBlocks? }
// All standard wp.blocks.createBlock arguments.
const BLOCKS = [
    { name: 'core/heading', attrs: { level: 1, content: 'Hello from wp-pilot' } },
    { name: 'core/paragraph', attrs: { content: 'This page was authored via wp.blocks.serialize().' } },
    // Example: EmbedPress block with cinematic preview
    // The runner will fetch the real embedHTML via the editor's REST endpoint.
    {
        name: 'embedpress/embedpress',
        attrs: {
            url: 'https://www.youtube.com/watch?v=b9EkMc79ZSU',
            editingURL: false,
            cannotEmbed: false,
            cinematicPreview: true,
            cinematicPreviewStyle: 'netflix-hero',
            cinematicPreviewTitle: 'Stranger Things',
        },
        // Set fetchEmbedHTML so the runner populates `embedHTML` for you
        fetchEmbedHTML: true,
    },
];
// --------------------------------------------------------------------------

(async () => {
    const result = await runInEditor({
        url: LANDING_URL,
        input: { blocks: BLOCKS, postId: POST_ID },
        evaluate: async ({ blocks, postId }) => {
            const built = [];
            for (const b of blocks) {
                let attrs = { ...b.attrs };

                // For EmbedPress / core/embed-style blocks: fetch the real
                // embed HTML via the editor's own oEmbed endpoint.
                if (b.fetchEmbedHTML && attrs.url) {
                    try {
                        const res = await wp.apiFetch({
                            path: '/embedpress/v1/oembed/embedpress',
                            method: 'POST',
                            data: { url: attrs.url, width: 600, height: 600 },
                        });
                        attrs.embedHTML = res.embed || '';
                        attrs.providerName = res.provider_name || attrs.providerName || '';
                    } catch (e) {
                        // fall back to core oEmbed
                        const res = await wp.apiFetch({
                            path: `/oembed/1.0/proxy?url=${encodeURIComponent(attrs.url)}`,
                        });
                        attrs.embedHTML = res?.html || '';
                        attrs.providerName = res?.provider_name || '';
                    }
                }

                const innerBlocks = (b.innerBlocks || []).map((ib) =>
                    wp.blocks.createBlock(ib.name, ib.attrs || {}, ib.innerBlocks || [])
                );
                built.push(wp.blocks.createBlock(b.name, attrs, innerBlocks));
            }

            const content = wp.blocks.serialize(built);

            await wp.apiFetch({
                path: `/wp/v2/pages/${postId}`,
                method: 'POST',
                data: { content },
            });

            return { ok: true, blockCount: built.length, contentLength: content.length };
        },
    });
    console.log(JSON.stringify(result, null, 2));
})();
