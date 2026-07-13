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
