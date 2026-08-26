import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import license as command


class TestLicenseStatusJson(unittest.TestCase):
    def test_status_json_is_redacted(self):
        output = io.StringIO()
        args = SimpleNamespace(action="status", json=True)
        safe_status = {
            "elementor": "set (…7890)",
            "elementor_primary": {
                "instance": "demo",
                "url": "https://demo.tst",
            },
        }
        with patch.object(command, "license_status", return_value=safe_status), \
             contextlib.redirect_stdout(output):
            command.cmd_license({}, args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["command"], "license")
        self.assertEqual(payload["action"], "status")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], safe_status)
        self.assertNotIn("raw", output.getvalue().lower())
        self.assertNotIn("elementor_license_data", output.getvalue())

    def test_json_is_rejected_for_mutations(self):
        args = SimpleNamespace(action="clear", json=True)
        with self.assertRaises(SystemExit) as raised, \
             patch.object(command, "clear_license") as clear:
            command.cmd_license({}, args)

        self.assertEqual(raised.exception.code, 1)
        clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
