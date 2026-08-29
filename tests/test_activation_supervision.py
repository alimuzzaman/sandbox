import unittest
import plistlib
import tempfile
from pathlib import Path
from unittest import mock


class ActivationSupervisionTests(unittest.TestCase):
    def test_macos_service_path_includes_discovered_docker_directory(self):
        from sandbox.activation import supervision

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(supervision.sys, "platform", "darwin"), \
             mock.patch.object(supervision.Path, "home", return_value=Path(tmp)), \
             mock.patch.object(supervision.shutil, "which",
                               return_value="/fixture/orbstack/bin/docker"):
            result = supervision.install()
            payload = plistlib.loads(Path(result["path"]).read_bytes())
            service_path = payload["EnvironmentVariables"]["PATH"].split(":")
            self.assertEqual(service_path[0], "/fixture/orbstack/bin")
            self.assertIn("/usr/local/bin", service_path)
            self.assertIn("/usr/bin", service_path)

    def test_enable_uses_healthy_authority_as_success_when_transition_is_racy(self):
        from sandbox.activation import supervision

        with mock.patch.object(supervision, "install", return_value={
                "ok": True, "state": "installed", "path": "/tmp/activation.plist",
            }), mock.patch.object(supervision.sys, "platform", "darwin"), \
             mock.patch.object(supervision, "_run", side_effect=[False, False, True]), \
             mock.patch("sandbox.core._domains._activation_gateway_healthy",
                        return_value=True):
            result = supervision.enable()

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "enabled")
        self.assertIn("warning", result)

    def test_enable_stays_failed_when_health_never_recovers(self):
        from sandbox.activation import supervision

        with mock.patch.object(supervision, "install", return_value={
                "ok": True, "state": "installed", "path": "/tmp/activation.plist",
            }), mock.patch.object(supervision.sys, "platform", "darwin"), \
             mock.patch.object(supervision, "_run", return_value=True), \
             mock.patch("sandbox.core._domains._activation_gateway_healthy",
                        return_value=False):
            result = supervision.enable()

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "enable_failed")


if __name__ == "__main__":
    unittest.main()
