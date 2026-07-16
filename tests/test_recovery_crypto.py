import tempfile
import unittest
import stat
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

    def test_passphrase_descriptor_handles_partial_writes(self):
        writes = []
        def partial_write(_fd, payload):
            if not writes:
                writes.append(bytes(payload[:1]))
                return 1
            writes.append(bytes(payload))
            return len(payload)
        def fake_run(argv, **kwargs):
            Path(argv[argv.index("--output") + 1]).write_bytes(b"ciphertext")
            return type("Result", (), {"returncode": 0, "stderr": ""})()
        with tempfile.TemporaryDirectory() as directory, \
                patch("sandbox.recovery.crypto.os.write", side_effect=partial_write), \
                patch("sandbox.recovery.crypto.subprocess.run", fake_run):
            source = Path(directory) / "plain"; source.write_bytes(b"payload")
            GpgCrypto("fixture-passphrase").encrypt_file(source, Path(directory) / "cipher")
        self.assertEqual(b"".join(writes), b"fixture-passphrase\n")
        self.assertGreater(len(writes), 1)

    def test_outputs_are_owner_only(self):
        def fake_run(argv, **kwargs):
            Path(argv[argv.index("--output") + 1]).write_bytes(b"ciphertext")
            return type("Result", (), {"returncode": 0, "stderr": ""})()
        with tempfile.TemporaryDirectory() as directory, patch("sandbox.recovery.crypto.subprocess.run", fake_run):
            source = Path(directory) / "plain"; source.write_bytes(b"payload")
            target = Path(directory) / "cipher"
            GpgCrypto("fixture").encrypt_file(source, target)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_preexisting_pending_output_is_not_overwritten(self):
        def fake_run(*args, **kwargs):
            self.fail("gpg must not run with a preexisting pending output")
        with tempfile.TemporaryDirectory() as directory, patch("sandbox.recovery.crypto.subprocess.run", fake_run):
            source = Path(directory) / "plain"; source.write_bytes(b"payload")
            target = Path(directory) / "cipher"; pending = Path(str(target) + ".pending")
            pending.write_bytes(b"keep")
            with self.assertRaisesRegex(RecoveryError, "pending"):
                GpgCrypto("fixture").encrypt_file(source, target)
            self.assertEqual(pending.read_bytes(), b"keep")

    def test_requires_a_nonempty_secret_channel(self):
        with self.assertRaisesRegex(RecoveryError, "passphrase"):
            GpgCrypto("")

    def test_rejects_ambiguous_passphrase_control_text(self):
        with self.assertRaisesRegex(RecoveryError, "control text"):
            GpgCrypto("fixture\npassphrase")

    def test_rejects_non_string_and_unbounded_passphrases(self):
        with self.assertRaises(RecoveryError):
            GpgCrypto(123)
        with self.assertRaisesRegex(RecoveryError, "too long"):
            GpgCrypto("x" * 4097)

    def test_interruption_removes_pending_ciphertext(self):
        with tempfile.TemporaryDirectory() as directory, patch("sandbox.recovery.crypto.subprocess.run", side_effect=OSError("interrupted")):
            source = Path(directory) / "plain"; target = Path(directory) / "cipher"; source.write_bytes(b"payload")
            with self.assertRaises(OSError):
                GpgCrypto("fixture").encrypt_file(source, target)
            self.assertFalse(target.exists())
            self.assertFalse((Path(str(target) + ".pending")).exists())

    def test_verification_rejects_plaintext_mutation_between_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plain"; source.write_bytes(b"payload")
            ciphertext = Path(directory) / "cipher"; ciphertext.write_bytes(b"ciphertext")
            crypto = GpgCrypto("fixture")
            crypto.decrypt_file = lambda _source, target: Path(target).write_bytes(b"payload")
            with patch("sandbox.recovery.crypto.sha256_file",
                       side_effect=("a" * 64, "a" * 64, "b" * 64)):
                with self.assertRaisesRegex(RecoveryError, "changed"):
                    crypto.verify_file(source, ciphertext)

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
