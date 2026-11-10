"""`sb deploy --stack`: link a WordPress site to its stack for the panel (spec 073)."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import sandbox.core._remote as sr
from sandbox.commands import deploy as deploy_cmd
from sandbox.hosting import stack_registry
from tests import test_host_stack as ths

COMMIT = "d" * 40
ENTRY = {"ssh": "deploy@203.0.113.10", "provisioned": True}


class _Runtime:
    def __init__(self, test: unittest.TestCase):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = mock.patch("sandbox.core._paths.RUNTIME_DIR", self.root / "runtime")
        patcher.start()
        test.addCleanup(patcher.stop)

    def put_stack(self, slug="demo", remote="xspeed-dev", **extra):
        with stack_registry.locked() as registry:
            registry.put({"remote": remote, "slug": slug, "project": f"xspeed-hub-{slug}",
                          "environment": "staging", "hostnames": [f"hub-{slug}.example.test"],
                          "ports": {"mailpit": 18142}, "created_at": 1, **extra})


class RefusalTest(unittest.TestCase):
    def setUp(self):
        self.runtime = _Runtime(self)

    def refusal(self, **args):
        return deploy_cmd._stack_link_refusal(SimpleNamespace(**args), "xspeed-dev")

    def test_absent_or_non_string_stack_is_ignored(self):
        self.assertIsNone(self.refusal(expose=True))
        self.assertIsNone(self.refusal(expose=True, stack=None))
        self.assertIsNone(deploy_cmd._stack_link_refusal(mock.MagicMock(), "xspeed-dev"))

    def test_requires_expose(self):
        self.runtime.put_stack()
        self.assertEqual(self.refusal(stack="demo", expose=False)[0], "deploy_stack_requires_expose")

    def test_invalid_slug(self):
        for slug in ("Demo", "-demo", "de mo", "../x", ""):
            self.assertEqual(self.refusal(stack=slug, expose=True)[0], "deploy_stack_invalid", slug)

    def test_unknown_stack_and_other_remote(self):
        self.assertEqual(self.refusal(stack="demo", expose=True)[0], "deploy_stack_unknown")
        self.runtime.put_stack(remote="other")
        refusal = self.refusal(stack="demo", expose=True)
        self.assertEqual(refusal[0], "deploy_stack_unknown")
        self.assertIn("sb host stack list --remote xspeed-dev", refusal[1])

    def test_known_stack_passes(self):
        self.runtime.put_stack()
        self.assertIsNone(self.refusal(stack="demo", expose=True))


class DeployRefusesBeforeEffectsTest(unittest.TestCase):
    def setUp(self):
        self.runtime = _Runtime(self)
        self.project = self.runtime.root / "proj"
        self.project.mkdir()

    def test_unknown_stack_refuses_before_any_remote_call(self):
        args = SimpleNamespace(project_dir=str(self.project), remote="xspeed-dev", json=True, expose=True,
                               ensure=True, stack="demo", domain="wp-demo.example.test", instance=None,
                               include=[], plugin_slug=None, source_ref=None, ref=None, deploy_timeout=None)
        sc = deploy_cmd._core()
        out = io.StringIO()
        with mock.patch.object(sc, "load_project_config", return_value={"root": str(self.project)}), \
                mock.patch.object(sr, "get_remote", return_value=dict(ENTRY)), \
                mock.patch.object(deploy_cmd, "preflight_project_capability", return_value=None), \
                mock.patch.object(deploy_cmd, "_warn_unstaged_composer_vendor", return_value=[]), \
                mock.patch("sandbox.hosting.front_door.require_capability"), \
                mock.patch.object(sr, "ssh_run") as ssh_run, \
                mock.patch.object(sr, "resolve_sandbox_home") as home, \
                mock.patch.object(deploy_cmd, "ExposureAttempt") as attempt, \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            deploy_cmd._cmd_deploy(None, args)
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload["error_code"], "deploy_stack_unknown")
        self.assertFalse(payload["ok"])
        ssh_run.assert_not_called()
        home.assert_not_called()
        attempt.assert_not_called()


class RecordLinkTest(unittest.TestCase):
    def setUp(self):
        self.runtime = _Runtime(self)
        self.runtime.put_stack(sites=[{"hostname": "wp-demo.example.test", "instance": "old",
                                       "label": "default", "commit": None, "recorded_at": 0},
                                      {"hostname": "a-demo.example.test", "instance": "a",
                                       "label": "default", "commit": None, "recorded_at": 0}])

    def record(self, returncode=0, home_error=None):
        commands = []

        def ssh_run(entry, command, timeout=30):
            commands.append(command)
            return subprocess.CompletedProcess(["ssh"], returncode, "", "")
        with mock.patch.object(sr, "ssh_run", side_effect=ssh_run), \
                mock.patch.object(sr, "resolve_sandbox_home", side_effect=home_error,
                                  return_value="/home/deploy/sandbox"):
            outcome = deploy_cmd._record_stack_link(
                ENTRY, "xspeed-dev", "demo", {"instance": "sandbox-wp-demo", "label": "default"},
                "wp-demo.example.test", COMMIT)
        return outcome, commands

    def test_records_on_remote_and_controller(self):
        outcome, commands = self.record()
        self.assertEqual(outcome, {"slug": "demo", "hostname": "wp-demo.example.test", "recorded": True})
        (command,) = commands
        self.assertIn("/home/deploy/sandbox/runtime/stack-sites/demo/wp-demo.example.test.json", command)
        payload = json.loads(base64.b64decode(re.search(r"printf %s '?([A-Za-z0-9+/=]+)", command).group(1)))
        self.assertEqual(payload["instance"], "sandbox-wp-demo")
        self.assertEqual(payload["commit"], COMMIT)
        self.assertEqual(payload["remote"], "xspeed-dev")
        record = stack_registry.read()["stacks"]["xspeed-dev/demo"]
        self.assertEqual([site["hostname"] for site in record["sites"]],
                         ["a-demo.example.test", "wp-demo.example.test"])
        self.assertEqual(record["sites"][1]["instance"], "sandbox-wp-demo")

    def test_remote_failure_is_reported_not_raised(self):
        outcome, _ = self.record(returncode=1)
        self.assertFalse(outcome["recorded"])
        self.assertEqual(outcome["error_code"], "stack_link_RuntimeError")
        record = stack_registry.read()["stacks"]["xspeed-dev/demo"]
        self.assertEqual(record["sites"][1]["instance"], "old")

    def test_unreachable_remote_is_reported(self):
        outcome, commands = self.record(home_error=RuntimeError("ssh down"))
        self.assertFalse(outcome["recorded"])
        self.assertEqual(commands, [])


class StackUpKeepsSitesTest(unittest.TestCase):
    """A re-apply with `sb host stack up` keeps the sites linked to the stack."""

    # Reuse the stack command harness (a real template checkout) without its tests.
    setUp = ths.StackCommandTests.setUp
    args = ths.StackCommandTests.args

    def test_up_again_keeps_linked_sites(self):
        site = {"hostname": "wp-demo.example.test", "instance": "sandbox-wp-demo", "label": "default",
                "commit": COMMIT, "recorded_at": 5}
        with mock.patch.object(ths.host_stack, "_submit", return_value="job-1"):
            ths.host_stack._up(self.args("up", confirm=True))
            with stack_registry.locked() as registry:
                record = registry.get("r", "demo")
                record["sites"] = [site]
                registry.put(record)
            ths.host_stack._up(self.args("up", confirm=True, request_id="second"))
        self.assertEqual(stack_registry.read()["stacks"]["r/demo"]["sites"], [site])

    def test_first_up_has_no_sites_key(self):
        with mock.patch.object(ths.host_stack, "_submit", return_value="job-1"):
            ths.host_stack._up(self.args("up", confirm=True))
        self.assertNotIn("sites", stack_registry.read()["stacks"]["r/demo"])


if __name__ == "__main__":
    unittest.main()
