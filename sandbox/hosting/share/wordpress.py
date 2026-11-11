"""Render the spec 088 WordPress online-claim consumer, without runtime effects.

Wire HMAC framing is UTF-8, ASCII fields joined by ``|`` without a trailing
separator; HMAC-SHA256 uses bytes decoded from the per-link 64-hex key.
Request: sandbox-share-wp-claim-request.v1|kid|token_sha256|nonce|at
Response: sandbox-share-wp-claim-response.v1|kid|token_sha256|nonce|at|expires_at
The response's ``ok`` must be literal true. Its expiry must equal token expiry.
"""
from __future__ import annotations

import base64
import hashlib
import re
import time
from urllib.parse import urlsplit

from sandbox.hosting.share import SIGN_IN_SECONDS, ShareRefusal

CLAIM_PATH = "/__sandbox/stack/wordpress-claim"
REQUEST_PURPOSE = "sandbox-share-wp-claim-request.v1"
RESPONSE_PURPOSE = "sandbox-share-wp-claim-response.v1"
CLAIM_CLOCK_SKEW = 30


def render_claim_plugin(
    kid: str, key: str, user: str, expires_at: int, claim_origin: str,
    *, now: float | None = None,
) -> str:
    """Render a per-link PHP plugin; invalid configuration raises ShareRefusal."""
    if (not isinstance(kid, str) or not re.fullmatch(r"[0-9a-f]{12}|[0-9a-f]{32}", kid)
            or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)):
        raise ShareRefusal("share_failed", "invalid WordPress share key")
    if type(expires_at) is not int or expires_at <= int(time.time() if now is None else now):
        raise ShareRefusal("share_failed", "WordPress share key must expire in the future")
    if (not isinstance(user, str) or not user or len(user) > 60
            or any(ord(char) < 33 or ord(char) > 126 for char in user)):
        raise ShareRefusal("share_failed", "invalid configured WordPress login")
    _validate_origin(claim_origin)
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="robots" content="noindex, nofollow"><title>Signing in</title></head>'
            '<body><main><h1>Signing in</h1><form method="post"><input type="hidden" name="token">'
            '<button>Continue</button></form><p id="incomplete" hidden>Open a fresh sign-in from the stack page.</p>'
            '<script>' + _OPENER_SCRIPT + '</script></main></body></html>')
    script_hash = base64.b64encode(hashlib.sha256(_OPENER_SCRIPT.encode()).digest()).decode()
    policy = ("default-src 'none'; script-src 'sha256-" + script_hash
              + "'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
    replacements = {
        "@@NAMESPACE@@": "SandboxShareClaim" + kid,
        "@@KID@@": _php(kid), "@@KEY@@": _php(key), "@@USER@@": _php(user),
        "@@UNTIL@@": str(expires_at), "@@SIGN_IN_SECONDS@@": str(SIGN_IN_SECONDS),
        "@@ENDPOINT@@": _php(claim_origin + CLAIM_PATH), "@@PAGE@@": _php(page),
        "@@POLICY@@": _php("Content-Security-Policy: " + policy),
        "@@REQUEST_PURPOSE@@": _php(REQUEST_PURPOSE),
        "@@RESPONSE_PURPOSE@@": _php(RESPONSE_PURPOSE), "@@SKEW@@": str(CLAIM_CLOCK_SKEW),
    }
    return re.sub(r"@@[A-Z_]+@@", lambda match: replacements[match.group()], _PLUGIN)


def _validate_origin(origin: str) -> None:
    if not isinstance(origin, str):
        raise ShareRefusal("share_failed", "claim origin must be canonical HTTPS")
    try:
        parsed = urlsplit(origin)
        host = parsed.hostname or ""
        port = parsed.port
        canonical = "https://" + host + (":" + str(port) if port is not None else "")
        valid_host = (len(host) <= 253 and re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", host)
            and not host.replace(".", "").isdigit()
            and host != "localhost" and not host.endswith(".localhost")
            and host != "local" and not host.endswith(".local"))
        if (not valid_host or origin != canonical or parsed.scheme != "https"
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or port == 443 or (port is not None and port < 1)):
            raise ValueError
    except ValueError:
        raise ShareRefusal("share_failed", "claim origin must be canonical public HTTPS") from None


def _php(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


_OPENER_SCRIPT = (
    "(function(){var m=location.hash.match(/^#([0-9]{1,12}\\.[A-Za-z0-9_-]{22,64}\\.[0-9a-f]{64})$/);"
    "var f=document.forms[0];history.replaceState(null,'',location.pathname+location.search);"
    "if(m){f.elements.token.value=m[1];f.submit();}else{f.hidden=true;"
    "document.getElementById('incomplete').hidden=false;}})();"
)

_PLUGIN = r"""<?php
/** Sandbox spec 088: online claim required before any WordPress authentication. */
namespace @@NAMESPACE@@;
defined( 'ABSPATH' ) || exit;
add_action( 'init', static function () {
    $kid = @@KID@@;
    if ( ! isset( $_GET['sandbox_share_login'] ) || ! is_string( $_GET['sandbox_share_login'] )
            || $_GET['sandbox_share_login'] !== $kid ) {
        return;
    }
    nocache_headers();
    header( 'X-Robots-Tag: noindex, nofollow, noarchive' );
    // A same-origin form POST must send Origin, including after the fragment opener.
    header( 'Referrer-Policy: same-origin' );
    $deny = static function ( $status = 403 ) {
        status_header( $status );
        wp_die( 'This Sandbox sign-in is unavailable. Open a fresh sign-in from the stack page.', '', array( 'response' => $status ) );
        exit;
    };
    $until = @@UNTIL@@;
    $now = time();
    if ( $now >= $until || ( is_multisite()
            && get_current_blog_id() !== (int) get_main_site_id( get_main_network_id() ) ) ) {
        $deny( 404 );
    }
    if ( array_keys( $_GET ) !== array( 'sandbox_share_login' ) ) {
        $deny();
    }
    $method = $_SERVER['REQUEST_METHOD'] ?? '';
    if ( 'GET' === $method || 'HEAD' === $method ) {
        header( 'Content-Type: text/html; charset=utf-8' );
        header( @@POLICY@@ );
        if ( 'GET' === $method ) {
            echo @@PAGE@@;
        }
        exit;
    }
    if ( 'POST' !== $method ) {
        $deny( 405 );
    }
    // Trust WordPress configuration, never attacker-controlled Host or forwarded headers.
    $home = parse_url( home_url( '/' ) );
    if ( ! is_array( $home ) || ( $home['scheme'] ?? '' ) !== 'https'
            || empty( $home['host'] ) || isset( $home['user'] ) || isset( $home['pass'] ) ) {
        $deny();
    }
    $home_origin = 'https://' . strtolower( $home['host'] );
    if ( isset( $home['port'] ) && 443 !== $home['port'] ) {
        $home_origin .= ':' . $home['port'];
    }
    if ( ( $_SERVER['HTTP_ORIGIN'] ?? '' ) !== $home_origin
            || ( isset( $_SERVER['HTTP_SEC_FETCH_SITE'] ) && $_SERVER['HTTP_SEC_FETCH_SITE'] !== 'same-origin' ) ) {
        $deny();
    }
    $length = $_SERVER['CONTENT_LENGTH'] ?? '';
    if ( ! preg_match( '/\A[0-9]{1,3}\z/', (string) $length )
            || (int) $length < 1 || (int) $length > 256
            || strtolower( trim( explode( ';', $_SERVER['CONTENT_TYPE'] ?? '' )[0] ) ) !== 'application/x-www-form-urlencoded' ) {
        $deny();
    }
    $body = file_get_contents( 'php://input', false, null, 0, 257 );
    if ( ! is_string( $body ) || strlen( $body ) !== (int) $length || strlen( $body ) > 256
            || ! preg_match( '/\Atoken=([0-9]{1,12})\.([A-Za-z0-9_-]{22,64})\.([0-9a-f]{64})\z/', $body, $m ) ) {
        $deny();
    }
    $token = substr( $body, 6 );
    $exp = (int) $m[1];
    if ( $exp <= $now || $exp > $now + @@SIGN_IN_SECONDS@@ || $exp > $until ) {
        $deny();
    }
    $key = hex2bin( @@KEY@@ );
    $mac = hash_hmac( 'sha256', 'sandbox-share-login.v1|' . $kid . '|' . $m[1] . '|' . $m[2], $key );
    if ( ! hash_equals( $mac, $m[3] ) ) {
        $deny();
    }
    // option_name is UNIQUE on the one allowed blog. sitemeta is not unique.
    // Claim BEFORE HTTP: ambiguous callback failure burns only this nonce.
    global $wpdb;
    $used = 'sandbox_share_n_' . substr( hash( 'sha256', $kid . '|' . $m[2] ), 0, 40 );
    $claimed = $wpdb->query( $wpdb->prepare(
        'INSERT IGNORE INTO ' . $wpdb->options . " (option_name, option_value, autoload) VALUES (%s, %s, 'no')",
        $used, (string) $exp
    ) );
    if ( 1 !== $claimed ) {
        $deny( 410 );
    }
    try {
        $nonce = bin2hex( random_bytes( 16 ) );
    } catch ( \Throwable $error ) {
        $deny();
    }
    $at = time();
    $hash = hash( 'sha256', $token );
    $request = array( 'kid' => $kid, 'token_sha256' => $hash, 'nonce' => $nonce, 'at' => $at );
    $request['mac'] = hash_hmac( 'sha256', @@REQUEST_PURPOSE@@ . '|' . $kid . '|' . $hash . '|' . $nonce . '|' . $at, $key );
    $reply = wp_remote_post( @@ENDPOINT@@, array(
        'sslverify' => true, 'redirection' => 0, 'timeout' => 5, 'limit_response_size' => 2048,
        'headers' => array( 'Content-Type' => 'application/json', 'Accept' => 'application/json' ),
        'body' => wp_json_encode( $request ),
    ) );
    if ( is_wp_error( $reply ) || 200 !== wp_remote_retrieve_response_code( $reply ) ) {
        $deny();
    }
    $raw = wp_remote_retrieve_body( $reply );
    if ( ! is_string( $raw ) || strlen( $raw ) < 2 || strlen( $raw ) >= 2048 ) {
        $deny();
    }
    // Canonical field spelling, exactly once each: json_decode alone accepts duplicate keys.
    if ( 7 !== preg_match_all( '/"(?:ok|kid|token_sha256|nonce|at|expires_at|mac)"\s*:/', $raw ) ) {
        $deny();
    }
    $claim = json_decode( $raw, true, 8 );
    $fields = is_array( $claim ) ? array_keys( $claim ) : array();
    sort( $fields );
    if ( $fields !== array( 'at', 'expires_at', 'kid', 'mac', 'nonce', 'ok', 'token_sha256' )
            || $claim['ok'] !== true || $claim['kid'] !== $kid || $claim['token_sha256'] !== $hash
            || $claim['nonce'] !== $nonce || ! is_int( $claim['at'] ) || ! is_int( $claim['expires_at'] )
            || ! is_string( $claim['mac'] ) || ! preg_match( '/\A[0-9a-f]{64}\z/', $claim['mac'] ) ) {
        $deny();
    }
    $now = time();
    if ( abs( $claim['at'] - $now ) > @@SKEW@@ || abs( $claim['at'] - $at ) > @@SKEW@@
            || $claim['expires_at'] !== $exp || $exp <= $now || $exp > $until || $now >= $until ) {
        $deny();
    }
    $expected = hash_hmac( 'sha256', @@RESPONSE_PURPOSE@@ . '|' . $kid . '|' . $hash . '|' . $nonce . '|' . $claim['at'] . '|' . $exp, $key );
    if ( ! hash_equals( $expected, $claim['mac'] ) ) {
        $deny();
    }
    $user = get_user_by( 'login', @@USER@@ );
    if ( ! $user ) {
        $deny();
    }
    wp_set_current_user( $user->ID );
    wp_set_auth_cookie( $user->ID, false, is_ssl() );
    wp_safe_redirect( is_multisite() ? network_admin_url() : admin_url() );
    exit;
}, 1 );
"""
