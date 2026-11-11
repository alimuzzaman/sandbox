"""Behavioral PHP fixtures for the WordPress online-claim consumer. No live HTTP."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from sandbox.hosting.share import ShareRefusal
from sandbox.hosting.share.wordpress import render_claim_plugin
from tests.subprocess_support import run_test_process

KID = 'c' * 12
KEY = 'd' * 64
NOW = 2_000_000_000
EXP = NOW + 120
ORIGIN = 'https://gate.example.test'


def token(nonce='a' * 32, exp=EXP):
    message = f'sandbox-share-login.v1|{KID}|{exp}|{nonce}'
    return f'{exp}.{nonce}.' + hmac.new(bytes.fromhex(KEY), message.encode(), hashlib.sha256).hexdigest()


class RendererBoundaryTest(unittest.TestCase):
    def test_rejects_noncanonical_or_local_claim_origins(self):
        for origin in ('http://gate.example.test', 'https://localhost', 'https://127.0.0.1',
                       'https://[::1]', 'https://gate.example.test/', 'https://user@gate.example.test',
                       'https://gate.example.test?x', 'https://gate.example.test#x',
                       'https://GATE.example.test', 'https://bad_.example.test',
                       'https://gate.example.test:443', 'https://foo.localhost'):
            with self.subTest(origin=origin), self.assertRaises(ShareRefusal):
                render_claim_plugin(KID, KEY, 'admin', EXP, origin, now=NOW)

    def test_configuration_types_and_expiry(self):
        for kid, key, user, expiry in ((KID, KEY, 'admin', NOW), (KID, KEY, 'admin', True),
                ('bad', KEY, 'admin', EXP), (KID, 'x' * 64, 'admin', EXP),
                (KID, KEY, "admin\n", EXP)):
            with self.subTest(kid=kid, expiry=expiry), self.assertRaises(ShareRefusal):
                render_claim_plugin(kid, key, user, expiry, ORIGIN, now=NOW)


@unittest.skipUnless(shutil.which('php'), 'PHP CLI unavailable')
class WordPressClaimTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'plugin.php').write_text(render_claim_plugin(KID, KEY, 'configured-admin',
                                                               NOW + 600, ORIGIN, now=NOW))
        (self.root / 'runner.php').write_text(PHP_FIXTURE.replace('__KID__', KID).replace('__KEY__', KEY))

    def request(self, **overrides):
        payload = {'body': 'token=' + token(), **overrides}
        result = run_test_process([shutil.which('php'), str(self.root / 'runner.php'), json.dumps(payload)],
                                  capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_success_claim_precedes_auth_and_uses_fixed_transport(self):
        result = self.request()
        self.assertEqual(result['events'], ['insert', 'claim', 'user:configured-admin', 'current:7', 'cookie:7', 'redirect'])
        self.assertEqual(result['url'], ORIGIN + '/__sandbox/stack/wordpress-claim')
        args = result['args']
        self.assertEqual([args['sslverify'], args['redirection'], args['timeout'], args['limit_response_size']],
                         [True, 0, 5, 2048])
        body = json.loads(args['body'])
        self.assertEqual(set(body), {'kid', 'token_sha256', 'nonce', 'at', 'mac'})
        self.assertEqual(body['token_sha256'], hashlib.sha256(token().encode()).hexdigest())
        framing = f"sandbox-share-wp-claim-request.v1|{KID}|{body['token_sha256']}|{body['nonce']}|{NOW}"
        self.assertEqual(body['mac'], hmac.new(bytes.fromhex(KEY), framing.encode(), hashlib.sha256).hexdigest())
        self.assertNotIn(token(), json.dumps(args))

    def test_callback_failures_never_authenticate_and_burn_nonce(self):
        for mode in ('wrong_mac', 'wrong_nonce', 'wrong_hash', 'wrong_kid', 'old_time', 'future_time',
                     'wrong_expiry', 'expired', 'http_error', 'redirect', 'oversized', 'truncated',
                     'unsigned', 'extra', 'false_ok', 'string_at', 'string_expiry', 'duplicate', 'malformed', 'empty'):
            with self.subTest(mode=mode):
                (self.root / 'used.json').unlink(missing_ok=True)
                result = self.request(mode=mode)
                self.assertEqual(result['status'], 403)
                self.assertEqual(result['events'], ['insert', 'claim'])
                replay = self.request()
                self.assertEqual(replay['status'], 410)
                self.assertEqual(replay['events'], ['insert'])

    def test_invalid_browser_requests_do_not_claim_or_authenticate(self):
        for payload in ({'origin': None}, {'origin': 'null'}, {'origin': 'https://evil.example.test'},
                        {'origin': 'https://evil.example.test', 'host': 'evil.example.test'},
                        {'fetch': 'cross-site'}, {'body': 'token=' + token() + '&extra=1'},
                        {'body': 'token=' + token() + '&token=' + token()}, {'length': 9999},
                        {'content_type': 'application/json'}, {'method': 'PUT'}, {'query_token': True},
                        {'body': 'token=' + token()[:-1] + ('1' if token()[-1] == '0' else '0')}, {'body': 'token=' + token(exp=NOW)},
                        {'body': 'token=' + token(nonce='!')}, {'body': 'token=' + token(exp=NOW + 601)},
                        {'clock': NOW + 601}):
            with self.subTest(payload=payload):
                result = self.request(**payload)
                self.assertEqual(result['events'], [])
                self.assertNotEqual(result['status'], 200)

    def test_multisite_only_network_main_blog_and_options_unique_claim(self):
        self.assertEqual(self.request(multisite=True, blog=2)['events'], [])
        result = self.request(multisite=True, blog=1)
        self.assertIn('wp_options', result['sql'])
        self.assertNotIn('sitemeta', result['sql'])
        self.assertIn('INSERT IGNORE', result['sql'])
        self.assertEqual(self.request(multisite=True, blog=1)['status'], 410)

    def test_opener_preserves_origin_and_clears_fragment_before_post(self):
        result = self.request(method='GET')
        self.assertIn('Referrer-Policy: same-origin', result['headers'])
        self.assertIn("history.replaceState", result['page'])
        self.assertLess(result['page'].index('history.replaceState'), result['page'].index('f.submit()'))
        self.assertEqual(result['events'], [])


PHP_FIXTURE = r'''<?php
namespace SandboxShareClaim__KID__ {
function time() { return $GLOBALS['input']['clock'] ?? 2000000000; }
function file_get_contents($path) { return $GLOBALS['input']['body']; }
function header($line) { $GLOBALS['result']['headers'][] = $line; }
}
namespace {
define('ABSPATH', __DIR__);
$input = json_decode($argv[1], true);
$result = ['status'=>200, 'events'=>[], 'headers'=>[]];
ob_start();
register_shutdown_function(function () { $GLOBALS['result']['page'] = ob_get_clean(); echo json_encode($GLOBALS['result']); });
$_GET = ['sandbox_share_login'=>'__KID__'];
if (!empty($input['query_token'])) $_GET['token'] = 'forbidden';
$_SERVER = ['REQUEST_METHOD'=>$input['method'] ?? 'POST', 'HTTP_HOST'=>$input['host'] ?? 'wp.example.test',
 'CONTENT_TYPE'=>$input['content_type'] ?? 'application/x-www-form-urlencoded',
 'CONTENT_LENGTH'=>$input['length'] ?? strlen($input['body'])];
if (!array_key_exists('origin', $input) || $input['origin'] !== null) $_SERVER['HTTP_ORIGIN'] = $input['origin'] ?? 'https://wp.example.test';
if (isset($input['fetch'])) $_SERVER['HTTP_SEC_FETCH_SITE'] = $input['fetch'];
function add_action($hook, $callback, $priority) { $GLOBALS['callback'] = $callback; }
function nocache_headers() {}
function status_header($code) { $GLOBALS['result']['status'] = $code; }
function wp_die($text) { exit; }
function home_url($path = '') { return 'https://wp.example.test' . $path; }
function is_multisite() { return $GLOBALS['input']['multisite'] ?? false; }
function get_current_blog_id() { return $GLOBALS['input']['blog'] ?? 1; }
function get_main_site_id($network) { return 1; }
function get_main_network_id() { return 1; }
class Database {
 public $options = 'wp_options';
 function prepare($sql, ...$args) { $GLOBALS['result']['sql'] = $sql; return $args[0]; }
 function query($key) {
  $GLOBALS['result']['events'][] = 'insert';
  $file = __DIR__ . '/used.json';
  $used = is_file($file) ? json_decode(\file_get_contents($file), true) : [];
  if (isset($used[$key])) return 0;
  $used[$key] = true; file_put_contents($file, json_encode($used)); return 1;
 }
}
$wpdb = new Database;
function wp_json_encode($value) { return json_encode($value); }
function wp_remote_post($url, $args) {
 $GLOBALS['result']['events'][] = 'claim'; $GLOBALS['result']['url'] = $url; $GLOBALS['result']['args'] = $args;
 $request = json_decode($args['body'], true); $mode = $GLOBALS['input']['mode'] ?? 'ok';
 $reply = ['ok'=>true, 'kid'=>$request['kid'], 'token_sha256'=>$request['token_sha256'],
  'nonce'=>$request['nonce'], 'at'=>$request['at'], 'expires_at'=>2000000120];
 switch ($mode) {
 case 'wrong_nonce': $reply['nonce'] = str_repeat('0',32); break;
 case 'wrong_hash': $reply['token_sha256'] = str_repeat('0',64); break;
 case 'wrong_kid': $reply['kid'] = str_repeat('0',12); break;
 case 'old_time': $reply['at'] -= 31; break;
 case 'future_time': $reply['at'] += 31; break;
 case 'wrong_expiry': $reply['expires_at']++; break;
 case 'expired': $reply['expires_at'] = 2000000000; break;
 case 'false_ok': $reply['ok'] = false; break;
 case 'string_at': $reply['at'] = (string)$reply['at']; break;
 case 'string_expiry': $reply['expires_at'] = (string)$reply['expires_at']; break;
 case 'extra': $reply['extra'] = 1; break;
 }
 $reply['mac'] = hash_hmac('sha256', 'sandbox-share-wp-claim-response.v1|' . $reply['kid'] . '|' . $reply['token_sha256'] . '|' . $reply['nonce'] . '|' . $reply['at'] . '|' . $reply['expires_at'], hex2bin('__KEY__'));
 if ($mode === 'wrong_mac') $reply['mac'] = str_repeat('0',64);
 if ($mode === 'unsigned') unset($reply['mac']);
 $body = json_encode($reply);
 if ($mode === 'duplicate') $body = str_replace('{', '{"ok":true,', $body);
 if ($mode === 'oversized') $body .= str_repeat(' ', 2048);
 if ($mode === 'truncated') $body = substr($body, 0, -3);
 if ($mode === 'malformed') $body = '{';
 if ($mode === 'empty') $body = '';
 return ['code'=>$mode === 'redirect' ? 302 : 200, 'body'=>$body, 'error'=>$mode === 'http_error'];
}
function is_wp_error($reply) { return $reply['error']; }
function wp_remote_retrieve_response_code($reply) { return $reply['code']; }
function wp_remote_retrieve_body($reply) { return $reply['body']; }
function get_user_by($field, $user) { $GLOBALS['result']['events'][] = 'user:' . $user; return (object)['ID'=>7]; }
function wp_set_current_user($id) { $GLOBALS['result']['events'][] = 'current:' . $id; }
function wp_set_auth_cookie($id, $remember, $secure) { $GLOBALS['result']['events'][] = 'cookie:' . $id; }
function is_ssl() { return true; }
function wp_safe_redirect($url) { $GLOBALS['result']['events'][] = 'redirect'; }
function admin_url() { return 'https://wp.example.test/wp-admin/'; }
function network_admin_url() { return 'https://wp.example.test/wp-admin/network/'; }
require __DIR__ . '/plugin.php';
($GLOBALS['callback'])();
}
'''
