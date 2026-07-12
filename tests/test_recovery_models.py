import unittest
from dataclasses import FrozenInstanceError

from sandbox.recovery.models import RecoveryProfile


class TestRecoveryModels(unittest.TestCase):
    def test_profile_is_immutable(self):
        profile = RecoveryProfile("fixture", "test", "filesystem", ("root",), ("path",),
                                  "partial", "stable", (), "encrypted", "target", "hash", "test")
        with self.assertRaises(FrozenInstanceError):
            profile.scope = "changed"


if __name__ == "__main__": unittest.main()
