<?php
/**
 * Elementor Pro platform module (spec 013) — share ONE activation across instances.
 *
 * Unlike WPDeveloper, Elementor Pro is genuinely licensed (OAuth/connect, seat-
 * limited), so we don't fabricate activation — we SHARE a real one. You connect
 * Elementor Pro on ONE instance (the primary) by hand; `sb license elementor-sync`
 * captures that instance's license key + license-data option + its URL into the
 * central store and propagates them here. This module then makes every OTHER
 * instance ride that single activation:
 *
 *  1. Seed `elementor_pro_license_key` + `_elementor_pro_license_v2_data` from the
 *     primary so this instance reports activated immediately (is_license_active()).
 *  2. Pin Elementor's license API URL to the primary's URL (reusing the proven
 *     `elementor_pro/license/api/use_home_url` + site_url method from
 *     templately-multi) so any live re-validation is seen by my.elementor.com as
 *     the primary site — one seat, many instances.
 *
 * Reads $SANDBOX_LICENSING (from the loader). No-ops on the primary itself and
 * when no primary/license data is present. Dev/staging only.
 */

if (! defined('ABSPATH')) {
    return;
}

$sandbox_el = isset($SANDBOX_LICENSING) && is_array($SANDBOX_LICENSING) ? $SANDBOX_LICENSING : array();
$sandbox_el_primary_url = isset($sandbox_el['elementor_primary_url']) ? (string) $sandbox_el['elementor_primary_url'] : '';
$sandbox_el_is_primary  = ! empty($sandbox_el['is_primary']);
$sandbox_el_key         = isset($sandbox_el['elementor_pro_key']) ? (string) $sandbox_el['elementor_pro_key'] : '';
$sandbox_el_data        = isset($sandbox_el['elementor_license_data']) ? $sandbox_el['elementor_license_data'] : null;

// The primary IS the real connected site — never override it. And without a
// primary URL there's nothing to share yet.
if ($sandbox_el_is_primary || $sandbox_el_primary_url === '') {
    return;
}

// 1) Seed the activation options so this secondary reports licensed on boot.
add_action('init', function () use ($sandbox_el_key, $sandbox_el_data) {
    if (! function_exists('update_option')) {
        return;
    }
    if ($sandbox_el_key !== '' && get_option('elementor_pro_license_key') !== $sandbox_el_key) {
        update_option('elementor_pro_license_key', $sandbox_el_key);
    }
    if (is_array($sandbox_el_data)) {
        // _elementor_pro_license_v2_data wraps the payload as {timeout, value};
        // refresh the timeout so EL Pro's transient read doesn't treat it as expired.
        $data = $sandbox_el_data;
        if (isset($data['timeout'])) {
            $data['timeout'] = time() + YEAR_IN_SECONDS;
        }
        update_option('_elementor_pro_license_v2_data', $data, false);
    }
}, 5);

// 2) Pin the Elementor license API URL to the primary's URL. EL Pro builds the
//    request URL from get_site_url() when use_home_url is false; we force that to
//    the primary's URL with a ONE-SHOT site_url filter (removes itself immediately
//    so unrelated site_url calls are unaffected), exactly as templately-multi does.
function sandbox_elementor_pin_site_url($url, $path = '', $scheme = null) {
    remove_filter('site_url', 'sandbox_elementor_pin_site_url', 10);
    remove_filter('home_url', 'sandbox_elementor_pin_site_url', 10);
    $primary = $GLOBALS['sandbox_el_primary_url_value'];
    return $primary . ($path ? '/' . ltrim($path, '/') : '');
}
$GLOBALS['sandbox_el_primary_url_value'] = $sandbox_el_primary_url;

add_filter('elementor_pro/license/api/use_home_url', function ($use_home_url) {
    add_filter('site_url', 'sandbox_elementor_pin_site_url', 10, 3);
    add_filter('home_url', 'sandbox_elementor_pin_site_url', 10, 3);
    return false; // → EL Pro uses get_site_url(), which we've pinned to the primary
});
