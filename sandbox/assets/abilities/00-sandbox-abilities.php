<?php
/**
 * Sandbox Abilities — in-instance WP Abilities for AI agents (spec 003).
 *
 * Registers `sandbox/*` abilities on the WordPress Abilities API (WP 6.9+).
 * Re-implemented against public WP/PHP APIs — NOT derived from any AGPL source.
 * Provisioned per-instance by the Sandbox; dev/staging only.
 *
 * MVP slice: the `sandbox/execute-php` ability (US1). The mcp-adapter MCP-server
 * exposure, file abilities, discover override, and crash-recovery loader land in
 * later slices.
 */

if (!defined('ABSPATH')) {
    exit;
}

// Hard version gate — no-op (with a log notice) on WP without the Abilities API.
if (!function_exists('wp_register_ability')) {
    if (function_exists('error_log')) {
        error_log('[sandbox-abilities] WordPress Abilities API not available; layer inactive.');
    }
    return;
}

define('SANDBOX_ABILITIES_MAX_EXEC', 30); // hard execution-time cap (seconds), spec FR-003.

/** Per-instance enable flag (default on for disposable Sandbox instances). */
function sandbox_abilities_enabled(): bool
{
    return (bool) apply_filters('sandbox_abilities_enabled', get_option('sandbox_abilities_enabled', '1') === '1');
}

/** Shared permission gate: logged-in user with manage_options (spec FR-008). */
function sandbox_abilities_permission_callback()
{
    if (!sandbox_abilities_enabled()) {
        return false;
    }
    return is_user_logged_in() && current_user_can('manage_options');
}

/** Resolve a path inside ABSPATH; reject escapes (incl. symlink). Returns string|WP_Error. */
function sandbox_abilities_resolve_path($path, bool $must_exist = false)
{
    $path = (string) $path;
    if ($path === '') {
        return new WP_Error('invalid_path', 'Empty path.');
    }
    if ($path[0] !== '/') {
        $path = rtrim(ABSPATH, '/') . '/' . ltrim($path, '/');
    }
    $base = realpath(ABSPATH) ?: rtrim(ABSPATH, '/');
    if ($must_exist) {
        $real = realpath($path);
        if ($real === false) {
            return new WP_Error('path_not_found', 'Path does not exist: ' . $path);
        }
    } else {
        $parent = realpath(dirname($path));
        $real = ($parent !== false) ? $parent . '/' . basename($path) : $path;
    }
    if (strpos($real, $base) !== 0) {
        return new WP_Error('path_outside_base', 'Path is outside the WordPress root.');
    }
    if (is_link($path)) {
        return new WP_Error('symlink_rejected', 'Symlinked paths are not allowed.');
    }
    return $real;
}

/** The only place new .php files may be created. */
function sandbox_abilities_sandbox_code_dir(): string
{
    return rtrim(WP_CONTENT_DIR, '/') . '/sandbox-code/';
}

/** New .php files must live under sandbox-code/. Returns true|WP_Error. */
function sandbox_abilities_check_php_sandbox(string $resolved, bool $is_new)
{
    if (!$is_new || strtolower((string) pathinfo($resolved, PATHINFO_EXTENSION)) !== 'php') {
        return true;
    }
    $sc = realpath(sandbox_abilities_sandbox_code_dir()) ?: sandbox_abilities_sandbox_code_dir();
    if (strpos($resolved, rtrim($sc, '/')) !== 0) {
        return new WP_Error('php_sandbox_required', 'New .php files must be created under wp-content/sandbox-code/.');
    }
    return true;
}

// WP 6.9: categories MUST be registered on their own (earlier) hook, before abilities.
add_action('wp_abilities_api_categories_init', 'sandbox_abilities_register_category');
add_action('wp_abilities_api_init', 'sandbox_abilities_register');

// Expose the abilities over MCP via the bundled wordpress/mcp-adapter (if vendored).
$sandbox_mcp_autoload = __DIR__ . '/sandbox-abilities/vendor/autoload.php';
if (sandbox_abilities_enabled() && is_readable($sandbox_mcp_autoload)) {
    require_once $sandbox_mcp_autoload;
    if (class_exists('WP\\MCP\\Core\\McpAdapter')) {
        \WP\MCP\Core\McpAdapter::instance();
        add_action('mcp_adapter_init', 'sandbox_abilities_register_mcp_server');
    }
}

/** Register the Sandbox MCP server (route /wp-json/sandbox/mcp) exposing our abilities. */
function sandbox_abilities_register_mcp_server($adapter): void
{
    if (!is_object($adapter) || !method_exists($adapter, 'create_server')) {
        return;
    }
    $adapter->create_server(
        'sandbox',                 // server id
        'sandbox',                 // REST namespace
        'mcp',                     // REST route  → /wp-json/sandbox/mcp
        'WPDeveloper Sandbox',     // server name
        'In-instance WordPress abilities for AI agents (dev/staging only).',
        '1.0.0',
        [\WP\MCP\Transport\HttpTransport::class],
        null,                      // error handler → adapter default
        null,                      // observability → adapter default
        [
            'sandbox/execute-php',
            'sandbox/read-file',
            'sandbox/write-file',
            'sandbox/edit-file',
            'sandbox/list-directory',
        ]
    );
}

function sandbox_abilities_register_category(): void
{
    if (!sandbox_abilities_enabled() || wp_has_ability_category('sandbox')) {
        return;
    }
    wp_register_ability_category('sandbox', [
        'label'       => __('Sandbox', 'sandbox'),
        'description' => __('WPDeveloper Sandbox agent abilities (dev/staging only).', 'sandbox'),
    ]);
}

function sandbox_abilities_register(): void
{
    if (!sandbox_abilities_enabled()) {
        return;
    }

    wp_register_ability('sandbox/execute-php', [
        'label'       => __('Execute PHP Code', 'sandbox'),
        'description' => __('Executes PHP in the live WordPress runtime. Full WP environment ($wpdb, all functions, loaded plugins). Returns the return value, echoed output, and captured warnings/notices/deprecations.', 'sandbox'),
        'category'    => 'sandbox',
        'input_schema' => [
            'type'       => 'object',
            'properties' => [
                'code' => [
                    'type'        => 'string',
                    'description' => 'PHP code WITHOUT <?php tags. Use "return $value;" to return data. Do not call exit()/die() and avoid infinite loops (30s cap).',
                    'minLength'   => 1,
                ],
            ],
            'required' => ['code'],
            'additionalProperties' => false,
        ],
        'output_schema' => [
            'type'       => 'object',
            'properties' => [
                'success'            => ['type' => 'boolean'],
                'return_value'       => [],
                'output'             => ['type' => 'string'],
                'errors'             => ['type' => 'array'],
                'error_message'      => ['type' => 'string'],
                'error_class'        => ['type' => 'string'],
                'execution_time_ms'  => ['type' => 'number'],
            ],
        ],
        'execute_callback'    => 'sandbox_abilities_execute_php',
        'permission_callback' => 'sandbox_abilities_permission_callback',
        'meta' => [
            'show_in_rest' => true,
            'mcp'          => ['public' => true],
            'annotations'  => ['readonly' => false, 'destructive' => true, 'idempotent' => false],
        ],
    ]);

    $rest_meta = ['show_in_rest' => true, 'mcp' => ['public' => true]];

    wp_register_ability('sandbox/read-file', [
        'label'       => __('Read File', 'sandbox'),
        'description' => __('Read a file under the WordPress root (ABSPATH-jailed).', 'sandbox'),
        'category'    => 'sandbox',
        'input_schema'  => ['type' => 'object', 'properties' => ['path' => ['type' => 'string']], 'required' => ['path'], 'additionalProperties' => false],
        'output_schema' => ['type' => 'object'],
        'execute_callback'    => 'sandbox_abilities_read_file',
        'permission_callback' => 'sandbox_abilities_permission_callback',
        'meta' => $rest_meta + ['annotations' => ['readonly' => true, 'destructive' => false, 'idempotent' => true]],
    ]);

    wp_register_ability('sandbox/write-file', [
        'label'       => __('Write File', 'sandbox'),
        'description' => __('Write a file under the WordPress root. New .php is confined to wp-content/sandbox-code/.', 'sandbox'),
        'category'    => 'sandbox',
        'input_schema'  => ['type' => 'object', 'properties' => [
            'path' => ['type' => 'string'], 'content' => ['type' => 'string'],
            'mode' => ['type' => 'string', 'enum' => ['overwrite', 'append']],
            'create_directories' => ['type' => 'boolean'],
        ], 'required' => ['path', 'content'], 'additionalProperties' => false],
        'output_schema' => ['type' => 'object'],
        'execute_callback'    => 'sandbox_abilities_write_file',
        'permission_callback' => 'sandbox_abilities_permission_callback',
        'meta' => $rest_meta + ['annotations' => ['readonly' => false, 'destructive' => true, 'idempotent' => false]],
    ]);

    wp_register_ability('sandbox/edit-file', [
        'label'       => __('Edit File', 'sandbox'),
        'description' => __('Replace a string in a file under the WordPress root.', 'sandbox'),
        'category'    => 'sandbox',
        'input_schema'  => ['type' => 'object', 'properties' => [
            'path' => ['type' => 'string'], 'old_string' => ['type' => 'string'],
            'new_string' => ['type' => 'string'], 'replace_all' => ['type' => 'boolean'],
        ], 'required' => ['path', 'old_string', 'new_string'], 'additionalProperties' => false],
        'output_schema' => ['type' => 'object'],
        'execute_callback'    => 'sandbox_abilities_edit_file',
        'permission_callback' => 'sandbox_abilities_permission_callback',
        'meta' => $rest_meta + ['annotations' => ['readonly' => false, 'destructive' => true, 'idempotent' => false]],
    ]);

    wp_register_ability('sandbox/list-directory', [
        'label'       => __('List Directory', 'sandbox'),
        'description' => __('List entries of a directory under the WordPress root.', 'sandbox'),
        'category'    => 'sandbox',
        'input_schema'  => ['type' => 'object', 'properties' => ['path' => ['type' => 'string']], 'required' => ['path'], 'additionalProperties' => false],
        'output_schema' => ['type' => 'object'],
        'execute_callback'    => 'sandbox_abilities_list_directory',
        'permission_callback' => 'sandbox_abilities_permission_callback',
        'meta' => $rest_meta + ['annotations' => ['readonly' => true, 'destructive' => false, 'idempotent' => true]],
    ]);
}

/**
 * execute-php callback: eval with output-buffer + non-fatal-diagnostic capture,
 * a hard time cap, Throwable catch, and a JSON-safe return value.
 *
 * @param array $input {code:string}
 * @return array
 */
function sandbox_abilities_execute_php($input): array
{
    $code   = (string) ($input['code'] ?? '');
    $errors = [];

    $levels = [
        E_WARNING => 'Warning', E_NOTICE => 'Notice', E_DEPRECATED => 'Deprecated',
        E_USER_WARNING => 'User Warning', E_USER_NOTICE => 'User Notice', E_USER_DEPRECATED => 'User Deprecated',
    ];
    set_error_handler(static function ($no, $str, $file, $line) use (&$errors, $levels) {
        $errors[] = ['type' => $levels[$no] ?? ('Unknown(' . (int) $no . ')'), 'message' => $str, 'file' => $file, 'line' => $line];
        return true;
    });

    $orig_limit = (int) ini_get('max_execution_time');
    set_time_limit(SANDBOX_ABILITIES_MAX_EXEC);

    ob_start();
    $start = microtime(true);
    $return_value = null;
    $success = true;
    $error_message = null;
    $error_class = null;

    try {
        // @phpcs:ignore -- arbitrary code execution is this ability's entire purpose (dev/staging only).
        $return_value = eval($code);
    } catch (\Throwable $e) {
        $success = false;
        $error_message = $e->getMessage();
        $error_class = get_class($e);
    }

    $ms = round((microtime(true) - $start) * 1000, 2);
    $output = ob_get_clean();
    restore_error_handler();
    set_time_limit($orig_limit);

    if ($return_value !== null && wp_json_encode($return_value) === false) {
        $return_value = print_r($return_value, true);
    }

    $result = [
        'success'           => $success,
        'return_value'      => $return_value,
        'output'            => $output,
        'errors'            => $errors,
        'execution_time_ms' => $ms,
    ];
    if ($error_message !== null) {
        $result['error_message'] = $error_message;
        $result['error_class']   = $error_class;
    }
    return $result;
}

function sandbox_abilities_read_file($input)
{
    $r = sandbox_abilities_resolve_path($input['path'] ?? '', true);
    if (is_wp_error($r)) {
        return $r;
    }
    if (!is_file($r)) {
        return new WP_Error('not_a_file', 'Not a file: ' . $r);
    }
    $c = (string) file_get_contents($r);
    return ['path' => $r, 'content' => $c, 'size' => strlen($c)];
}

function sandbox_abilities_write_file($input)
{
    $r = sandbox_abilities_resolve_path($input['path'] ?? '', false);
    if (is_wp_error($r)) {
        return $r;
    }
    $is_new = !file_exists($r);
    $chk = sandbox_abilities_check_php_sandbox($r, $is_new);
    if (is_wp_error($chk)) {
        return $chk;
    }
    if (($input['create_directories'] ?? true) && !is_dir(dirname($r))) {
        wp_mkdir_p(dirname($r));
    }
    $flags = LOCK_EX | ((($input['mode'] ?? 'overwrite') === 'append') ? FILE_APPEND : 0);
    $n = file_put_contents($r, (string) ($input['content'] ?? ''), $flags);
    if ($n === false) {
        return new WP_Error('write_failed', 'Failed to write: ' . $r);
    }
    return ['path' => $r, 'bytes_written' => $n, 'created' => $is_new, 'size' => filesize($r)];
}

function sandbox_abilities_edit_file($input)
{
    $r = sandbox_abilities_resolve_path($input['path'] ?? '', true);
    if (is_wp_error($r)) {
        return $r;
    }
    $old = (string) ($input['old_string'] ?? '');
    $new = (string) ($input['new_string'] ?? '');
    $c = (string) file_get_contents($r);
    $cnt = 0;
    if (!empty($input['replace_all'])) {
        $c = str_replace($old, $new, $c, $cnt);
    } else {
        $pos = strpos($c, $old);
        if ($pos !== false) {
            $c = substr_replace($c, $new, $pos, strlen($old));
            $cnt = 1;
        }
    }
    if ($cnt === 0) {
        return new WP_Error('no_match', 'old_string not found in file.');
    }
    file_put_contents($r, $c, LOCK_EX);
    return ['path' => $r, 'replacements' => $cnt, 'size' => filesize($r)];
}

function sandbox_abilities_list_directory($input)
{
    $r = sandbox_abilities_resolve_path($input['path'] ?? '', true);
    if (is_wp_error($r)) {
        return $r;
    }
    if (!is_dir($r)) {
        return new WP_Error('not_a_dir', 'Not a directory: ' . $r);
    }
    $entries = [];
    foreach (scandir($r) as $e) {
        if ($e === '.' || $e === '..') {
            continue;
        }
        $p = $r . '/' . $e;
        $entries[] = ['name' => $e, 'type' => is_dir($p) ? 'dir' : 'file', 'size' => is_file($p) ? filesize($p) : 0];
    }
    return ['path' => $r, 'entries' => $entries];
}

/**
 * Crash-recovery loader for persistent AI-written PHP (spec 003 FR-007).
 * Loads wp-content/sandbox-code/*.php behind a shutdown handler that writes a
 * .crashed marker on fatal and drops into safe mode (skip ALL sandbox files).
 * Runs independently of the enable flag — existing sandbox files keep loading
 * even when the abilities layer is off; only the write ability (gated) creates them.
 */
(static function () {
    $dir = sandbox_abilities_sandbox_code_dir();
    if (!is_dir($dir)) {
        return;
    }
    $crashed = $dir . '.crashed';
    $loading = $dir . '.loading';

    // Recovery: a stale .loading means a PRIOR request started loading sandbox
    // files and never finished (it fataled mid-require). Promote to .crashed.
    // This does NOT depend on our shutdown handler firing — WP registers its own
    // fatal handler before mu-plugins load and can pre-empt later shutdown
    // callbacks, so the marker-file handshake is the reliable signal.
    if (!file_exists($crashed) && file_exists($loading)) {
        $bad = trim((string) @file_get_contents($loading));
        file_put_contents($crashed, (string) wp_json_encode([
            'sandbox_file' => $bad,
            'message'      => 'A sandbox-code file caused a fatal error during load.',
        ]), LOCK_EX);
        @unlink($loading);
    }

    if (file_exists($crashed)) {
        add_action('admin_notices', static function () use ($crashed) {
            if (!current_user_can('manage_options')) {
                return;
            }
            echo '<div class="notice notice-error"><p><strong>'
                . esc_html__('Sandbox safe mode is active.', 'sandbox') . '</strong> '
                . esc_html__('A sandbox-code file caused a fatal error; all sandbox-code files are disabled. Fix or remove it, then delete the .crashed marker to resume.', 'sandbox')
                . ' <code>' . esc_html($crashed) . '</code></p></div>';
        });
    }

    if (file_exists($crashed) || (($_GET['sb_safe_mode'] ?? null) === '1')) {
        @unlink($loading);
        return;
    }

    $files = glob($dir . '*.php');
    if (!$files) {
        return;
    }

    // Fast path: if our shutdown handler DOES get to run, mark crashed immediately.
    $current = null;
    register_shutdown_function(static function () use ($crashed, $loading, &$current) {
        if ($current === null) {
            return;
        }
        $e = error_get_last();
        if ($e === null || !($e['type'] & (E_ERROR | E_PARSE | E_CORE_ERROR | E_COMPILE_ERROR))) {
            return;
        }
        $e['sandbox_file'] = $current;
        @file_put_contents($crashed, (string) wp_json_encode($e), LOCK_EX);
        @unlink($loading);
    });

    foreach ($files as $f) {
        $current = $f;
        // Marker written BEFORE require; if the require fatals, .loading persists
        // and the next request promotes it to .crashed (above).
        @file_put_contents($loading, $f, LOCK_EX);
        require_once $f;
    }
    $current = null;
    @unlink($loading); // clean load → clear the in-progress marker
})();
