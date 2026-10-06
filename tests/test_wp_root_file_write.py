"""Sandbox-managed WordPress root files survive a host-unwritable root."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sandbox.core import _provision


class WpRootFileWriteTests(unittest.TestCase):
    def test_host_write_is_used_when_the_root_is_writable(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(_provision, "wp_dir", return_value=Path(temp)), \
                mock.patch.object(_provision, "wpcli", side_effect=AssertionError):
            _provision._write_wp_root_file("fixture", ".sandbox-multisite", "marker\n")
            self.assertEqual((Path(temp) / ".sandbox-multisite").read_text(), "marker\n")

    def test_permission_error_falls_back_to_container_write(self):
        root = mock.MagicMock()
        root.__truediv__.return_value.write_text.side_effect = PermissionError
        with mock.patch.object(_provision, "wp_dir", return_value=root), \
                mock.patch.object(_provision, "wpcli",
                                  return_value=SimpleNamespace(returncode=0)) as wpcli:
            _provision._write_wp_root_file("fixture", ".htaccess", "rules\n")
        argv = wpcli.call_args[0][0]
        self.assertEqual(argv[0], "eval")
        self.assertIn("ABSPATH . '.htaccess'", argv[1])

    def test_unwritable_everywhere_raises_typed_error(self):
        root = mock.MagicMock()
        root.__truediv__.return_value.write_text.side_effect = PermissionError
        with mock.patch.object(_provision, "wp_dir", return_value=root), \
                mock.patch.object(_provision, "wpcli",
                                  return_value=SimpleNamespace(returncode=1)), \
                self.assertRaisesRegex(RuntimeError, "wordpress_root_unwritable"):
            _provision._write_wp_root_file("fixture", ".sandbox-multisite", "x")


if __name__ == "__main__":
    unittest.main()
