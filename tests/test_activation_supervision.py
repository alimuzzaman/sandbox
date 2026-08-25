import unittest
from unittest import mock


class ActivationSupervisionTests(unittest.TestCase):
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
