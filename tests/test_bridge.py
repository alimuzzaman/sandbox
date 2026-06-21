"""Spec 002 snapshot-bridge tests + spec 001 registry-overlay (loop iteration 2).

`_bridge_handle` is the trust boundary for wp-admin snapshot calls, so its auth
and path-traversal guards get focused coverage. Docker-touching paths
(_start_job → cmd_snapshot/restore) are stubbed so these stay pure unit tests.
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core as core  # noqa: E402
import sandbox_core  # noqa: E402

TOK = "secrettoken123"


class TestBridgeHandle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sb-bridge-"))
        # patch the bridge's external touchpoints
        self.p = [
            mock.patch.object(core, "_bridge_token_for", lambda inst: TOK),
            mock.patch.object(core, "_is_herd_instance", lambda inst: False),
            mock.patch.object(core, "snapshots_dir", lambda inst: self.tmp),
            mock.patch.object(core, "load_config", lambda: {}),
            mock.patch.object(core, "_start_job", lambda label, fn: "job-1"),
        ]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mksnap(self, name):
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "db.sql").write_text("-- sql")
        (d / "META").write_text(f"instance=x\nname={name}\n")
        return d

    def call(self, method, sub, body=None, auth=f"Bearer {TOK}"):
        return core._bridge_handle(method, "inst", sub, body or {}, auth)

    # --- auth ---
    def test_no_token_403(self):
        self.assertEqual(self.call("GET", "/snapshots", auth="")[0], 403)

    def test_wrong_token_403(self):
        self.assertEqual(self.call("GET", "/snapshots", auth="Bearer nope")[0], 403)

    def test_unknown_instance_404(self):
        with mock.patch.object(core, "_bridge_token_for", lambda inst: None):
            self.assertEqual(self.call("GET", "/snapshots")[0], 404)

    def test_herd_409(self):
        with mock.patch.object(core, "_is_herd_instance", lambda inst: True):
            self.assertEqual(self.call("GET", "/snapshots")[0], 409)

    # --- list ---
    def test_list_ok(self):
        self._mksnap("t1")
        code, data = self.call("GET", "/snapshots")
        self.assertEqual(code, 200)
        self.assertEqual([s["name"] for s in data["snapshots"]], ["t1"])

    # --- take ---
    def test_take_valid_202(self):
        code, data = self.call("POST", "/snapshot", {"name": "good"})
        self.assertEqual(code, 202)
        self.assertEqual(data["job_id"], "job-1")

    def test_take_invalid_name_400(self):
        for bad in ("..", "../x", ".", "a/b"):
            self.assertEqual(self.call("POST", "/snapshot", {"name": bad})[0], 400, bad)

    # --- restore (traversal is the high-severity finding) ---
    def test_restore_traversal_400(self):
        for bad in ("..", "../../etc", "."):
            self.assertEqual(self.call("POST", "/restore", {"name": bad})[0], 400, bad)

    def test_restore_missing_404(self):
        self.assertEqual(self.call("POST", "/restore", {"name": "ghost"})[0], 404)

    def test_restore_valid_202(self):
        self._mksnap("keep")
        self.assertEqual(self.call("POST", "/restore", {"name": "keep"})[0], 202)

    # --- delete ---
    def test_delete_traversal_400(self):
        self.assertEqual(self.call("DELETE", "/snapshot/../../x")[0], 400)

    def test_delete_missing_404(self):
        self.assertEqual(self.call("DELETE", "/snapshot/ghost")[0], 404)

    def test_delete_ok_removes_dir(self):
        d = self._mksnap("gone")
        self.assertEqual(self.call("DELETE", "/snapshot/gone")[0], 200)
        self.assertFalse(d.exists())

    def test_delete_traversal_does_not_escape(self):
        # A sibling dir outside the snapshots tree must survive a traversal attempt.
        outside = self.tmp.parent / f"victim-{self.tmp.name}"
        outside.mkdir(exist_ok=True)
        try:
            self.call("DELETE", "/snapshot/..%2f" + outside.name)  # encoded slash
            self.assertTrue(outside.exists(), "traversal deleted an outside dir!")
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestRegistryOverlay(unittest.TestCase):
    """Spec 001 #9: a registry instance with no local.yml block resolves to its
    real (registry-cached) ports, not the shared hardcoded defaults."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-overlay-")
        self.old = os.environ.get("SANDBOX_RUNTIME")
        os.environ["SANDBOX_RUNTIME"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("SANDBOX_RUNTIME", None)
        else:
            os.environ["SANDBOX_RUNTIME"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_block_less_instance_uses_registry_ports(self):
        root = str(Path(self.tmp) / "projA")
        sandbox_core.registry_put(root, instance="inst-a", wordpress_port=8201,
                                  db_port=3401, mailpit_port=8231, server="nginx")
        out = core.resolve_instances({"instances": {}})  # no local.yml block
        self.assertIn("inst-a", out)
        self.assertEqual(out["inst-a"]["wordpress_port"], 8201)  # not 8088 default
        self.assertEqual(out["inst-a"]["db_port"], 3401)
        self.assertEqual(out["inst-a"]["server"], "nginx")


if __name__ == "__main__":
    unittest.main(verbosity=2)
