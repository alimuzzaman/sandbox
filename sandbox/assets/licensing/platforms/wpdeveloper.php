<?php
/**
 * WPDeveloper platform module (spec 013).
 *
 * One license key activates ALL WPDeveloper pro plugins. Every WPDeveloper pro
 * plugin validates against the same EDD-style backend (api.wpdeveloper.com), so a
 * single strictly-scoped pre_http_request interceptor that returns a synthetic
 * "valid" response for the central key makes them all report licensed — uniform
 * across the modern shared-SDK plugins (essential-blocks-pro, notificationx-pro,
 * wp-scheduled-posts-pro, betterdocs-pro) and the older bespoke-license ones
 * (essential-addons-elementor, embedpress-pro, betterlinks-pro, better-payment-pro),
 * with no per-plugin option schema, no OTP, and no seat consumption.
 *
 * Only LICENSE actions (activate/check/deactivate) are synthesized; update checks
 * (get_version) and downloads (package_download) pass through to the real API so
 * the real key fetches real builds.
 *
 * Expects $SANDBOX_LICENSING['wpdeveloper_key'] from the loader. No-ops without it.
 */

if (! defined('ABSPATH')) {
    return;
}

$sandbox_wpd_key = isset($SANDBOX_LICENSING['wpdeveloper_key'])
    ? (string) $SANDBOX_LICENSING['wpdeveloper_key'] : '';
if ($sandbox_wpd_key === '') {
    return; // no WPDeveloper key → no-op
}

add_filter('pre_http_request', function ($pre, $args, $url) use ($sandbox_wpd_key) {
    // Strictly scoped: only the WPDeveloper license backend. All other HTTP untouched.
    if (strpos((string) $url, 'api.wpdeveloper.com') === false) {
        return $pre;
    }

    // Extract edd_action from the request body (array or urlencoded string).
    $body   = isset($args['body']) ? $args['body'] : array();
    $action = '';
    if (is_array($body)) {
        $action = isset($body['edd_action']) ? $body['edd_action'] : '';
    } elseif (is_string($body)) {
        $parsed = array();
        parse_str($body, $parsed);
        $action = isset($parsed['edd_action']) ? $parsed['edd_action'] : '';
    }

    // Only synthesize license validation; let updates/downloads reach the real API.
    $license_actions = array('activate_license', 'check_license', 'deactivate_license');
    if (! in_array($action, $license_actions, true)) {
        return $pre;
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

// Instant activation: seed each WPDeveloper plugin's license option to 'valid' so
// it reports licensed on boot without waiting for its own check. The shared
// WPDeveloper Licensing SDK keys its options off a per-plugin `*_SL_DB_PREFIX`
// constant ({prefix}_license + {prefix}_license_status); we scan those constants,
// so this covers EVERY shared-SDK WPDeveloper plugin with NO per-plugin code. We
// also flip any pre-existing *_license_status (catches bespoke-license plugins).
// The interceptor above is the real guarantee; this makes it immediate. Runs late
// so plugin constants are defined.
add_action('init', function () use ($sandbox_wpd_key) {
    if (! function_exists('get_option')) {
        return;
    }
    $user = get_defined_constants(true);
    $user = isset($user['user']) ? $user['user'] : array();
    foreach ($user as $name => $val) {
        if (is_string($val) && $val !== '' && substr($name, -13) === '_SL_DB_PREFIX') {
            $lic = $val . '_license';            // SDK: {prefix}_license
            $st  = $val . '_license_status';     // SDK: {prefix}_license_status
            $cur = get_option($lic);
            if ($cur === false || $cur === '') {
                update_option($lic, $sandbox_wpd_key, 'no');
            }
            if (get_option($st) !== 'valid') {
                update_option($st, 'valid', 'no');
            }
        }
    }
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
}, 20);
