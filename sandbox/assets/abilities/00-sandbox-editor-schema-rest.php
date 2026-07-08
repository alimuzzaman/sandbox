<?php
/**
 * Sandbox editor-schema REST convenience route (spec 011/012 follow-on).
 *
 * `sandbox_editor_schema()` (in sandbox-editor.php) is already reachable over
 * the network two ways: the generic WP Abilities REST controller
 * (`POST /wp-abilities/v1/abilities/sandbox%2Feditor-schema/run`, body
 * `{"input": {...}}`) and the full MCP JSON-RPC server (`/wp-json/sandbox/mcp`,
 * requires an `initialize` handshake + session id). Both work but are heavier
 * than a plain lookup call needs.
 *
 * This adds a single plain GET route so any HTTP client/agent can query the
 * block/widget attribute schema with a one-line request and query-string
 * params — no JSON-RPC envelope, no ability-name URL-encoding, no session
 * handshake.
 *
 * UNAUTHENTICATED BY DESIGN — unlike every other sandbox REST route, this one
 * does NOT require a logged-in manage_options user. It is read-only schema
 * introspection (block/widget attribute shapes — no post content, no user
 * data, no code execution, no writes), meant to be queried by another
 * machine on the same LAN without provisioning WP credentials for it. Still
 * gated by `sandbox_abilities_enabled()` (the same instance-wide kill switch
 * as the rest of the abilities layer) so `wp option update
 * sandbox_abilities_enabled 0` turns it off along with everything else.
 * Dev/staging only — this instance's WP port is reachable from any device on
 * the local network by design (Docker publishes 0.0.0.0:<port>), so treat
 * the whole instance, not just this route, as LAN-exposed.
 *
 * GET /wp-json/sandbox/v1/editor-schema
 *   ?builder=gutenberg|elementor   (required)
 *   &name=<block-or-widget-name>   (optional — omit for the full listing)
 *   &search=<query>                (optional — filter attributes/controls)
 *   &eb_only=1                     (optional — Gutenberg listing: EB blocks only)
 *   &types=all|widgets|elements    (optional — Elementor listing filter)
 *   &limit=<n>                     (optional — Elementor listing page size)
 *   &variants=<key>                (optional — Elementor per-name variant lookup)
 *   &source_root=<path>            (optional — EB source checkout override)
 *   &include_variants=1            (optional — Gutenberg: include MOB/TAB/hov_
 *                                     variant attrs instead of hiding them)
 *   &full=1                        (optional — Gutenberg, with `name`: include full
 *                                     definitions for global/style attrs too, not
 *                                     just their names)
 *
 * GET /wp-json/sandbox/v1/editor-schema/docs
 *   Serves editor-schema-api.md (this same directory) as JSON, so an agent
 *   can self-bootstrap over the network without filesystem access to this
 *   repo. Keep the two in sync: update the .md when the response shape or
 *   query params change.
 */

if (!defined('ABSPATH')) {
    return;
}

add_action('rest_api_init', function () {
    if (!function_exists('sandbox_editor_schema')) {
        return; // spec 005 helpers didn't load on this instance.
    }
    register_rest_route('sandbox/v1', '/editor-schema', [
        'methods'             => 'GET',
        'permission_callback' => 'sandbox_editor_schema_rest_permission',
        'callback'            => 'sandbox_editor_schema_rest_cb',
    ]);
    register_rest_route('sandbox/v1', '/editor-schema/docs', [
        'methods'             => 'GET',
        'permission_callback' => 'sandbox_editor_schema_rest_permission',
        'callback'            => 'sandbox_editor_schema_docs_rest_cb',
    ]);
});

/** No login/capability check — only the instance-wide abilities kill switch. */
function sandbox_editor_schema_rest_permission()
{
    return function_exists('sandbox_abilities_enabled') ? sandbox_abilities_enabled() : true;
}

function sandbox_editor_schema_rest_cb(WP_REST_Request $req)
{
    $input = [
        'builder'          => (string) $req->get_param('builder'),
        'name'             => (string) $req->get_param('name'),
        'search'           => $req->get_param('search'),
        'eb_only'          => $req->get_param('eb_only'),
        'types'            => $req->get_param('types'),
        'limit'            => $req->get_param('limit'),
        'variants'         => $req->get_param('variants'),
        'source_root'      => $req->get_param('source_root'),
        'include_variants' => $req->get_param('include_variants'),
        'full'             => $req->get_param('full'),
    ];
    // Drop unset optional params rather than passing through empty-string/null —
    // sandbox_editor_schema() distinguishes "absent" (isset checks) from "present
    // but empty" for several of these (search, variants, types, limit).
    $input = array_filter($input, static fn ($v) => $v !== null && $v !== '');

    $result = sandbox_editor_schema($input);
    if (is_wp_error($result)) {
        return $result;
    }
    return new WP_REST_Response($result, 200);
}

/** Serve editor-schema-api.md (this mu-plugin's own directory) as JSON. */
function sandbox_editor_schema_docs_rest_cb(WP_REST_Request $req)
{
    $path = __DIR__ . '/editor-schema-api.md';
    if (!is_readable($path)) {
        return new WP_Error('docs_not_found', 'Editor Schema API docs are not available.', ['status' => 404]);
    }
    return new WP_REST_Response([
        'format'  => 'markdown',
        'title'   => 'Editor Schema API',
        'content' => file_get_contents($path),
    ], 200);
}
