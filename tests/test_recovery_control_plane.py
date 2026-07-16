import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.control_plane import capture_declarations
from sandbox.recovery.errors import RecoveryError


class TestControlPlane(unittest.TestCase):
    def test_capture_is_allowlisted_and_excludes_credentials_and_runtime_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "cloudflare.json").write_text("declaration")
            (root / ".env").write_text("secret")
            receipt = capture_declarations(root, ("cloudflare.json", ".env"))
            self.assertEqual(receipt["artifacts"][0]["path"], "cloudflare.json")
            self.assertEqual(receipt["excluded"], (".env",))
            self.assertTrue(receipt["cloudflare"])
            with self.assertRaises(RecoveryError): capture_declarations(root, ("../outside",))

    def test_capture_rejects_malformed_declarations_and_non_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valid.json").write_text("declaration")
            (root / "directory").mkdir()
            (root / "link.json").symlink_to(root / "valid.json")
            for declared in (("valid.json", "valid.json"), ("bad\x00name",), ("directory",), ("link.json",), ("../outside",)):
                with self.subTest(declared=declared), self.assertRaises(RecoveryError) as caught:
                    capture_declarations(root, declared)
                self.assertIn(caught.exception.code, {"invalid_control_plane_path", "invalid_control_plane_root"})

    def test_capture_requires_a_real_root_and_tuple_declarations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RecoveryError) as caught:
                capture_declarations(root / "missing", ())
            self.assertEqual(caught.exception.code, "invalid_control_plane_root")
            with self.assertRaises(RecoveryError) as caught:
                capture_declarations(root, ["valid.json"])
            self.assertEqual(caught.exception.code, "invalid_control_plane_path")
