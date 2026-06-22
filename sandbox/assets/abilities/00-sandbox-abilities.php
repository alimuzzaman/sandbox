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

// WP 6.9: categories MUST be registered on their own (earlier) hook, before abilities.
add_action('wp_abilities_api_categories_init', 'sandbox_abilities_register_category');
add_action('wp_abilities_api_init', 'sandbox_abilities_register');

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
