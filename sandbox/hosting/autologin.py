"""Bounded WordPress sign-ins using a database pool and connection-scoped lock."""
from __future__ import annotations

import base64
import hashlib
import json
import re

_SCRIPT = """(function(){var token=location.hash.slice(1);history.replaceState(null,'',location.pathname+location.search);if(/^[A-Za-z0-9_-]{43}$/.test(token)){var f=document.forms[0];f.elements.sandbox_autologin.value=token;f.submit();}else{document.getElementById('note').textContent='Open a fresh sign-in link from the stack page.';}})();"""


def render_plugin() -> str:
    """Keep hashes in WordPress, independent of later plugin installs or clicks."""
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="robots" content="noindex,nofollow"><title>Signing in</title></head>'
            '<body><form method="post"><input type="hidden" name="sandbox_autologin">'
            '<button>Continue</button></form><p id="note">Signing in…</p>'
            f'<script>{_SCRIPT}</script></body></html>')
    return _PLUGIN.replace('__PAGE__', json.dumps(page)).replace('__SCRIPT_HASH__', script_hash)


def register_script(container_path: str, token_hash: str, user: str, issued_at: int, expires_at: int) -> str:
    """PHP CLI program; bootstrap this site's WordPress, then register only a hash."""
    if not re.fullmatch(r"[0-9a-f]{64}", token_hash):
        raise ValueError("autologin hash must be SHA-256 hex")
    # JSON uses the same quoting rules needed for these PHP string literals, except '$'.
    def literal(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return (f"$directory = dirname({literal(container_path)}); "
            "while (!is_file($directory . '/wp-load.php')) { "
            "$parent = dirname($directory); if ($parent === $directory) { exit(71); } $directory = $parent; } "
            "require $directory . '/wp-load.php'; "
            "if (!function_exists('sandbox_host_autologin_register')) { exit(72); } "
            f"sandbox_host_autologin_register({literal(token_hash)}, {literal(user)}, "
            f"{int(issued_at)}, {int(expires_at)});")


_PLUGIN = r'''<?php
/** Generated Sandbox WordPress sign-in handler. Raw tokens are never stored. */
defined('ABSPATH') || exit;

function sandbox_host_autologin_pool_key() {
    return 'sandbox_host_autologin_pool_' . substr(hash('sha256', __FILE__), 0, 16);
}

function sandbox_host_autologin_with_pool($operation) {
    global $wpdb;
    $key = sandbox_host_autologin_pool_key();
    $lock = 'sb-autologin-' . sha1(DB_NAME . $wpdb->base_prefix . $key);
    if ('1' !== (string) $wpdb->get_var($wpdb->prepare('SELECT GET_LOCK(%s, 10)', $lock))) {
        throw new RuntimeException('Sandbox sign-in is busy. Click again.');
    }
    try {
        // Other workers can update this option while this process waits for the lock.
        if (is_multisite()) {
            $network = get_current_network_id();
            wp_cache_delete($network . ':' . $key, 'site-options');
            wp_cache_delete($network . ':notoptions', 'site-options');
        } else {
            wp_cache_delete($key, 'options');
            wp_cache_delete('alloptions', 'options');
            wp_cache_delete('notoptions', 'options');
        }
        $pool = get_site_option($key, array());
        if (!is_array($pool)) {
            throw new RuntimeException('Sandbox sign-in state is unavailable.');
        }
        $before = $pool;
        foreach ($pool as $hash => $entry) {
            if (!is_array($entry) || !isset($entry['expires_at']) || $entry['expires_at'] <= time()) {
                unset($pool[$hash]);
            }
        }
        $result = $operation($pool);
        if ($pool !== $before && !update_site_option($key, $pool)) {
            throw new RuntimeException('Sandbox sign-in state could not be saved.');
        }
        return $result;
    } finally {
        $wpdb->get_var($wpdb->prepare('SELECT RELEASE_LOCK(%s)', $lock));
    }
}

function sandbox_host_autologin_register($hash, $user, $issued_at, $expires_at) {
    if (!preg_match('/^[0-9a-f]{64}$/D', $hash) || $expires_at <= time() || $expires_at - $issued_at > 3600) {
        throw new RuntimeException('Invalid Sandbox sign-in registration.');
    }
    sandbox_host_autologin_with_pool(static function (&$pool) use ($hash, $user, $issued_at, $expires_at) {
        uasort($pool, static function ($a, $b) { return $a['issued_at'] <=> $b['issued_at']; });
        while (count($pool) >= 8) { array_shift($pool); }
        $pool[$hash] = array('user' => $user, 'issued_at' => $issued_at, 'expires_at' => $expires_at);
    });
}

function sandbox_host_autologin_consume($token) {
    $hash = hash('sha256', $token);
    return sandbox_host_autologin_with_pool(static function (&$pool) use ($hash) {
        if (!isset($pool[$hash])) { return false; }
        $user = $pool[$hash]['user'];
        unset($pool[$hash]);
        return $user;
    });
}

function sandbox_host_autologin_same_origin($origin) {
    if (!is_string($origin)) { return false; }
    $source = parse_url($origin);
    $site = parse_url(home_url('/'));
    if (!is_array($source) || !is_array($site) || isset($source['user']) || isset($source['pass']) ||
        isset($source['path']) || isset($source['query']) || isset($source['fragment'])) { return false; }
    foreach (array($source, $site) as $parts) {
        if (!isset($parts['scheme'], $parts['host']) ||
            !in_array(strtolower($parts['scheme']), array('http', 'https'), true)) { return false; }
    }
    $source_port = $source['port'] ?? (strtolower($source['scheme']) === 'https' ? 443 : 80);
    $site_port = $site['port'] ?? (strtolower($site['scheme']) === 'https' ? 443 : 80);
    return strtolower($source['scheme']) === strtolower($site['scheme']) &&
           strtolower($source['host']) === strtolower($site['host']) && $source_port === $site_port;
}

add_action('init', static function () {
    if (!isset($_GET['sandbox_autologin']) && !isset($_POST['sandbox_autologin'])) { return; }
    header('Cache-Control: no-store');
    header('Referrer-Policy: same-origin');
    header('X-Robots-Tag: noindex, nofollow');
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
        header("Content-Security-Policy: default-src 'none'; script-src 'sha256-__SCRIPT_HASH__'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");
        echo __PAGE__;
        exit;
    }
    if (!sandbox_host_autologin_same_origin($_SERVER['HTTP_ORIGIN'] ?? null) ||
        (isset($_SERVER['HTTP_SEC_FETCH_SITE']) && $_SERVER['HTTP_SEC_FETCH_SITE'] !== 'same-origin')) {
        status_header(403);
        wp_die('This sign-in must be opened in a browser from the stack page.', '', array('response' => 403));
    }
    $token = $_POST['sandbox_autologin'] ?? null;
    if (!is_string($token) || !preg_match('/^[A-Za-z0-9_-]{43}$/D', $token)) {
        status_header(403);
        wp_die('Open a fresh sign-in link from the stack page.', '', array('response' => 403));
    }
    try { $login = sandbox_host_autologin_consume(wp_unslash($token)); }
    catch (Throwable $error) { $login = false; }
    if (!$login || !($user = get_user_by('login', $login))) {
        status_header(403);
        wp_die('This Sandbox sign-in was used or expired. Click sign-in again on the stack page.', '',
               array('response' => 403));
    }
    wp_set_current_user($user->ID);
    wp_set_auth_cookie($user->ID, false, is_ssl());
    wp_safe_redirect(is_multisite() ? network_admin_url() : admin_url());
    exit;
}, 1);
'''
