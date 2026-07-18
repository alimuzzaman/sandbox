import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ProcessIdentityTests(unittest.TestCase):
    def test_linux_identity_uses_boot_id_pid_start_ticks_and_nonce_hash(self):
        from sandbox.jobs.process import capture_process_identity

        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "sys/kernel/random").mkdir(parents=True)
            (proc / "sys/kernel/random/boot_id").write_text("boot-1\n")
            (proc / "42").mkdir()
            # Field 22 is process start time; comm may contain spaces/parentheses.
            (proc / "42/stat").write_text("42 (test worker) S " + " ".join(["0"] * 18) + " 987 0\n")
            identity = capture_process_identity(42, nonce="launch-secret", proc_root=proc)
        self.assertEqual(identity.host_boot_id, "boot-1")
        self.assertEqual(identity.start_identity, "987")
        self.assertNotEqual(identity.nonce_hash, "launch-secret")

    def test_pid_reuse_and_boot_change_do_not_verify(self):
        from sandbox.jobs.process import ProcessIdentity, verify_process_identity

        expected = ProcessIdentity("boot-a", 42, "100", "hash")
        self.assertTrue(verify_process_identity(expected, expected))
        self.assertFalse(verify_process_identity(expected, ProcessIdentity("boot-a", 42, "101", "hash")))
        self.assertFalse(verify_process_identity(expected, ProcessIdentity("boot-b", 42, "100", "hash")))
        self.assertFalse(verify_process_identity(expected, None))

    def test_process_group_signal_requires_verified_identity(self):
        from sandbox.jobs.process import ProcessIdentity, signal_owned_process_group

        expected = ProcessIdentity("boot", 42, "100", "hash", process_group_id=42)
        with mock.patch("sandbox.jobs.process.capture_process_identity", return_value=None), \
             mock.patch("sandbox.jobs.process.os.killpg") as killpg:
            self.assertFalse(signal_owned_process_group(expected, 15))
            killpg.assert_not_called()
        with mock.patch("sandbox.jobs.process.capture_process_identity", return_value=expected), \
             mock.patch("sandbox.jobs.process.os.killpg") as killpg:
            self.assertTrue(signal_owned_process_group(expected, 15))
            killpg.assert_called_once_with(42, 15)


if __name__ == "__main__":
    unittest.main()
