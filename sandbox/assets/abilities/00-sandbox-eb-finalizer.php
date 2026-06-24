<?php
/**
 * Sandbox EB real-editor finalizer (spec 005, US5 / T009-T011).
 *
 * Static / third-party Gutenberg blocks must be serialized through their REAL
 * JS save() or WP block validation flags them "invalid/recovery". Hand-authored
 * PHP markup can't reproduce that. This mu-plugin closes the gap headlessly:
 *
 *   1. sandbox_eb_finalizer_enqueue($post_id, $spec) queues an attribute-level
 *      block spec and returns a job id (the agent polls it).
 *   2. A hidden admin page (admin.php?page=sandbox-eb-finalizer) loads the block
 *      editor runtime (wp.blocks + core/EB block registrations) and, for each
 *      pending job, createBlock(...)->serialize() to produce canonical save
 *      markup, then POSTs it back to a REST route that appends it to the post.
 *   3. The agent drives that page with the headless `visit` tool (auto-login)
 *      and polls sandbox_eb_finalizer_status($job_id) until done — no human step.
 *
 * Base-content-hash concurrency: each job records the post hash at enqueue; the
 * apply step rejects if the post changed underneath it. Dev/staging only.
 */

if (!defined('ABSPATH')) {
    return;
}

const SANDBOX_EB_FINALIZE_OPTION = 'sandbox_eb_finalize_queue';

// EB's first-run "quick setup" wizard redirects every admin load until dismissed,
// which hijacks headless automation. Mark it shown so the finalizer page is
// always reachable. Idempotent; dev/staging only.
add_action('admin_init', function () {
    if (!get_option('essential_blocks_quick_setup_shown')) {
        update_option('essential_blocks_quick_setup_shown', true);
    }
}, 0);

/** Load the job queue (job_id => job). */
function sandbox_eb_finalizer_queue(): array
{
    return (array) get_option(SANDBOX_EB_FINALIZE_OPTION, []);
}

function sandbox_eb_finalizer_save_queue(array $q): void
{
    update_option(SANDBOX_EB_FINALIZE_OPTION, $q, false);
}

/**
 * Queue a block spec for headless finalization.
 * @param int          $post_id
 * @param array|string $spec  block spec {name, attributes?, inner_html?, inner_blocks?} or a block name
 * @return array {job_id, status, post_id, finalizer_url, poll}
 */
function sandbox_eb_finalizer_enqueue(int $post_id, $spec): array
{
    if (is_string($spec)) {
        $spec = ['name' => $spec];
    }
    $spec = (array) $spec;
    $job_id = 'ebf_' . bin2hex(random_bytes(6));
    $post   = get_post($post_id);
    $q = sandbox_eb_finalizer_queue();
    $q[$job_id] = [
        'job_id'     => $job_id,
        'post_id'    => $post_id,
        'block_spec' => $spec,
        'status'     => 'queued',
        'base_hash'  => $post ? md5($post->post_content) : '',
        'error'      => null,
        'result_hash' => null,
    ];
    sandbox_eb_finalizer_save_queue($q);
    return [
        'job_id'        => $job_id,
        'status'        => 'queued',
        'post_id'       => $post_id,
        'finalizer_url' => admin_url('admin.php?page=sandbox-eb-finalizer'),
        'poll'          => "sandbox_eb_finalizer_status('$job_id')",
        'note'          => 'Drive finalizer_url headlessly (visit), then poll status until done.',
    ];
}

/** Poll a job's status. */
function sandbox_eb_finalizer_status(string $job_id)
{
    $q = sandbox_eb_finalizer_queue();
    if (!isset($q[$job_id])) {
        return new WP_Error('not_found', "finalizer job '$job_id' not found");
    }
    return $q[$job_id];
}

/* ----------------------------- Admin page -------------------------------- */

add_action('admin_menu', function () {
    add_menu_page(
        'Sandbox EB Finalizer',
        'Sandbox EB Finalizer',
        'edit_posts',
        'sandbox-eb-finalizer',
        'sandbox_eb_finalizer_render_page',
        'dashicons-buddicons-replies',
        99
    );
});

add_action('admin_enqueue_scripts', function ($hook) {
    if (strpos((string) $hook, 'sandbox-eb-finalizer') === false) {
        return;
    }
    // Block editor runtime. EB registers its blocks on enqueue_block_editor_assets;
    // core blocks need an explicit registerCoreBlocks() (see the runner below).
    do_action('enqueue_block_editor_assets');

    // A src-less aggregator handle: depends on every wp-* bundle the runner needs,
    // so the attached inline script is guaranteed to print AFTER them (a raw
    // footer <script> runs before the enqueued bundles load -> "wp is not defined").
    wp_register_script('sandbox-eb-finalizer-run', false,
        ['wp-blocks', 'wp-block-library', 'wp-dom-ready', 'wp-api-fetch', 'wp-data'], null, true);
    wp_enqueue_script('sandbox-eb-finalizer-run');

    $pending = array_values(array_filter(sandbox_eb_finalizer_queue(), function ($j) {
        return ($j['status'] ?? '') === 'queued';
    }));
    wp_localize_script('sandbox-eb-finalizer-run', 'SBEBF', [
        'jobs'  => $pending,
        'nonce' => wp_create_nonce('wp_rest'),
        'rest'  => '/sandbox/v1/eb-finalize-apply',
    ]);
    wp_add_inline_script('sandbox-eb-finalizer-run', sandbox_eb_finalizer_runner_js());
}, 100);

function sandbox_eb_finalizer_render_page(): void
{
    echo '<div class="wrap"><h1>Sandbox EB Finalizer</h1>'
       . '<p>Headless block serialization queue. This page is driven by the agent.</p>'
       . '<div id="sandbox-finalizer-log"></div></div>';
    // The serializer runs from a footer inline script (assets are enqueued above).
}

/** The headless runner JS: serialize each queued spec via real wp.blocks, POST back. */
function sandbox_eb_finalizer_runner_js(): string
{
    return <<<'JS'
wp.domReady(function () {
    var cfg = window.SBEBF || { jobs: [], rest: '', nonce: '' };
    var log = document.getElementById('sandbox-finalizer-log');
    if (cfg.nonce && wp.apiFetch && wp.apiFetch.createNonceMiddleware) {
        wp.apiFetch.use(wp.apiFetch.createNonceMiddleware(cfg.nonce));
    }
    // Core blocks aren't auto-registered outside the iframe editor; without this,
    // createBlock() infinitely recurses building an unregistered fallback. EB
    // blocks register via enqueue_block_editor_assets (server side).
    try {
        if (wp.blockLibrary && wp.blockLibrary.registerCoreBlocks
            && !wp.data.select('core/blocks').getBlockType('core/paragraph')) {
            wp.blockLibrary.registerCoreBlocks();
        }
    } catch (e) { /* best-effort */ }
    function build(spec) {
        var inner = (spec.inner_blocks || []).map(build);
        return wp.blocks.createBlock(spec.name, spec.attributes || {}, inner);
    }
    function mark(done) {
        var d = document.createElement('div');
        d.id = 'sandbox-finalizer-done';
        d.setAttribute('data-count', String(done));
        d.textContent = 'DONE';
        document.body.appendChild(d);
    }
    var jobs = cfg.jobs || [];
    var chain = Promise.resolve();
    jobs.forEach(function (job) {
        chain = chain.then(function () {
            var data = { job_id: job.job_id };
            try {
                data.serialized = wp.blocks.serialize(build(job.block_spec));
                data.post_id = job.post_id;
            } catch (e) {
                data.error = String(e);
            }
            return wp.apiFetch({ path: cfg.rest, method: 'POST', data: data })
                .then(function (r) {
                    if (log) { log.insertAdjacentHTML('beforeend', '<p>' + job.job_id + ': ' + (r && r.status) + '</p>'); }
                })
                .catch(function (e) {
                    if (log) { log.insertAdjacentHTML('beforeend', '<p>' + job.job_id + ': apply-failed ' + e + '</p>'); }
                });
        });
    });
    chain.then(function () { mark(jobs.length); });
});
JS;
}

/* ------------------------------- REST apply ------------------------------ */

add_action('rest_api_init', function () {
    register_rest_route('sandbox/v1', '/eb-finalize-apply', [
        'methods'             => 'POST',
        'permission_callback' => function () {
            return current_user_can('edit_posts');
        },
        'callback'            => 'sandbox_eb_finalizer_apply_cb',
    ]);
});

function sandbox_eb_finalizer_apply_cb(WP_REST_Request $req)
{
    $job_id = (string) $req->get_param('job_id');
    $q = sandbox_eb_finalizer_queue();
    if (!isset($q[$job_id])) {
        return new WP_REST_Response(['status' => 'unknown-job'], 404);
    }
    $job = &$q[$job_id];

    if ($req->get_param('error')) {
        $job['status'] = 'error';
        $job['error']  = (string) $req->get_param('error');
        sandbox_eb_finalizer_save_queue($q);
        return new WP_REST_Response(['status' => 'error'], 200);
    }

    $post_id    = (int) $req->get_param('post_id');
    $serialized = (string) $req->get_param('serialized');
    $post = get_post($post_id);
    if (!$post) {
        $job['status'] = 'error';
        $job['error']  = 'post gone';
        sandbox_eb_finalizer_save_queue($q);
        return new WP_REST_Response(['status' => 'error'], 200);
    }
    // base-content-hash concurrency: refuse if the post changed since enqueue.
    if (!empty($job['base_hash']) && md5($post->post_content) !== $job['base_hash']) {
        $job['status'] = 'conflict';
        $job['error']  = 'base content changed since enqueue';
        sandbox_eb_finalizer_save_queue($q);
        return new WP_REST_Response(['status' => 'conflict'], 200);
    }
    $content = $post->post_content;
    $content = ($content === '' ? '' : $content . "\n\n") . $serialized;
    wp_update_post(['ID' => $post_id, 'post_content' => $content]);
    $new  = get_post($post_id);
    $hash = md5($new->post_content);
    $job['status']      = 'done';
    $job['result_hash'] = $hash;
    // Re-base sibling jobs on the same post so a batch of appends chains, while a
    // genuine external edit (before this drive) is still caught by the first job.
    foreach ($q as &$sib) {
        if ($sib['status'] === 'queued' && (int) $sib['post_id'] === $post_id) {
            $sib['base_hash'] = $hash;
        }
    }
    unset($sib);
    sandbox_eb_finalizer_save_queue($q);
    return new WP_REST_Response(['status' => 'done'], 200);
}
