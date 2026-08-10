<?php
/**
 * Sandbox Query Monitor capture (spec 007) — headless QM data to wp-content/qm.jsonl.
 *
 * On shutdown (after QM has collected), reads QM_Collectors directly (bypassing
 * the view_query_monitor cap gate + the partial REST/header outputters) and appends
 * one JSON line per request. `qm_capture(url)` fires a real request then reads the
 * last line. Local development only.
 */

if (!defined('ABSPATH')) {
    exit;
}
$sandbox_qm_environment = function_exists('wp_get_environment_type')
    ? wp_get_environment_type()
    : (defined('WP_ENVIRONMENT_TYPE') ? WP_ENVIRONMENT_TYPE : '');
if ('local' !== $sandbox_qm_environment) {
    return;
}
if (!defined('QM_HIDE_SELF')) {
    define('QM_HIDE_SELF', true);  // drop QM's own queries/hooks from the capture
}

add_action('shutdown', static function () {
    if (!class_exists('QM_Collectors')) {
        return;  // QM not active — nothing to capture
    }
    // Collector ids worth capturing; `hooks` is intentionally excluded (huge).
    $want = ['db_queries', 'php_errors', 'request', 'timing', 'http',
             'cache', 'conditionals', 'transients', 'logger', 'response',
             'db_callers', 'db_components'];
    try {
        $collectors = QM_Collectors::init();
        if (method_exists($collectors, 'process')) {
            $collectors->process();
        }
        $data = [];
        foreach ($collectors as $id => $collector) {
            if (!in_array($id, $want, true)) {
                continue;
            }
            $d = method_exists($collector, 'get_data') ? $collector->get_data() : ($collector->data ?? null);
            $data[$id] = $d;
        }
        $payload = [
            'ts'      => microtime(true),
            'url'     => $_SERVER['REQUEST_URI'] ?? '',
            'is_ajax' => function_exists('wp_doing_ajax') ? wp_doing_ajax() : false,
            'data'    => $data,
        ];
        // qm_capture sends a random, one-request marker. Persisting it lets
        // the reader find this request's record instead of returning a stale
        // final line written by unrelated traffic.
        if (isset($_GET['sandbox_qm_capture'])
            && is_string($_GET['sandbox_qm_capture'])
            && preg_match('/^[a-f0-9]{32}$/', $_GET['sandbox_qm_capture'])) {
            $payload['capture_id'] = $_GET['sandbox_qm_capture'];
        }
        $line = function_exists('wp_json_encode') ? wp_json_encode($payload) : json_encode($payload);
        if ($line !== false) {
            @file_put_contents(rtrim(WP_CONTENT_DIR, '/') . '/qm.jsonl', $line . "\n", FILE_APPEND | LOCK_EX);
        }
    } catch (\Throwable $e) {
        // never let capture break the request
    }
}, PHP_INT_MAX);
