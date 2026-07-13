import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.bootstrap import build_bootstrap_plan
from sandbox.recovery.errors import RecoveryError


class TestFreshServerBootstrap(unittest.TestCase):
    def test_requires_empty_target_checkout_and_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); checkout = root / "checkout"; checkout.mkdir(); (checkout / ".git").mkdir()
            plan = build_bootstrap_plan(root / "fresh", checkout=checkout, profiles=("control-plane",), prerequisites=("gpg", "rclone"))
            self.assertTrue(plan["requires_confirmation"])
            populated = root / "populated"; populated.mkdir(); (populated / "old").write_text("x")
            with self.assertRaisesRegex(RecoveryError, "empty"):
                build_bootstrap_plan(populated, checkout=checkout, profiles=("control-plane",), prerequisites=())
