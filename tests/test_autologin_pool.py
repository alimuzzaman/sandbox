"""Exercise the bounded WordPress hash pool with PHP and a small WP API fixture."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from sandbox.hosting import autologin
from tests.subprocess_support import run_test_process, synthetic_environment


@unittest.skipUnless(shutil.which("php"), "PHP CLI is unavailable")
class AutologinPoolTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.plugin = self.root / "plugin.php"
        self.plugin.write_text(autologin.render_plugin())
        self.runner = self.root / "run.php"
        self.runner.write_text('''<?php
const ABSPATH = '/fixture/';
const DB_NAME = 'fixture';
class FakeDB {
    public $base_prefix = 'wp_';
    function prepare($sql, ...$args) { return $sql; }
    function get_var($sql) { return '1'; }
}
$wpdb = new FakeDB();
function get_site_option($key, $default) {
    $path = __DIR__ . '/options.json';
    return file_exists($path) ? json_decode(file_get_contents($path), true) : $default;
}
function update_site_option($key, $value) {
    return file_put_contents(__DIR__ . '/options.json', json_encode($value)) !== false;
}
function add_action($name, $callback, ...$args) { $GLOBALS['action'] = $callback; }
function wp_cache_delete(...$args) {}
function is_multisite() { return false; }
function home_url($path) { return 'https://wp.example.test' . $path; }
function status_header($status) {}
function wp_die($message) { echo 'refused'; exit; }
function wp_unslash($value) { return $value; }
function get_user_by($field, $value) { return (object) array('ID' => 1); }
function wp_set_current_user($id) {}
function wp_set_auth_cookie(...$args) {}
function is_ssl() { return true; }
function admin_url() { return 'https://wp.example.test/wp-admin/'; }
function wp_safe_redirect($url) { echo 'signed-in'; }
require __DIR__ . '/plugin.php';
$input = json_decode($argv[1], true);
if ($input['op'] === 'request') {
    $_GET = array(); $_POST = array('sandbox_autologin' => $input['token']);
    $_SERVER['REQUEST_METHOD'] = 'POST';
    if (isset($input['origin'])) { $_SERVER['HTTP_ORIGIN'] = $input['origin']; }
    if (isset($input['site'])) { $_SERVER['HTTP_SEC_FETCH_SITE'] = $input['site']; }
    ($GLOBALS['action'])();
} elseif ($input['op'] === 'mint') {
    sandbox_host_autologin_register($input['hash'], 'admin', $input['issued'], $input['expires']);
    echo 'minted';
} else {
    echo json_encode(sandbox_host_autologin_consume($input['token']));
}
''')

    def run_php(self, payload):
        result = run_test_process([shutil.which("php"), str(self.runner), json.dumps(payload)],
                                  capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def mint(self, token, issued=None, expires=None):
        now = int(time.time())
        self.run_php({"op": "mint", "hash": hashlib.sha256(token.encode()).hexdigest(),
                      "issued": now if issued is None else issued,
                      "expires": now + 120 if expires is None else expires})

    def consume(self, token):
        return json.loads(self.run_php({"op": "consume", "token": token}))

    def test_two_clicks_work_in_either_order_and_replay_is_refused(self):
        self.mint("first-token")
        self.mint("second-token")
        self.assertEqual(self.consume("second-token"), "admin")
        self.assertEqual(self.consume("first-token"), "admin")
        self.assertFalse(self.consume("first-token"))

    def test_eight_unused_bound_drops_oldest_and_spent_slots_are_reusable(self):
        for i in range(8):
            self.mint(f"token-{i}", issued=int(time.time()) - 10 + i)
        self.assertEqual(self.consume("token-7"), "admin")
        self.mint("new-token")
        self.assertEqual(self.consume("token-0"), "admin")
        self.mint("overflow-one")
        self.mint("overflow-two")
        self.assertFalse(self.consume("token-1"))
        self.assertEqual(self.consume("overflow-two"), "admin")

    def test_expired_pool_entry_cannot_sign_in(self):
        self.mint("expired-token")
        pool = json.loads((self.root / "options.json").read_text())
        pool[hashlib.sha256(b"expired-token").hexdigest()]["expires_at"] = int(time.time()) - 1
        (self.root / "options.json").write_text(json.dumps(pool))
        self.assertFalse(self.consume("expired-token"))

    def test_invalid_token_never_consumes_a_valid_one(self):
        self.mint("valid-token")
        self.assertFalse(self.consume("invalid-token"))
        self.assertEqual(self.consume("valid-token"), "admin")

    def test_foreign_or_missing_origin_cannot_consume_from_own_page(self):
        token = 'a' * 43
        self.mint(token)
        for headers in ({'origin': 'https://foreign.example.test'}, {},
                        {'origin': 'null'}, {'origin': 'https://wp.example.test:444'},
                        {'origin': 'https://wp.example.test', 'site': 'cross-site'}):
            self.assertEqual(self.run_php({'op': 'request', 'token': token, **headers}), 'refused')
        self.assertEqual(self.run_php({'op': 'request', 'token': token,
                                      'origin': 'https://wp.example.test', 'site': 'same-origin'}), 'signed-in')
        self.assertFalse(self.consume(token))


@unittest.skipUnless(shutil.which('php') and shutil.which('node') and
                     os.environ.get('SANDBOX_TEST_PLAYWRIGHT_MODULE'),
                     'set SANDBOX_TEST_PLAYWRIGHT_MODULE to an installed Playwright module for browser fixture proof')
class AutologinBrowserTest(unittest.TestCase):
    """Exercise browser-built Origin headers against the generated PHP handler."""
    setUp = AutologinPoolTest.setUp
    run_php = AutologinPoolTest.run_php
    mint = AutologinPoolTest.mint

    def test_fragment_opener_preserves_origin_and_never_sends_token_in_urls(self):
        token = 'b' * 43
        self.mint(token)
        (self.root / 'plugin-control.php').write_text(autologin.render_plugin().replace(
            "Referrer-Policy: same-origin", "Referrer-Policy: no-referrer"))
        fixture = self.runner.read_text().split('$input = json_decode')[0]
        fixture = fixture.replace("return 'https://wp.example.test' . $path;",
                                  "return 'http://' . $_SERVER['HTTP_HOST'] . $path;")
        fixture = fixture.replace("require __DIR__ . '/plugin.php';",
            "require __DIR__ . (isset($_GET['control']) ? '/plugin-control.php' : '/plugin.php');")
        (self.root / 'serve.php').write_text(fixture + "($GLOBALS['action'])();\n")
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        address = f'http://127.0.0.1:{port}'
        log = (self.root / 'server.log').open('w')
        self.addCleanup(log.close)
        server = subprocess.Popen([shutil.which('php'), '-S', f'127.0.0.1:{port}', str(self.root / 'serve.php')],
                                  cwd=self.root, env=synthetic_environment(), stdout=log, stderr=log)
        def stop():
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        self.addCleanup(stop)
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    self.fail('PHP browser fixture did not start')
                time.sleep(0.05)
        script = self.root / 'browser.cjs'
        script.write_text('''
const {chromium} = require(process.argv[2]);
(async () => {
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage();
  const requests = [], errors = [], responses = [];
  const base = process.argv[3], token = 'b'.repeat(43);
  let stage = 'negative-control';
  try {
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
    page.on('response', r => responses.push({status:r.status(), policy:r.headers()['referrer-policy'],
      csp:r.headers()['content-security-policy']}));
    page.on('request', r => requests.push({method:r.method(), url:r.url(),
      origin:r.headers()['origin'] || null, referer:r.headers()['referer'] || null}));
    await page.goto(base + '/?sandbox_autologin=1&control=1#' + token);
    await page.waitForFunction(() => document.body.textContent === 'refused', null, {timeout:5000});
    stage = 'same-origin';
    await page.goto(base + '/?sandbox_autologin=1#' + token);
    await page.waitForFunction(() => document.body.textContent === 'signed-in', null, {timeout:5000});
    stage = 'replay';
    // A fragment-only navigation would reuse the signed-in document without running the opener.
    await page.goto('about:blank');
    await page.goto(base + '/?sandbox_autologin=1#' + token);
    await page.waitForFunction(() => document.body.textContent === 'refused', null, {timeout:5000});
    console.log(JSON.stringify(requests));
  } catch (error) {
    console.error(JSON.stringify({stage, message:error.message, requests, responses, errors,
      body:await page.locator('body').innerText()}).split(token).join('<fixture-token>'));
    process.exitCode = 1;
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exitCode=1; });
''')
        result = run_test_process([shutil.which('node'), str(script),
                                   os.environ['SANDBOX_TEST_PLAYWRIGHT_MODULE'], address],
                                  timeout=40, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = json.loads(result.stdout)
        posts = [row for row in requests if row['method'] == 'POST']
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts[0]['origin'], 'null')
        self.assertEqual(posts[1]['origin'], address)
        self.assertEqual(posts[2]['origin'], address)
        for row in requests:
            self.assertNotIn(token, row['url'])
            self.assertNotIn(token, row['referer'] or '')
