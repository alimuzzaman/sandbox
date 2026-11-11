"""Existing native lock keys/directories contend across actual processes."""
from pathlib import Path
import sys
import tempfile
import unittest

from sandbox.hosting.recovery.repository import RecoveryRepository
from sandbox.hosting.shared_credentials.native_locks import (
    NativeLockError, hold_native_mutations, require_native_mutations,
)
from tests.subprocess_support import run_test_process

ROOT = Path(__file__).resolve().parents[1]


class NativeCredentialLockTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="credential-lock-")))
        self.owner = RecoveryRepository(self.root / "replica/hosts.json", self.root / "replica/locks")
        self.legacy = RecoveryRepository(self.root / "hosts.json", self.root / "hosting/locks")
        self.bindings = ((self.owner, "owner/project/env"), (self.legacy, "alias/project/env"))

    def test_each_actual_native_directory_contends_with_legacy_writer(self):
        code = """import sys
from sandbox.hosting.recovery.repository import RecoveryRepository
r=RecoveryRepository(sys.argv[1],sys.argv[2])
try:
    with r.effect_lock(sys.argv[3],timeout_seconds=0.05):
        print('acquired')
except Exception:
    print('busy')
"""
        with hold_native_mutations(self.bindings, timeout_seconds=5):
            for repository, key in self.bindings:
                result = run_test_process([sys.executable, "-c", code,
                    str(repository.state_path), str(repository.lock_dir), key],
                    env={"HOME": str(self.root), "SANDBOX_HOME": str(self.root)},
                    timeout=3, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "busy")

    def test_exact_nested_set_reuses_hold_and_subset_cannot_authorize(self):
        with hold_native_mutations(self.bindings, timeout_seconds=5):
            with hold_native_mutations(tuple(reversed(self.bindings)), timeout_seconds=1):
                require_native_mutations(self.bindings)
            with self.assertRaises(NativeLockError):
                with hold_native_mutations(self.bindings[:1], timeout_seconds=1):
                    self.fail("partial native set was accepted")
        with self.assertRaises(NativeLockError):
            require_native_mutations(self.bindings)


if __name__ == "__main__":
    unittest.main()
