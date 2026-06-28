<?php
/**
 * Sandbox headless Gutenberg block registry dump (spec 012, T003).
 *
 * A finalizer-style admin page that boots the block editor runtime
 * (registerCoreBlocks + enqueue_block_editor_assets) and serializes
 * wp.blocks.getBlockTypes() → name→{attributes,supports,dynamic} JSON,
 * persisting it to WP_CONTENT_DIR/sandbox-schema-dump/gutenberg.json
 * for the host to read.
 *
 * Driven by the `sb schema-catalog generate` headless flow via the
 * `visit` MCP tool (auto-login). The generator reads the dumped file
 * from the host filesystem after the DOM signals done. Dev/staging only.
 */

if (!defined('ABSPATH')) {
    return;
}

define('SANDBOX_SCHEMA_DUMP_DIR',  WP_CONTENT_DIR . '/sandbox-schema-dump');
define('SANDBOX_SCHEMA_DUMP_FILE', SANDBOX_SCHEMA_DUMP_DIR . '/gutenberg.json');

// EB's first-run setup wizard redirects every admin load until dismissed,
// hijacking headless automation. Mark it shown. Idempotent; mirrors the
// same guard in 00-sandbox-eb-finalizer.php.
add_action('admin_init', function () {
    if (!get_option('essential_blocks_quick_setup_shown')) {
        update_option('essential_blocks_quick_setup_shown', true);
    }
}, 0);

/* ------------------------------ Admin page -------------------------------- */

add_action('admin_menu', function () {
    add_menu_page(
        'Sandbox Schema Dump',
        'Sandbox Schema Dump',
        'manage_options',
        'sandbox-schema-dump',
        'sandbox_schema_dump_render_page',
        'dashicons-database-export',
        99
    );
});

add_action('admin_enqueue_scripts', function ($hook) {
    if (strpos((string) $hook, 'sandbox-schema-dump') === false) {
        return;
    }
    // Boot the block editor runtime so wp.blocks.getBlockTypes() is complete.
    // EB registers its blocks via enqueue_block_editor_assets; core blocks
    // need an explicit registerCoreBlocks() call in the JS runner.
    do_action('enqueue_block_editor_assets');

    // A src-less aggregator handle: its dependencies are loaded first, so
    // the attached inline script runs after all wp-* bundles are ready.
    wp_register_script('sandbox-schema-dump-run', false,
        ['wp-blocks', 'wp-block-library', 'wp-dom-ready', 'wp-api-fetch', 'wp-data'],
        null, true);
    wp_enqueue_script('sandbox-schema-dump-run');
    wp_localize_script('sandbox-schema-dump-run', 'SBDUMP', [
        'nonce' => wp_create_nonce('wp_rest'),
        'rest'  => '/sandbox/v1/schema-dump-store',
    ]);
    wp_add_inline_script('sandbox-schema-dump-run', sandbox_schema_dump_runner_js());
}, 100);

function sandbox_schema_dump_render_page(): void
{
    echo '<div class="wrap"><h1>Sandbox Schema Dump</h1>'
       . '<p>Headless Gutenberg block registry dump. Driven by the agent via visit.</p>'
       . '<div id="sandbox-schema-dump-log"></div></div>';
}

/** JS runner: boot wp.blocks, dump getBlockTypes(), POST to REST, mark done. */
function sandbox_schema_dump_runner_js(): string
{
    return <<<'JS'
wp.domReady(function () {
    var log = document.getElementById('sandbox-schema-dump-log');
    function status(msg) { if (log) { log.textContent = msg; } }
    function mark(count, err) {
        var d = document.createElement('div');
        d.id = err ? 'sandbox-schema-dump-error' : 'sandbox-schema-dump-done';
        d.setAttribute('data-count', String(count || 0));
        d.textContent = err ? ('ERROR: ' + err) : ('DONE (' + count + ' blocks)');
        document.body.appendChild(d);
    }

    // Core blocks aren't auto-registered outside the iframe editor; without
    // this, any createBlock() call would recurse into unregistered fallbacks.
    // EB blocks register via enqueue_block_editor_assets (server side).
    try {
        if (wp.blockLibrary && wp.blockLibrary.registerCoreBlocks
            && !wp.data.select('core/blocks').getBlockType('core/paragraph')) {
            wp.blockLibrary.registerCoreBlocks();
        }
    } catch (e) { /* best-effort */ }

    var types = wp.blocks.getBlockTypes();
    status('Collected ' + types.length + ' block types. Serialising…');

    var dump = {};
    types.forEach(function (bt) {
        dump[bt.name] = {
            attributes: bt.attributes || {},
            supports:   bt.supports   || {},
        };
    });

    status('Storing ' + Object.keys(dump).length + ' entries…');

    if (wp.apiFetch.use && window.SBDUMP && window.SBDUMP.nonce) {
        wp.apiFetch.use(wp.apiFetch.createNonceMiddleware(window.SBDUMP.nonce));
    }
    wp.apiFetch({
        path: (window.SBDUMP || {}).rest || '/sandbox/v1/schema-dump-store',
        method: 'POST',
        data: { dump: dump },
    })
    .then(function (r) { mark(Object.keys(dump).length); })
    .catch(function (e) { mark(0, String(e)); });
});
JS;
}

/* ------------------------------ REST store ------------------------------ */

add_action('rest_api_init', function () {
    register_rest_route('sandbox/v1', '/schema-dump-store', [
        'methods'             => 'POST',
        'permission_callback' => function () { return current_user_can('manage_options'); },
        'callback'            => 'sandbox_schema_dump_store_cb',
    ]);
});

function sandbox_schema_dump_store_cb(WP_REST_Request $req)
{
    $dump = $req->get_param('dump');
    if (!is_array($dump)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'dump must be a JSON object'], 400);
    }
    if (!is_dir(SANDBOX_SCHEMA_DUMP_DIR)) {
        wp_mkdir_p(SANDBOX_SCHEMA_DUMP_DIR);
    }
    $written = file_put_contents(SANDBOX_SCHEMA_DUMP_FILE, wp_json_encode($dump));
    if ($written === false) {
        return new WP_REST_Response(['ok' => false, 'error' => 'write failed'], 500);
    }
    return new WP_REST_Response(['ok' => true, 'count' => count($dump), 'bytes' => $written], 200);
}
