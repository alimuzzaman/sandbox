import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import shutil

from sandbox.recovery.crypto import GpgCrypto
from sandbox.recovery.errors import RecoveryError


class TestGpgCrypto(unittest.TestCase):
    def test_passphrase_is_not_in_argv_or_process_output(self):
        secret = "fixture-passphrase"
        calls = []
        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            Path(argv[argv.index("--output") + 1]).write_bytes(b"ciphertext")
            return type("Result", (), {"returncode": 0, "stderr": ""})()
        with tempfile.TemporaryDirectory() as directory, patch("sandbox.recovery.crypto.subprocess.run", fake_run):
            source = Path(directory) / "plain"; source.write_bytes(b"payload")
            GpgCrypto(secret).encrypt_file(source, Path(directory) / "cipher")
        argv, kwargs = calls[0]
        self.assertNotIn(secret, argv)
        self.assertNotIn(secret, " ".join(map(str, argv)))
        self.assertIn("--passphrase-fd", argv)

    def test_requires_a_nonempty_secret_channel(self):
        with self.assertRaisesRegex(RecoveryError, "passphrase"):
            GpgCrypto("")

    def test_rejects_ambiguous_passphrase_control_text(self):
        with self.assertRaisesRegex(RecoveryError, "control text"):
            GpgCrypto("fixture\npassphrase")

    def test_interruption_removes_pending_ciphertext(self):
        with tempfile.TemporaryDirectory() as directory, patch("sandbox.recovery.crypto.subprocess.run", side_effect=OSError("interrupted")):
            source = Path(directory) / "plain"; target = Path(directory) / "cipher"; source.write_bytes(b"payload")
            with self.assertRaises(OSError):
                GpgCrypto("fixture").encrypt_file(source, target)
            self.assertFalse(target.exists())
            self.assertFalse((Path(str(target) + ".pending")).exists())

    @unittest.skipUnless(shutil.which("gpg"), "GnuPG is unavailable")
    def test_real_gpg_fixture_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plain"; cipher = Path(directory) / "plain.gpg"; restored = Path(directory) / "restored"
            source.write_bytes(b"fixture payload")
            crypto = GpgCrypto("fixture-only-passphrase")
            crypto.encrypt_file(source, cipher)
            crypto.decrypt_file(cipher, restored)
            self.assertEqual(restored.read_bytes(), source.read_bytes())
            self.assertEqual(crypto.verify_file(source, cipher), __import__("hashlib").sha256(source.read_bytes()).hexdigest())
