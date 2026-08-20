"""Local contract tests for Spec 003 T012 discovery guidance."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import instances_cmd
from sandbox.core import _provision as provision


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "sandbox/assets/abilities/00-sandbox-abilities.php"


class DiscoveryContextSyncTests(unittest.TestCase):
    def test_context_is_not_created_before_payload_provisioning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(provision, "wp_dir", return_value=root / "wp"), \
                    patch.object(provision, "focus_file", return_value=root / ".focus"):
                provision._write_abilities_context("demo")
            self.assertFalse((root / "wp/wp-content/mu-plugins").exists())

    def test_provisioning_writes_the_initial_context_after_the_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            focus = root / ".focus"
            focus.write_text("provisioned-plugin")

            def copytree(_source: Path, destination: Path) -> None:
                destination.mkdir(parents=True)

            with patch.object(provision, "wp_dir", return_value=root / "wp"), \
                    patch.object(provision, "focus_file", return_value=focus), \
                    patch.object(provision.shutil, "copytree", side_effect=copytree):
                provision._write_abilities_muplugin("demo")

            mu = root / "wp/wp-content/mu-plugins"
            self.assertTrue((mu / "00-sandbox-abilities.php").is_file())
            self.assertEqual(
                json.loads((mu / "sandbox-abilities-context.json").read_text()),
                {"focused_plugin": "provisioned-plugin"},
            )
            self.assertEqual(list(mu.glob(".sandbox-abilities-context.*.tmp")), [])

    def test_context_contains_only_a_validated_slug_or_null(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mu = root / "wp/wp-content/mu-plugins"
            mu.mkdir(parents=True)
            (mu / "00-sandbox-abilities.php").write_text("<?php\n")
            focus = root / ".focus"
            with patch.object(provision, "wp_dir", return_value=root / "wp"), \
                    patch.object(provision, "focus_file", return_value=focus):
                focus.write_text("safe-plugin_2")
                provision._write_abilities_context("demo")
                context = mu / "sandbox-abilities-context.json"
                self.assertEqual(json.loads(context.read_text()), {"focused_plugin": "safe-plugin_2"})

                focus.write_text("../../secret?token=value")
                provision._write_abilities_context("demo")
                self.assertEqual(json.loads(context.read_text()), {"focused_plugin": None})

    def test_focus_clear_and_stolen_focus_synchronize_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / ".focus.current"
            other = root / ".focus.other"
            other.write_text("plugin-slug")
            calls: list[str] = []

            def focus_file(instance: str) -> Path:
                return root / f".focus.{instance}"

            args = SimpleNamespace(
                resolved_instance="current", clear=False, slug="plugin-slug", here=False
            )
            with patch.object(instances_cmd, "ROOT", root), \
                    patch.object(instances_cmd, "focus_file", side_effect=focus_file), \
                    patch.object(instances_cmd, "_write_abilities_context", side_effect=calls.append), \
                    patch.object(instances_cmd, "ok"), patch.object(instances_cmd, "info"):
                instances_cmd.cmd_focus({}, args)
                self.assertEqual(current.read_text(), "plugin-slug")
                self.assertFalse(other.exists())
                self.assertEqual(calls, ["other", "current"])

                args.clear = True
                instances_cmd.cmd_focus({}, args)
                self.assertFalse(current.exists())
                self.assertEqual(calls[-1], "current")


class DiscoveryPhpHarnessTests(unittest.TestCase):
    def test_discovery_contract_and_security_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            loader = temp / "00-sandbox-abilities.php"
            loader.write_bytes(LOADER.read_bytes())
            vendor = temp / "sandbox-abilities/vendor"
            vendor.mkdir(parents=True)
            (vendor / "autoload.php").write_text(
                "<?php namespace WP\\MCP\\Core; class McpAdapter { "
                "public static function instance() { return new self(); } }\n"
            )
            context = temp / "sandbox-abilities-context.json"
            context.write_text(json.dumps({"focused_plugin": "demo-plugin"}))
            harness = temp / "harness.php"
            harness.write_text(
                """<?php
define('ABSPATH', __DIR__ . '/wp/');
define('WP_CONTENT_DIR', ABSPATH . 'wp-content');
$GLOBALS['filters'] = [];
$GLOBALS['home_url'] = 'https://user:password@Example.TEST:8443/site/?token=secret#fragment';
$GLOBALS['logged_in'] = true;
$GLOBALS['manage_options'] = true;
class WP_Error {}
function is_wp_error($value) { return $value instanceof WP_Error; }
function add_action($hook, $callback) {}
function add_filter($hook, $callback, $priority = 10, $args = 1) { $GLOBALS['filters'][$hook][] = [$callback, $priority, $args]; }
function apply_filters($hook, $value) {
    if (empty($GLOBALS['filters'][$hook])) { return $value; }
    foreach ($GLOBALS['filters'][$hook] as $entry) { $value = call_user_func($entry[0], $value); }
    return $value;
}
function get_option($key, $default = null) { return '1'; }
function is_user_logged_in() { return $GLOBALS['logged_in']; }
function current_user_can($capability) { return $capability === 'manage_options' && $GLOBALS['manage_options']; }
function wp_register_ability() {}
function wp_has_ability_category() { return false; }
function wp_register_ability_category() {}
function __($value) { return $value; }
function esc_html__($value) { return $value; }
function esc_html($value) { return $value; }
function wp_json_encode($value) { return json_encode($value); }
function wp_mkdir_p($path) { return true; }
function home_url($path = '/') { return $GLOBALS['home_url']; }
function wp_parse_url($url) { return parse_url($url); }
require __DIR__ . '/00-sandbox-abilities.php';
class FakeServer { private $id; public function __construct($id) { $this->id = $id; } public function get_server_id() { return $this->id; } }
class FakeAdapter { public $args = []; public function create_server(...$args) { $this->args = $args; } }
function check($condition, $message) { if (!$condition) { fwrite(STDERR, $message . "\n"); exit(1); } }
$abilities = [['name' => 'sandbox/execute-php', 'label' => 'Execute', 'description' => 'x']];
$base = ['abilities' => $abilities, 'marker' => ['nested' => true]];
$result = sandbox_abilities_enrich_discovery($base, [], 'mcp-adapter-discover-abilities', null, new FakeServer('sandbox'));
check($result['abilities'] === $abilities, 'ability list changed');
check($result['marker'] === $base['marker'], 'existing result changed');
check($result['sandbox_environment'] === [
    'focused_plugin' => 'demo-plugin',
    'instance_url' => 'https://example.test:8443/site/',
    'snapshot_reminder' => 'Before destructive changes, use the supported Sandbox snapshot workflow.',
], 'environment shape mismatch');
check(sandbox_abilities_enrich_discovery($base, [], 'other-tool', null, new FakeServer('sandbox')) === $base, 'other tool changed');
check(sandbox_abilities_enrich_discovery($base, [], 'mcp-adapter-discover-abilities', null, new FakeServer('other')) === $base, 'other server changed');
$malformed = ['abilities' => 'not-an-array'];
check(sandbox_abilities_enrich_discovery($malformed, [], 'mcp-adapter-discover-abilities', null, new FakeServer('sandbox')) === $malformed, 'malformed result changed');
$error = new WP_Error();
check(sandbox_abilities_enrich_discovery($error, [], 'mcp-adapter-discover-abilities', null, new FakeServer('sandbox')) === $error, 'error changed');
$failure = ['success' => false, 'error' => 'denied', 'abilities' => $abilities];
check(sandbox_abilities_enrich_discovery($failure, [], 'mcp-adapter-discover-abilities', null, new FakeServer('sandbox')) === $failure, 'failure envelope changed');
check(!isset($GLOBALS['filters']['mcp_adapter_discover_abilities_capability']), 'global capability hook installed');
check(apply_filters('mcp_adapter_discover_abilities_capability', 'read') === 'read', 'unrelated capability changed');
check(isset($GLOBALS['filters']['mcp_adapter_tool_call_result']), 'result hook absent');
check($GLOBALS['filters']['mcp_adapter_tool_call_result'][0][2] === 5, 'result hook arity mismatch');
$adapter = new FakeAdapter();
sandbox_abilities_register_mcp_server($adapter);
check(count($adapter->args) === 13, 'server argument count mismatch');
check($adapter->args[0] === 'sandbox', 'server id mismatch');
check($adapter->args[10] === [] && $adapter->args[11] === [], 'resources/prompts mismatch');
$transport_permission = $adapter->args[12];
check(is_callable($transport_permission), 'transport callback missing');
check(call_user_func($transport_permission, null) === true, 'administrator denied');
$GLOBALS['manage_options'] = false;
check(call_user_func($transport_permission, null) === false, 'under-privileged user granted');
$GLOBALS['manage_options'] = true; $GLOBALS['logged_in'] = false;
check(call_user_func($transport_permission, null) === false, 'anonymous administrator granted');
check(in_array('mcp-adapter/discover-abilities', sandbox_abilities_mcp_tool_ids(), true), 'discovery tool absent');
echo "ok\n";
"""
            )
            result = subprocess.run(
                ["php", str(harness)], capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(result.stdout, "ok\n")

    def test_hostile_and_malformed_context_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            loader = temp / "00-sandbox-abilities.php"
            loader.write_bytes(LOADER.read_bytes())
            harness = temp / "harness.php"
            harness.write_text(
                """<?php
define('ABSPATH', __DIR__ . '/wp/'); define('WP_CONTENT_DIR', ABSPATH . 'wp-content');
class WP_Error {} function is_wp_error($v) { return $v instanceof WP_Error; }
function add_action($h,$c) {} function add_filter($h,$c,$p=10,$a=1) {} function apply_filters($h,$v) { return $v; }
function get_option($k,$d=null) { return '1'; } function is_user_logged_in() { return true; } function current_user_can($c) { return true; }
function wp_register_ability() {} function wp_has_ability_category() { return false; } function wp_register_ability_category() {}
function __($v) { return $v; } function esc_html__($v) { return $v; } function esc_html($v) { return $v; }
function wp_json_encode($v) { return json_encode($v); } function wp_mkdir_p($p) { return true; }
function home_url($p='/') { return 'javascript://user:pass@host/?token=x'; } function wp_parse_url($u) { return parse_url($u); }
require __DIR__ . '/00-sandbox-abilities.php';
function check($c,$m) { if (!$c) { fwrite(STDERR,$m."\n"); exit(1); } }
file_put_contents(__DIR__ . '/sandbox-abilities-context.json', '{bad json');
check(sandbox_abilities_environment_context() === ['focused_plugin' => null], 'malformed JSON accepted');
file_put_contents(__DIR__ . '/sandbox-abilities-context.json', json_encode(['focused_plugin' => '../../secret', 'token' => 'value']));
check(sandbox_abilities_environment_context() === ['focused_plugin' => null], 'hostile context accepted');
check(sandbox_abilities_safe_instance_url() === null, 'unsafe URL accepted');
echo "ok\n";
"""
            )
            result = subprocess.run(["php", str(harness)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(result.stdout, "ok\n")


if __name__ == "__main__":
    unittest.main()
