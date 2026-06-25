<?php
/**
 * WPDeveloper platform module (spec 013) — KEYLESS force-activation.
 *
 * A WPDeveloper license key buys nothing in the sandbox: the licensed update API
 * gates the actual plugin download on a real upstream site activation (which
 * needs 2FA + consumes a seat), and we don't want that. So we don't manage a key
 * at all — we simply force every WPDeveloper pro plugin into an "activated" state
 * locally (dev/staging only), so they run without nag screens and with pro
 * features unlocked.
 *
 * Two mechanisms, no key:
 *  1. Seed each plugin's license-status option to 'valid'. The shared WPDeveloper
 *     SDK keys its options off a per-plugin `*_SL_DB_PREFIX` or `*_SL_ITEM_SLUG`
 *     constant; we scan both → covers every WPDeveloper pro plugin with no
 *     per-plugin code.
 *  2. Intercept the WPDeveloper license backend (api.wpdeveloper.com) and return a
 *     synthetic 'valid' for license actions, so any live re-check stays valid and
 *     never triggers the 2FA/OTP path. Update checks (get_version) and downloads
 *     pass through to the real API untouched.
 *
 * Disable with `define('SANDBOX_WPD_ACTIVATE_OFF', true)` (e.g. to test the real
 * unlicensed state). Dev/staging only.
 */

if (! defined('ABSPATH')) {
    return;
}
if (defined('SANDBOX_WPD_ACTIVATE_OFF') && SANDBOX_WPD_ACTIVATE_OFF) {
    return;
}

// A non-empty placeholder so plugins that require a non-blank license string are
// satisfied. This is NOT a real key — no key is involved.
if (! defined('SANDBOX_WPD_LICENSE_PLACEHOLDER')) {
    define('SANDBOX_WPD_LICENSE_PLACEHOLDER', 'sandbox-activated');
}

// 1) Keep any live WPDeveloper license check 'valid' (bypasses OTP/seat). Strictly
//    scoped to the WPDeveloper backend; only LICENSE actions are synthesized so
//    update checks/downloads pass through.
add_filter('pre_http_request', function ($pre, $args, $url) {
    if (strpos((string) $url, 'api.wpdeveloper.com') === false) {
        return $pre;
    }
    $body   = isset($args['body']) ? $args['body'] : array();
    $action = '';
    if (is_array($body)) {
        $action = isset($body['edd_action']) ? $body['edd_action'] : '';
    } elseif (is_string($body)) {
        $parsed = array();
        parse_str($body, $parsed);
        $action = isset($parsed['edd_action']) ? $parsed['edd_action'] : '';
    }
    $license_actions = array('activate_license', 'check_license', 'deactivate_license');
    if (! in_array($action, $license_actions, true)) {
        return $pre; // get_version / package_download reach the real API
    }
    $status  = ($action === 'deactivate_license') ? 'deactivated' : 'valid';
    $payload = array(
        'success'          => true,
        'license'          => $status,
        'item_id'          => false,
        'item_name'        => 'Sandbox Pro License',
        'license_limit'    => 0,
        'site_count'       => 1,
        'expires'          => 'lifetime',
        'activations_left' => 'unlimited',
        'price_id'         => false,
        'payment_id'       => 0,
        'customer_name'    => 'Sandbox',
        'customer_email'   => '',
        'checksum'         => '',
    );
    return array(
        'headers'       => array(),
        'body'          => wp_json_encode($payload),
        'response'      => array('code' => 200, 'message' => 'OK'),
        'cookies'       => array(),
        'http_response' => null,
    );
}, 10, 3);

// 2) Seed every WPDeveloper pro plugin's license option to 'valid'. We derive the
//    option names from the ACTIVE PLUGIN SLUGS (always available, every context) —
//    NOT from plugin constants, which several WPDeveloper plugins only define in
//    admin context. The shared SDK keys its option off either the item-slug form
//    (`{slug}_license_status`, e.g. better-payment-pro) or the db-prefix form
//    (`{slug→underscores}_software__license_status`, e.g.
//    essential_blocks_pro_software_); we seed BOTH for each active WPDeveloper
//    plugin → covers the whole family with no per-plugin code, in any context.
$sandbox_wpd_seed = function () {
    if (! function_exists('get_option')) {
        return;
    }
    // WPDeveloper product keywords — restrict seeding to this family so we never
    // touch unrelated plugins' options.
    $keywords = array(
        'better-payment', 'betterdocs', 'betterlinks', 'embedpress',
        'essential-addons', 'essential-blocks', 'notificationx',
        'wp-scheduled-posts', 'schedulepress',
    );
    $set = function ($prefix) {
        $lic = $prefix . '_license';
        $st  = $prefix . '_license_status';
        $cur = get_option($lic);
        if ($cur === false || $cur === '') {
            update_option($lic, SANDBOX_WPD_LICENSE_PLACEHOLDER, 'no');
        }
        if (get_option($st) !== 'valid') {
            update_option($st, 'valid', 'no');
        }
    };
    foreach ((array) get_option('active_plugins', array()) as $plugin) {
        $slug = dirname($plugin);
        if ($slug === '.' || $slug === '') {
            $slug = basename($plugin, '.php');
        }
        $is_wpd = false;
        foreach ($keywords as $kw) {
            if (strpos($slug, $kw) !== false) { $is_wpd = true; break; }
        }
        if (! $is_wpd) {
            continue;
        }
        $set($slug);                                          // item-slug form
        $set(str_replace('-', '_', $slug) . '_software_');    // db-prefix form
    }
    // Also flip any pre-existing *_license_status that's not yet valid.
    global $wpdb;
    if (isset($wpdb)) {
        $names = $wpdb->get_col(
            "SELECT option_name FROM {$wpdb->options} WHERE option_name LIKE '%\_license\_status'"
        );
        foreach ((array) $names as $opt) {
            if (get_option($opt) !== 'valid') {
                update_option($opt, 'valid', 'no');
            }
        }
    }
};
add_action('init', $sandbox_wpd_seed, 99);
add_action('admin_init', $sandbox_wpd_seed, 1);
