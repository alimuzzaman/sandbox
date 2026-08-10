<?php
/**
 * Sandbox dump()/dd() — quick-and-dirty debugging to a tailable file (spec 007).
 *
 * Defines global dump()/dd() that append a faithful, plain-text rendering to
 * wp-content/debug-dump.log (separate from the noisy debug.log) with a timestamp
 * + caller file:line. Read it with `./sb dump` or tail_log(file="dump").
 *
 * Local development only: hard-returns outside WP_ENVIRONMENT_TYPE=local.
 * function_exists-guarded so it never collides with Symfony's / another plugin's.
 */

if (!defined('ABSPATH')) {
    exit;
}

$sandbox_dump_environment = function_exists('wp_get_environment_type')
    ? wp_get_environment_type()
    : (defined('WP_ENVIRONMENT_TYPE') ? WP_ENVIRONMENT_TYPE : '');
if ('local' !== $sandbox_dump_environment) {
    return;
}

if (!function_exists('sandbox_dump_write')) {
    function sandbox_dump_write(array $vars, bool $die = false): void
    {
        $log = rtrim(WP_CONTENT_DIR, '/') . '/debug-dump.log';
        $bt = debug_backtrace(DEBUG_BACKTRACE_IGNORE_ARGS, 2);
        $caller = $bt[1] ?? $bt[0] ?? [];
        $loc = ($caller['file'] ?? '?') . ':' . ($caller['line'] ?? 0);
        $out = "\n=== dump " . gmdate('H:i:s') . '  ' . $loc . " ===\n";
        foreach ($vars as $v) {
            $out .= sandbox_dump_render($v) . "\n";
        }
        @file_put_contents($log, $out, FILE_APPEND | LOCK_EX);
        if ($die) {
            if (function_exists('wp_die')) {
                wp_die('dd() — see wp-content/debug-dump.log');
            }
            exit('dd() — see wp-content/debug-dump.log');
        }
    }
}

if (!function_exists('sandbox_dump_render')) {
    function sandbox_dump_render($v, int $depth = 0): string
    {
        if ($depth > 6) {
            return '*MAX_DEPTH*';
        }
        if (is_bool($v)) {
            return $v ? 'true' : 'false';
        }
        if (is_null($v)) {
            return 'null';
        }
        if (is_scalar($v)) {
            return is_string($v) ? '"' . $v . '"' : (string) $v;
        }
        $pad = str_repeat('  ', $depth + 1);
        if (is_array($v)) {
            if (!$v) {
                return '[]';
            }
            $s = "[\n";
            foreach ($v as $k => $val) {
                $s .= $pad . (is_int($k) ? $k : '"' . $k . '"') . ' => '
                    . sandbox_dump_render($val, $depth + 1) . ",\n";
            }
            return $s . str_repeat('  ', $depth) . ']';
        }
        if (is_object($v)) {
            $cls = get_class($v);
            $props = get_object_vars($v);
            if (!$props) {
                return $cls . ' {}';
            }
            $s = $cls . " {\n";
            foreach ($props as $k => $val) {
                $s .= $pad . '$' . $k . ' = ' . sandbox_dump_render($val, $depth + 1) . ",\n";
            }
            return $s . str_repeat('  ', $depth) . '}';
        }
        return gettype($v);
    }
}

if (!function_exists('dump')) {
    function dump(...$vars)
    {
        sandbox_dump_write($vars);
        return $vars[0] ?? null;
    }
}

if (!function_exists('dd')) {
    function dd(...$vars)
    {
        sandbox_dump_write($vars, true);
    }
}
